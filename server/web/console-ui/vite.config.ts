import { resolve } from "node:path";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

const rootDir = fileURLToPath(new URL(".", import.meta.url));
const { version } = JSON.parse(readFileSync(resolve(rootDir, "package.json"), "utf8")) as { version: string };

export default defineConfig({
  define: { __ZERO_VERSION__: JSON.stringify(version) },
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": resolve(rootDir, "src"),
      // 0's upstream source, carried verbatim in zero/src (see zero/README.md). Upstream files import
      // their own modules through this alias, so they never resolve to the console's modified copies.
      "@zero": resolve(rootDir, "zero/src"),
    },
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    hmr: { host: "127.0.0.1", ...(process.env.ZERO_DEV_FRONTEND_PORT ? { clientPort: Number(process.env.ZERO_DEV_FRONTEND_PORT) } : {}) },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rolldownOptions: {
      // Two documents: the console, and the new-engagement landing (0's new-thread screen, kept in
      // its own document so its stylesheet stays out of the console). Served same-origin at
      // /zero/home.html by the console's static handler.
      input: { operations: resolve(rootDir, "index.html"), "zero-home": resolve(rootDir, "zero/home.html") },
    },
  },
});
