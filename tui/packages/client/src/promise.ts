/**
 * Promise-based client surface (minimal).
 *
 * The full Promise client previously shipped as a prebuilt tarball generated
 * by httpapi-codegen. Medusa does not distribute npm packages, so we keep only
 * the shared types that the UI layers import.
 */

/** Legacy diff projection shared by the session review UI. */
export type FileDiffInfo = {
  file: string
  patch?: string
  before?: string
  after?: string
  additions: number
  deletions: number
  status?: "added" | "deleted" | "modified"
}
