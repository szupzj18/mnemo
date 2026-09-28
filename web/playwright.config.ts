import { defineConfig, devices } from "@playwright/test"

const port = process.env.E2E_PORT ?? "7899"
const baseURL = `http://127.0.0.1:${port}`

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  // Screenshot baselines are Linux renders (CI and `pnpm e2e:docker`); other
  // platforms still run every functional assertion but skip pixel comparison.
  ignoreSnapshots: process.platform !== "linux",
  snapshotPathTemplate: "{testDir}/__screenshots__/{testFileName}/{arg}{ext}",
  expect: {
    toHaveScreenshot: { maxDiffPixelRatio: 0.01, animations: "disabled", caret: "hide" },
  },
  use: {
    baseURL,
    locale: "zh-CN",
    timezoneId: "UTC",
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 900 },
        // Locally reuse the installed Chrome; CI uses Playwright's bundled Chromium.
        channel: process.env.CI || process.platform === "linux" ? undefined : "chrome",
      },
    },
  ],
  webServer: {
    command: "node e2e/serve.mjs",
    url: `${baseURL}/`,
    env: { E2E_PORT: port },
    reuseExistingServer: false,
    timeout: 60_000,
    stdout: "ignore",
    stderr: "pipe",
  },
})
