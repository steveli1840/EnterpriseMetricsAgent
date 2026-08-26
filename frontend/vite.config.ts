import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: "./src/test-setup.ts",
    globals: true,
  },
  server: {
    proxy: { "/api": "http://localhost:8000", "/health": "http://localhost:8000" },
  },
});
