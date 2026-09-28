import { expect, test, type Page } from "@playwright/test"

// Pixel regression for the main views in both themes. Baselines are Linux
// renders (see playwright.config.ts); refresh them with `pnpm e2e:docker --update`.
// Anything that depends on the clock or the temp HOME path is masked.

const themes = ["light", "dark"] as const

async function setTheme(page: Page, theme: (typeof themes)[number]) {
  await page.addInitScript((t) => localStorage.setItem("mnemo-theme", t), theme)
}

for (const theme of themes) {
  test.describe(`visual (${theme})`, () => {
    test.beforeEach(async ({ page }) => setTheme(page, theme))

    test("dashboard", async ({ page }) => {
      await page.goto("/")
      await expect(page.getByTestId("device-card")).toHaveCount(1)
      await expect(page).toHaveScreenshot(`dashboard-${theme}.png`, {
        // Only clock-dependent values: db path (temp HOME), sync times, "N前同步".
        mask: [page.getByTestId("db-path"), page.getByTestId("last-sync"), page.getByTestId("stat").last(), page.getByTestId("device-ago")],
      })
    })

    test("search results", async ({ page }) => {
      await page.goto("/search/")
      await page.getByLabel("搜索关键词").fill("backoff")
      await page.getByLabel("搜索关键词").press("Enter")
      await expect(page.getByTestId("hit")).toHaveCount(17)
      await expect(page).toHaveScreenshot(`search-${theme}.png`, {
        mask: [page.getByTestId("host-chips"), page.getByTestId("hit-count")],
      })
    })

    test("session transcript", async ({ page }) => {
      await page.goto("/search/")
      await page.getByLabel("搜索关键词").fill("backoff")
      await page.getByLabel("搜索关键词").press("Enter")
      await page.locator('[data-testid="hit"][data-source="claude"]').filter({ hasText: "rg -n" }).click()
      await expect(page.locator("[data-hit]")).toBeVisible()
      await expect(page).toHaveScreenshot(`session-${theme}.png`, {
        mask: [page.getByTitle(/\/home\//), page.locator('[title*="mnemo-e2e"]')],
      })
    })
  })
}
