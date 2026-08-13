import { Config } from "effect"

export function truthy(key: string) {
  const value = process.env[key]?.toLowerCase()
  return value === "true" || value === "1"
}

const copy = process.env["MEDUSA_EXPERIMENTAL_DISABLE_COPY_ON_SELECT"]
const fff = process.env["MEDUSA_DISABLE_FFF"]

function enabledByExperimental(key: string) {
  return process.env[key] === undefined ? truthy("MEDUSA_EXPERIMENTAL") : truthy(key)
}

export const Flag = {
  OTEL_EXPORTER_OTLP_ENDPOINT: process.env["OTEL_EXPORTER_OTLP_ENDPOINT"],
  OTEL_EXPORTER_OTLP_HEADERS: process.env["OTEL_EXPORTER_OTLP_HEADERS"],

  MEDUSA_AUTO_HEAP_SNAPSHOT: truthy("MEDUSA_AUTO_HEAP_SNAPSHOT"),
  MEDUSA_GIT_BASH_PATH: process.env["MEDUSA_GIT_BASH_PATH"],
  MEDUSA_CONFIG: process.env["MEDUSA_CONFIG"],
  MEDUSA_CONFIG_CONTENT: process.env["MEDUSA_CONFIG_CONTENT"],
  MEDUSA_DISABLE_AUTOUPDATE: truthy("MEDUSA_DISABLE_AUTOUPDATE"),
  MEDUSA_ALWAYS_NOTIFY_UPDATE: truthy("MEDUSA_ALWAYS_NOTIFY_UPDATE"),
  MEDUSA_DISABLE_PRUNE: truthy("MEDUSA_DISABLE_PRUNE"),
  MEDUSA_DISABLE_TERMINAL_TITLE: truthy("MEDUSA_DISABLE_TERMINAL_TITLE"),
  MEDUSA_SHOW_TTFD: truthy("MEDUSA_SHOW_TTFD"),
  MEDUSA_DISABLE_AUTOCOMPACT: truthy("MEDUSA_DISABLE_AUTOCOMPACT"),
  MEDUSA_DISABLE_MODELS_FETCH: truthy("MEDUSA_DISABLE_MODELS_FETCH"),
  MEDUSA_DISABLE_MOUSE: truthy("MEDUSA_DISABLE_MOUSE"),
  MEDUSA_FAKE_VCS: process.env["MEDUSA_FAKE_VCS"],
  MEDUSA_SERVER_PASSWORD: process.env["MEDUSA_SERVER_PASSWORD"],
  MEDUSA_SERVER_USERNAME: process.env["MEDUSA_SERVER_USERNAME"],
  MEDUSA_DISABLE_FFF: fff === undefined ? process.platform === "win32" : truthy("MEDUSA_DISABLE_FFF"),

  // Experimental
  MEDUSA_EXPERIMENTAL_FILEWATCHER: Config.boolean("MEDUSA_EXPERIMENTAL_FILEWATCHER").pipe(
    Config.withDefault(false),
  ),
  MEDUSA_EXPERIMENTAL_DISABLE_FILEWATCHER: Config.boolean("MEDUSA_EXPERIMENTAL_DISABLE_FILEWATCHER").pipe(
    Config.withDefault(false),
  ),
  MEDUSA_EXPERIMENTAL_DISABLE_COPY_ON_SELECT:
    copy === undefined ? process.platform === "win32" : truthy("MEDUSA_EXPERIMENTAL_DISABLE_COPY_ON_SELECT"),
  MEDUSA_MODELS_URL: process.env["MEDUSA_MODELS_URL"],
  MEDUSA_MODELS_PATH: process.env["MEDUSA_MODELS_PATH"],
  MEDUSA_DB: process.env["MEDUSA_DB"],

  MEDUSA_WORKSPACE_ID: process.env["MEDUSA_WORKSPACE_ID"],
  MEDUSA_EXPERIMENTAL_WORKSPACES: enabledByExperimental("MEDUSA_EXPERIMENTAL_WORKSPACES"),

  // Evaluated at access time (not module load) because tests, the CLI, and
  // external tooling set these env vars at runtime.
  get MEDUSA_DISABLE_PROJECT_CONFIG() {
    return truthy("MEDUSA_DISABLE_PROJECT_CONFIG")
  },
  get MEDUSA_EXPERIMENTAL_REFERENCES() {
    return enabledByExperimental("MEDUSA_EXPERIMENTAL_REFERENCES")
  },
  get MEDUSA_TUI_CONFIG() {
    return process.env["MEDUSA_TUI_CONFIG"]
  },
  get MEDUSA_CONFIG_DIR() {
    return process.env["MEDUSA_CONFIG_DIR"]
  },
  get MEDUSA_PURE() {
    return truthy("MEDUSA_PURE")
  },
  get MEDUSA_PERMISSION() {
    return process.env["MEDUSA_PERMISSION"]
  },
  get MEDUSA_PLUGIN_META_FILE() {
    return process.env["MEDUSA_PLUGIN_META_FILE"]
  },
  get MEDUSA_CLIENT() {
    return process.env["MEDUSA_CLIENT"] ?? "cli"
  },
}
