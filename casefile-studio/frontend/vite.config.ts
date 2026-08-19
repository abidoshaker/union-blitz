import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // In dev the UI runs on 5173 and talks to the backend on 8760.
    proxy: {
      "/api": { target: "http://127.0.0.1:8760", changeOrigin: true, ws: true },
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
