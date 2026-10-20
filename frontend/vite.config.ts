// Adapted from Build-a-Spec's frontend/vite.config.ts (copy-based reuse).
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The page is built once into the Python package (src/spec_surgeon/web/static)
// and served by the local server from whatever path it chooses, so every asset
// URL is relative ("./assets/…"). In development the page is opened from Vite
// with `?token=…&api=http://127.0.0.1:<port>`; without `api` the dev server
// proxies /api to the mock server (frontend/mock/server.mjs) on 8765.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: "./",
  build: {
    outDir: "../src/spec_surgeon/web/static",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8765",
    },
  },
});
