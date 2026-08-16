import { defineConfig } from "vitest/config";

export default defineConfig({
  server: {
    host: "0.0.0.0",
    port: 5173,
  },
  test: {
    // `e2e/` belongs to Playwright; Vitest must not try to collect those specs.
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    // Pin the API base URL so a developer's local .env / .env.local cannot silently
    // repoint the MSW handlers and fail the suite.
    env: { VITE_API_BASE_URL: "http://localhost:8000" },
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    css: true,
    restoreMocks: true,
  },
});
