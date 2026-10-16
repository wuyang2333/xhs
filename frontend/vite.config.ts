import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    // Bind on all interfaces so another computer on the LAN can use the same
    // frontend and control the backend host's connected Android device.
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        // Android publish may take several minutes while ADB drives the phone.
        // Keep the LAN proxy connection alive until the backend returns the
        // final publish result instead of surfacing a generic network error.
        timeout: 360000,
        proxyTimeout: 360000,
        configure: (proxy) => {
          proxy.on("proxyRes", (proxyRes) => {
            if (proxyRes.headers["content-type"]?.includes("text/event-stream")) {
              proxyRes.headers["cache-control"] = "no-cache";
              proxyRes.headers["x-accel-buffering"] = "no";
            }
          });
        },
      },
    },
  },
});
