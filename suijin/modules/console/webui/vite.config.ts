import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev proxy: `npm run dev` hits the Flask backend on :7800.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:7800", changeOrigin: true },
    },
  },
  build: {
    outDir: "../lib/ui/dist",
    emptyOutDir: true,
    sourcemap: false,
  },
});
