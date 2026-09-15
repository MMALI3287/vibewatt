/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const api = process.env.CCBURN_API ?? "http://127.0.0.1:8777";

export default defineConfig({
  plugins: [react()],
  define: { __APP_VERSION__: JSON.stringify(process.env.npm_package_version ?? "dev") },
  build: { outDir: "../ccburn/static", emptyOutDir: true },
  server: { proxy: { "/api": api } },
  preview: { proxy: { "/api": api } },
  test: { environment: "jsdom", include: ["src/**/*.test.{ts,tsx}"] },
});
