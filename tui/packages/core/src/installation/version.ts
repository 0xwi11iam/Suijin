declare global {
  const MEDUSA_VERSION: string
  const MEDUSA_CHANNEL: string
}

export const InstallationVersion = typeof MEDUSA_VERSION === "string" ? MEDUSA_VERSION : "local"
export const InstallationChannel = typeof MEDUSA_CHANNEL === "string" ? MEDUSA_CHANNEL : "local"
export const InstallationLocal = InstallationChannel === "local"
