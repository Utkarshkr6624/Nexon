#!/usr/bin/env python
"""Structurally validate ``docker-compose.yml`` without running Docker.

Docker is not always available (and cannot build images on every machine), so
the parts of the compose file that silently rot are checked here instead:

* the YAML parses and uses Compose v2 syntax (no ``version:`` key);
* exactly the expected services are defined;
* every ``build.context`` is a real directory and contains the Dockerfile the
  build will look for;
* every host path bind-mounted into a container exists;
* every ``${VAR}`` the file interpolates is documented in ``.env.example``.

Needs PyYAML, which is intentionally *not* a runtime dependency of the project:

    backend/.venv/Scripts/python -m pip install pyyaml
    backend/.venv/Scripts/python scripts/verify_compose.py
    backend/.venv/Scripts/python scripts/verify_compose.py --strict

Exit status is 0 when every check passes, 1 otherwise. ``--strict`` also fails
on undocumented ``.env.example`` entries (see the report it prints).

NOTE: this is a static check. It does not and cannot tell you that
``docker compose up`` works.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import ENV_EXAMPLE_FILE, REPO_ROOT, read_env_file  # noqa: E402

COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
EXPECTED_SERVICES = ("postgres", "backend", "frontend")

#: ${VAR} and ${VAR:-default}. `$$` is Compose's escape for a literal `$`, so a
#: doubled sigil is skipped rather than treated as a variable.
VARIABLE_PATTERN = re.compile(r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-[^}]*)?\}")


class Report:
    """Collects failures and warnings so one run reports everything."""

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.warnings: list[str] = []

    def fail(self, message: str) -> None:
        self.failures.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def render(self) -> None:
        for message in self.warnings:
            print(f"[warn] {message}")
        for message in self.failures:
            print(f"[FAIL] {message}", file=sys.stderr)
        if self.failures:
            print(f"\n{len(self.failures)} check(s) failed.", file=sys.stderr)
        else:
            print("\nAll compose checks passed.")


def load_compose() -> dict:
    """Parse the compose file, exiting with a clear message if that is not possible."""
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        print(
            "[error] PyYAML is required by this script but is not installed.\n"
            "        It is deliberately not a project dependency. Install it with:\n"
            "          backend/.venv/Scripts/python -m pip install pyyaml",
            file=sys.stderr,
        )
        raise SystemExit(1)

    if not COMPOSE_FILE.is_file():
        print(f"[error] {COMPOSE_FILE} does not exist", file=sys.stderr)
        raise SystemExit(1)
    with COMPOSE_FILE.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def check_syntax(compose: dict, report: Report) -> None:
    """Validate the top-level shape and the Compose v2 requirement."""
    if not isinstance(compose, dict):
        report.fail("the compose file does not contain a top-level mapping")
        return
    if "version" in compose:
        report.fail(
            "a `version:` key is present; Compose v2 ignores it and it must be removed"
        )
    if compose.get("name") != "nexus":
        report.fail(f"top-level `name` is {compose.get('name')!r}, expected 'nexus'")
    services = compose.get("services")
    if not isinstance(services, dict):
        report.fail("no `services` mapping was found")
        return
    if set(services) != set(EXPECTED_SERVICES):
        extra = sorted(set(services) - set(EXPECTED_SERVICES))
        missing = sorted(set(EXPECTED_SERVICES) - set(services))
        report.fail(f"unexpected service set; extra={extra} missing={missing}")


def check_builds(services: dict, report: Report) -> None:
    """Each service needs an image or a build; a build needs a real context."""
    for name, service in services.items():
        build = service.get("build")
        image = service.get("image")
        if build is not None and image is not None:
            report.fail(f"service '{name}' sets both `image` and `build`")
        if build is None:
            if not image:
                report.fail(f"service '{name}' has neither `image` nor `build`")
            else:
                print(f"  image          {image}")
            continue

        context = build.get("context") if isinstance(build, dict) else None
        if not context:
            report.fail(f"service '{name}' has no `build.context`")
            continue

        context_path = (REPO_ROOT / context).resolve()
        if not context_path.is_dir():
            report.fail(f"service '{name}': build context '{context}' is not a directory")
            continue

        dockerfile = build.get("dockerfile", "Dockerfile") if isinstance(build, dict) else "Dockerfile"
        dockerfile_path = (context_path / dockerfile).resolve()
        if not dockerfile_path.is_file():
            report.fail(
                f"service '{name}': {context}/{dockerfile} does not exist "
                "(the build would fall back to the context root and fail)"
            )
            continue
        print(f"  build context  {context:<12} -> {context}/{dockerfile}")


def check_host_mounts(services: dict, report: Report) -> None:
    """Every bind mount must name a path that exists in the repository."""
    for name, service in services.items():
        for mount in service.get("volumes", []) or []:
            if not isinstance(mount, str) or ":" not in mount:
                report.fail(f"service '{name}': cannot parse volume entry {mount!r}")
                continue
            source = mount.split(":", 1)[0]
            # Skip named volumes (no path separator) and interpolation.
            if source.startswith("$") or source.startswith("${"):
                continue
            if "/" not in source and "\\" not in source:
                continue  # named volume, e.g. nexus_pgdata:/var/lib/postgresql/data
            source_path = (REPO_ROOT / source).resolve()
            if not source_path.exists():
                report.fail(f"service '{name}': bind mount source '{source}' does not exist")
            else:
                print(f"  host mount     {source}")


def referenced_variables(compose_text: str) -> set[str]:
    """Return every ``${VAR}`` the compose file interpolates.

    Whole-line comments are dropped first: Compose does not interpolate them,
    and a prose ``$VAR`` in a comment is not a variable the operator can set.
    ``$$`` is Compose's escape for a literal ``$`` and is unwound first so it
    never looks like the start of a reference.
    """
    body = "\n".join(
        line for line in compose_text.splitlines() if not line.lstrip().startswith("#")
    )
    return set(VARIABLE_PATTERN.findall(body.replace("$$", "")))


def check_environment_variables(compose_text: str, report: Report, *, strict: bool) -> None:
    """Cross-check the compose variables against ``.env.example``."""
    documented = set(read_env_file(ENV_EXAMPLE_FILE))
    used = referenced_variables(compose_text)

    undocumented = sorted(used - documented)
    unused = sorted(documented - used)

    for name in undocumented:
        report.fail(
            f"${{{name}}} is used by docker-compose.yml but is not documented in .env.example"
        )
    print(f"  compose vars   {len(used)} referenced, all documented in .env.example"
          if not undocumented else f"  compose vars   {len(used)} referenced")

    if strict and unused:
        report.warn(
            "in .env.example but never used by compose: " + ", ".join(unused)
        )
    elif unused:
        print(f"  unused in compose (read by the app or the scripts): {', '.join(unused)}")


def main() -> int:
    """Run every check and report the outcome."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--strict",
        action="store_true",
        help="also report .env.example entries that docker-compose.yml never uses",
    )
    args = parser.parse_args()

    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    compose = load_compose()
    report = Report()

    print(f"docker-compose.yml ({COMPOSE_FILE})")
    check_syntax(compose, report)
    services = compose.get("services", {}) if isinstance(compose, dict) else {}
    print(f"  services       {', '.join(services)}")

    if services:
        check_builds(services, report)
        check_host_mounts(services, report)
    check_environment_variables(compose_text, report, strict=args.strict)

    report.render()
    return 1 if report.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
