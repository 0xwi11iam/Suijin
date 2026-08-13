import path from "path"

process.env.MEDUSA_DB = ":memory:"
process.env.MEDUSA_MODELS_PATH = path.join(import.meta.dir, "plugin", "fixtures", "models-dev.json")
process.env.MEDUSA_DISABLE_MODELS_FETCH = "true"
