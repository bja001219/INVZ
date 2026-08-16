import { defineConfig, devices } from "@playwright/test";

// Minimal ambient declaration so this config reads one env var without pulling @types/node
// into a suite that otherwise needs no Node typings.
declare const process: { env: Record<string, string | undefined> };

// The suite runs against an already-running Mock stack. Start it with
// `docker compose up --build -d` from the repository root, or with the manual
// backend/frontend commands documented in the README. Point E2E_BASE_URL somewhere else
// when the default ports are taken.
const baseURL = process.env.E2E_BASE_URL ?? "http://localhost:5173";

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
    baseURL,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
