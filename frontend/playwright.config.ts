import { defineConfig, devices } from "@playwright/test";

// The suite runs against an already-running Mock stack. Start it with
// `docker compose up --build -d` from the repository root, or with the manual
// backend/frontend commands documented in the README.
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  // Cut 5 of the sequence player assertion waits on six real five-second videos.
  timeout: 240_000,
  expect: { timeout: 30_000 },
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:5173",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
