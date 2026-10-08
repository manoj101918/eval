import { defineConfig, devices } from "@playwright/test";
import path from "node:path";

// End-to-end: the real web app against the demo backend (real FastAPI + grading pipeline,
// fake AI answers, its own throwaway database under data/e2e).
const ROOT = path.resolve(__dirname, "..");
const PYTHON = process.platform === "win32"
  ? path.join(ROOT, ".venv", "Scripts", "python.exe")
  : path.join(ROOT, ".venv", "bin", "python");
const API_PORT = 8765;
const WEB_PORT = 3100;

export default defineConfig({
  testDir: "e2e",
  timeout: 120_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: `http://localhost:${WEB_PORT}`  /* Next dev serves its scripts to localhost */,
    trace: "retain-on-failure",
    acceptDownloads: true,
  },
  // Uses the installed Google Chrome by default: Windows Application Control may block the
  // browser Playwright downloads. Set PW_CHANNEL=chromium to use the bundled one instead.
  projects: [{
    name: "chrome",
    use: {
      ...devices["Desktop Chrome"],
      channel: process.env.PW_CHANNEL === "chromium" ? undefined : (process.env.PW_CHANNEL ?? "chrome"),
    },
  }],
  webServer: [
    {
      command: `"${PYTHON}" -m backend.app.demo --yes-demo --reset --root data/e2e --port ${API_PORT}`,
      cwd: ROOT,
      url: `http://127.0.0.1:${API_PORT}/docs`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `npx next dev --port ${WEB_PORT}`,
      url: `http://localhost:${WEB_PORT}/login`,
      env: { API_URL: `http://127.0.0.1:${API_PORT}` },
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
