import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Proxy /api to FastAPI in development so the browser sees a single origin
// (no CORS configuration needed on the backend).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET ?? "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
