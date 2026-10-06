import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The FastAPI backend runs on :8000; proxy its API and media so the app is same-origin.
const backend = "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5180,
    strictPort: true,
    proxy: {
      "/state": backend,
      "/demo": backend,
      "/packages": backend,
      "/simulate-event": backend,
      "/media": backend,
    },
  },
});
