// Minimal ambient declaration — the vendored/source trees don't ship
// @types/cytoscape and the app only uses the core API (nodes/edges/style).
declare module "cytoscape" {
  const cytoscape: any;
  export default cytoscape;
}
