# NEXUS frontend — local-first dev image.
#
# Runs the Vite dev server (not a static build) so the container behaves like a
# local `npm run dev`: hot reload, the `/api` proxy to the backend, and the
# relaxed settings a local-first toolchain expects. For a production bundle,
# build the image with a separate `vite build` + static-serve stage.
FROM node:22-alpine

WORKDIR /app

# `npm ci` needs the manifests only, so dependency installs stay cached when
# application source changes.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 5173

# Vite's dev server has no dedicated health endpoint; the index document is
# served as soon as the server is listening.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD wget --quiet --spider --tries=1 http://127.0.0.1:5173/ || exit 1

# `host: true` in vite.config.ts already binds 0.0.0.0; the flags are repeated
# so the image keeps working if that config changes.
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173", "--strictPort"]
