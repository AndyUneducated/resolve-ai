import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for the M8 demo recorder.
 *
 * `npm run demo:record` drives the /chat UI through the demo scenarios and the
 * generated metrics/trace artifacts, recording a real video (webm) into
 * `demo/output/`. Point DEMO_BASE_URL at a running `make dev` stack (ideally
 * with the API started under LLM_BACKEND=fake for instant, deterministic
 * responses).
 */
export default defineConfig({
  testDir: "./demo",
  outputDir: "./demo/output",
  timeout: 180_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.DEMO_BASE_URL ?? "http://localhost:3000",
    headless: true,
    viewport: { width: 1280, height: 800 },
    video: { mode: "on", size: { width: 1280, height: 800 } },
    actionTimeout: 30_000,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
