#!/usr/bin/env bash
# =============================================================================
# NEXUS — local development launcher.
# =============================================================================
# Starts the backend and the frontend together against an already-running
# PostgreSQL, and stops both on Ctrl-C.
#
#   ./scripts/dev.sh              # backend + frontend
#   ./scripts/dev.sh backend      # API only
#   ./scripts/dev.sh frontend     # UI only
#
# On Windows run this from Git Bash or WSL, e.g.
#   bash scripts/dev.sh
# It does not use `docker compose`: the container stack is started separately
# with `docker compose up`, and running both at once would fight over :8000.
#
# macOS ships bash 3.2, so nothing newer than that is used here.
# =============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
FRONTEND_DIR="$REPO_ROOT/frontend"
VENV_DIR="$BACKEND_DIR/.venv"

log()  { printf '\033[36m[dev]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[dev]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m[dev]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'USAGE'
Usage: scripts/dev.sh [both|backend|frontend]

Starts the NEXUS dev servers against an already-running PostgreSQL and stops
them again on Ctrl-C.

  both       backend and frontend (default)
  backend    FastAPI only, on http://localhost:8000
  frontend   Vite dev server only, on http://localhost:5173

On Windows run this from Git Bash or WSL:  bash scripts/dev.sh
USAGE
}

# --- platform helpers --------------------------------------------------------

is_windows() {
  case "$(uname -s 2>/dev/null)" in
    MINGW*|MSYS*|CYGWIN*) return 0 ;;
    *) return 1 ;;
  esac
}

# Map an MSYS process id to the native Windows pid (`ps -W` columns are
# PID PPID PGID WINPID). taskkill only understands the native one.
win_pid() {
  ps -W 2>/dev/null | awk -v want="$1" '$1 == want { print $4; exit }'
}

# Direct children of `pid`. `ps -A` is needed because plain `ps` only reports
# the calling user's own processes on some systems.
child_pids() {
  ps -A -o pid=,ppid= 2>/dev/null | awk -v parent="$1" '$2 == parent { print $1 }'
}

# Signal a process and every descendant, children before parents. `npm run dev`
# forks `sh -c vite` -> `node .../vite`, and a TERM aimed at npm alone never
# reaches that grandchild, which then goes on holding the Vite port and makes
# the next `npm run dev` fail on strictPort. Walking the descendants is done
# instead of signalling a process group because putting each server in its own
# group needs setsid, which macOS does not ship.
kill_tree_posix() {
  local pid="$1" sig="${2:-TERM}" child
  for child in $(child_pids "$pid"); do
    kill_tree_posix "$child" "$sig"
  done
  kill "-$sig" "$pid" 2>/dev/null
}

# Kill a process and everything it spawned. On Windows `npm run dev` leaves
# node.exe and esbuild.exe as grandchildren of the shell, and a plain `kill`
# reaches neither, so that branch uses taskkill's tree mode; POSIX walks the
# process tree instead.
kill_tree() {
  local pid="$1" native
  if is_windows; then
    native="$(win_pid "$pid")"
    if [ -n "$native" ]; then
      # `//` keeps MSYS from rewriting the switches into paths.
      taskkill //F //T //PID "$native" >/dev/null 2>&1
    fi
    kill -TERM "$pid" >/dev/null 2>&1
  else
    kill_tree_posix "$pid" TERM
  fi
}

# --- interpreter discovery ---------------------------------------------------

# Windows virtualenvs use Scripts/, POSIX ones bin/. Check the Windows path
# first: a POSIX system never has it, and the reverse is not true.
find_python() {
  if [ -x "$VENV_DIR/Scripts/python.exe" ]; then
    printf '%s' "$VENV_DIR/Scripts/python.exe"
  elif [ -x "$VENV_DIR/bin/python" ]; then
    printf '%s' "$VENV_DIR/bin/python"
  else
    printf '%s' ""
  fi
}

# --- process management ------------------------------------------------------

PIDS=()
NAMES=()

start() {
  local name="$1" dir="$2"
  shift 2
  log "starting $name in ${dir#"$REPO_ROOT"/}"
  (
    cd "$dir" || exit 127
    exec "$@"
  ) &
  PIDS+=("$!")
  NAMES+=("$name")
  log "$name pid $!"
}

shutdown() {
  trap - INT TERM EXIT
  if [ "${#PIDS[@]}" -eq 0 ]; then
    return
  fi
  log "stopping ${NAMES[*]}"
  local i
  for i in "${!PIDS[@]}"; do
    kill_tree "${PIDS[$i]}"
  done
  for i in "${!PIDS[@]}"; do
    wait "${PIDS[$i]}" 2>/dev/null
  done
  log "stopped"
}

on_signal() {
  printf '\n'
  shutdown
  exit 130
}

# --- argument parsing --------------------------------------------------------

TARGET="${1:-both}"
case "$TARGET" in
  -h|--help|help) usage; exit 0 ;;
  both|backend|frontend) ;;
  *) die "unknown target '$TARGET' (expected: both | backend | frontend)" ;;
esac

command -v node >/dev/null 2>&1 || die "node is not on PATH — install Node 20+ first"

PYTHON="$(find_python)"
if [ -z "$PYTHON" ]; then
  die "no virtualenv at ${VENV_DIR#"$REPO_ROOT"/}.
       Run:  python scripts/bootstrap.py"
fi
[ -f "$BACKEND_DIR/run.py" ] || die "backend/run.py is missing — is this a full checkout?"

# Best effort only: a database that is not up yet must not stop the servers,
# they log their own probe result. The venv is required for psycopg.
if [ -f "$SCRIPT_DIR/wait_for_db.py" ]; then
  "$PYTHON" "$SCRIPT_DIR/wait_for_db.py" --timeout 15 --quiet \
    || warn "database is not reachable yet — the servers still start, check their logs"
fi

trap on_signal INT TERM
trap shutdown EXIT

# The API is started with `python run.py`, never with a bare
# `uvicorn app.main:app`: run.py installs the event loop psycopg needs
# (see backend/app/core/event_loop.py), and on Windows the default Proactor
# loop cannot drive psycopg's async driver at all.
if [ "$TARGET" = both ] || [ "$TARGET" = backend ]; then
  start backend "$BACKEND_DIR" "$PYTHON" run.py
fi

if [ "$TARGET" = both ] || [ "$TARGET" = frontend ]; then
  start frontend "$FRONTEND_DIR" npm run dev
fi

if [ "$TARGET" = both ]; then
  log "backend  http://localhost:${NEXUS_PORT:-8000}/health  (docs at /docs)"
  log "frontend http://localhost:5173"
  log "press Ctrl-C to stop both"
fi

# Poll instead of `wait -n`, which bash 3.2 does not have. If either server
# dies, take the other one down too rather than leaving a half-running stack.
while :; do
  for i in "${!PIDS[@]}"; do
    if ! kill -0 "${PIDS[$i]}" 2>/dev/null; then
      wait "${PIDS[$i]}" 2>/dev/null
      status=$?
      warn "${NAMES[$i]} exited (status $status); stopping the other service"
      exit 1
    fi
  done
  sleep 1
done
