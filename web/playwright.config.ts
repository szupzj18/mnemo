import { defineConfig, devices } from "@playwright/test"

const port = process.env.E2E_PORT ?? "7899"
const baseURL = `http://127.0.0.1:${port}`
// A second dashboard whose device has neighbors (see e2e/serve.mjs --mesh).
const meshPort = String(Number(port) - 1)
const meshURL = `http://127.0.0.1:${meshPort}`
// The website's static export under /mnemo/, for the phone-width suite (see e2e/serve-site.mjs).
const sitePort = String(Number(port) - 3)
const siteURL = `http://127.0.0.1:${sitePort}`

const browser = {
  ...devices["Desktop Chrome"],
  viewport: { width: 1440, height: 900 },
  // Locally reuse the installed Chrome; CI uses Playwright's bundled Chromium.
  channel: process.env.CI || process.platform === "linux" ? undefined : "chrome",
}

const server = {
  reuseExistingServer: false,
  timeout: 90_000,
  stdout: "ignore" as const,
  stderr: "pipe" as const,
}

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
    // Renders in the pinned Linux image are pixel-stable, so keep the budget tight:
    // a 1% ratio (~13k px) let a whole new header icon through unnoticed.
    toHaveScreenshot: { maxDiffPixels: 100, animations: "disabled", caret: "hide" },
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
    { name: "chromium", testIgnore: /(mesh|mobile)\.spec\.ts/, use: { ...browser } },
    { name: "mesh", testMatch: /mesh\.spec\.ts/, use: { ...browser, baseURL: meshURL } },
    // A 390x844 touch phone on Chromium; the assertions measure layout, no pixel baselines.
    { name: "mobile", testMatch: /mobile\.spec\.ts/, use: { ...devices["Pixel 7"], viewport: { width: 390, height: 844 }, channel: browser.channel, baseURL: siteURL } },
  ],
  webServer: [
    { ...server, command: "node e2e/serve.mjs", url: `${baseURL}/`, env: { E2E_PORT: port } },
    { ...server, command: "node e2e/serve.mjs --mesh", url: `${meshURL}/`, env: { E2E_PORT: meshPort } },
    // Builds site/out first when it is missing or stale, which takes a while on a cold checkout.
    { ...server, timeout: 300_000, command: "node e2e/serve-site.mjs", url: `${siteURL}/mnemo/`, env: { E2E_SITE_PORT: sitePort } },
  ],
})
