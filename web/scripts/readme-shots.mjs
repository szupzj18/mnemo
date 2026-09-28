// Capture the README/docs screenshots (docs/assets/*.png) from a running
// dashboard over demo data. Usage (from web/):
//   python3 ../scripts/make-demo-home.py /Users/Shared/alex
//   HOME=/Users/Shared/alex ../bin/mnemo index
//   HOME=/Users/Shared/alex ../bin/mnemo dashboard --no-open --port 7797 &
//   node scripts/readme-shots.mjs http://127.0.0.1:7797 ../docs/assets
// A neutral HOME path matters: it is visible in the session header.
import { chromium } from "@playwright/test"

const [base = "http://127.0.0.1:7797", out = "../docs/assets"] = process.argv.slice(2)
const browser = await chromium.launch({ channel: process.env.CI ? undefined : "chrome" })

for (const theme of ["light", "dark"]) {
  const sfx = theme === "dark" ? "-dark" : ""
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2, locale: "zh-CN" })
  await ctx.addInitScript((t) => localStorage.setItem("mnemo-theme", t), theme)
  const page = await ctx.newPage()

  await page.goto(base + "/")
  await page.getByTestId("device-card").first().waitFor()
  await page.screenshot({ path: `${out}/dashboard${sfx}.png` })

  await page.goto(base + "/search/")
  await page.getByLabel("搜索关键词").fill("backoff")
  await page.getByLabel("搜索关键词").press("Enter")
  await page.getByTestId("hit").first().waitFor()
  await page.mouse.move(0, 0)
  await page.screenshot({ path: `${out}/search${sfx}.png` })

  await page.locator('[data-testid="hit"][data-source="claude"]').filter({ hasText: "rg -n" }).click()
  await page.locator("[data-hit]").waitFor()
  await page.waitForTimeout(400)
  await page.screenshot({ path: `${out}/session${sfx}.png` })
  await ctx.close()
}
await browser.close()
console.log("saved screenshots to", out)
