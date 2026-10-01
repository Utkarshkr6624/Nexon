/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL of the backend API including the version prefix, e.g. `/api/v1`. */
  readonly VITE_API_BASE_URL?: string
  /** Absolute base URL of the backend, used to link to the OpenAPI docs. */
  readonly VITE_API_SERVER_URL?: string
  readonly VITE_APP_NAME?: string
  readonly VITE_ENABLE_COMMAND_PALETTE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
