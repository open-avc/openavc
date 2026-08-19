import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Component tests run in jsdom via vitest. Kept separate from vite.config.ts so
// the production build (tsc -b + vite build) never pulls in the test tooling.
// Mirrors openavc/web/programmer's setup so both UIs are tested the same way.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
