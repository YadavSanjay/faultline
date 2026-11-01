import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// The dev server proxies /api to the BFF (FastAPI on :8088). Override the target with
// VITE_BFF_TARGET if the BFF runs elsewhere.
const BFF_TARGET = process.env.VITE_BFF_TARGET ?? "http://127.0.0.1:8088";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  build: {
    rollupOptions: {
      output: {
        // Split the heavy charting lib into its own chunk so the main bundle stays lean
        // and echarts caches independently across deploys.
        manualChunks: {
          echarts: ["echarts"],
          vendor: ["react", "react-dom", "react-router-dom", "@tanstack/react-query"],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: BFF_TARGET, changeOrigin: true },
    },
  },
});
