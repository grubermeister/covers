import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import path from "path";
import { readFileSync } from "fs";
// (removed component tagger)

// Single source of truth for the app version is the repo-root VERSION file
// (shared with pyproject.toml, see [tool.hatch.version] there). Bump that
// file to release a new version; this just reads it in at build time.
const appVersion = readFileSync(new URL("../VERSION", import.meta.url), "utf-8").trim();

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => ({
  define: {
    __APP_VERSION__: JSON.stringify(appVersion),
  },
  build: {
    sourcemap: true, // unminify errors in devtools (file:line instead of index-xxx.js)
    chunkSizeWarningLimit: 768,
    rollupOptions: {
      output: {
        // Split the 900+ kB vendor monolith into long-lived cacheable
        // chunks. Anything not matched falls through into the per-page
        // chunks rollup already produces.
        manualChunks: (id) => {
          if (!id.includes("node_modules")) return;
          if (/[\\/]node_modules[\\/](react|react-dom|react-router|react-router-dom)[\\/]/.test(id)) return "react";
          if (/[\\/]node_modules[\\/]@radix-ui[\\/]/.test(id)) return "radix";
          if (/[\\/]node_modules[\\/]recharts[\\/]/.test(id)) return "charts";
          if (/[\\/]node_modules[\\/](react-hook-form|@hookform[\\/]resolvers|formik|zod)[\\/]/.test(id)) return "forms";
        },
      },
    },
  },
  server: {
    host: "::",
    port: 8080,
    // Proxy backend URLs to Django so /api, /admin, /accounts work when using npm run dev
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/api-auth": "http://127.0.0.1:8000",
      "/admin": "http://127.0.0.1:8000",
      "/accounts": "http://127.0.0.1:8000",
      "/media": "http://127.0.0.1:8000",
      "/static": "http://127.0.0.1:8000",
    },
  },
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
}));
