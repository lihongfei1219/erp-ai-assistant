import { defineConfig } from "vite";

export default defineConfig({
  esbuild: { jsx: "automatic" },
  server: {
    host: "127.0.0.1",
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/assets/analytics-plotly.js": "http://127.0.0.1:8000",
    },
  },
});
