import { expect, test, type Page } from "@playwright/test"

// Runs against `mnemo dashboard` over scripts/make-demo-home.py data:
// 5 sessions (claude 2, codex 2, pi 1), 45 messages, no remotes.

const pageErrors = new WeakMap<Page, string[]>()

test.beforeEach(async ({ page }) => {
  const errors: string[] = []
  pageErrors.set(page, errors)
  page.on("pageerror", (e) => errors.push(e.message))
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text())
  })
})

test.afterEach(async ({ page }) => {
  expect(pageErrors.get(page), "no page or console errors").toEqual([])
})

async function search(page: Page, query: string) {
  await page.goto("/search/")
  await page.getByLabel("搜索关键词").fill(query)
  await page.getByLabel("搜索关键词").press("Enter")
  await expect(page.getByTestId("hit-count")).toContainText("用时")
}

test.describe("dashboard", () => {
  test("shows index stats and the local device", async ({ page }) => {
    await page.goto("/")
    await expect(page.getByTestId("page-title")).toHaveText("仪表盘")
    await expect(page.getByTestId("stat").filter({ hasText: "索引会话" })).toContainText("5")
    await expect(page.getByTestId("stat").filter({ hasText: "索引消息" })).toContainText("45")
    const local = page.locator('[data-testid="device-card"][data-device="local"]')
    await expect(local).toHaveAttribute("data-state", "ok")
    await expect(local).toContainText("claude 2 · codex 2 · pi 1")
    await expect(page.getByText("还没有远程设备")).toBeVisible()
  })

  test("sidebar navigation reaches every view", async ({ page }) => {
    await page.goto("/")
    for (const [label, title] of [
      ["设备管理", "设备管理"],
      ["会话搜索", "会话搜索"],
      ["日志查看", "日志查看"],
      ["仪表盘", "仪表盘"],
    ]) {
      await page.getByRole("link", { name: label }).click()
      await expect(page.getByTestId("page-title")).toHaveText(title)
    }
  })
})

test.describe("search", () => {
  test("finds hits across all three agents with official icons", async ({ page }) => {
    await search(page, "backoff")
    const hits = page.getByTestId("hit")
    await expect(hits).toHaveCount(17)
    await expect(page.getByTestId("hit-count")).toContainText("17 条")
    await expect(page.getByTestId("host-chips")).toContainText("local")
    await expect(hits.first().locator("mark").first()).toHaveText(/backoff/i)
    for (const src of ["claude", "codex", "pi"]) {
      await expect(page.locator(`[data-testid="hit"][data-source="${src}"]`).first()).toBeVisible()
    }
  })

  test("CJK and multi-term queries", async ({ page }) => {
    await search(page, "retry budget")
    await expect(page.getByTestId("hit").first()).toContainText(/budget/i)
    await search(page, "不存在的词组")
    await expect(page.getByText("没有匹配结果。")).toBeVisible()
  })

  test("shows progress while searching and ignores repeat submits", async ({ page }) => {
    let calls = 0
    await page.route("**/api/search", async (route) => {
      calls++
      await new Promise((r) => setTimeout(r, 1200))
      await route.continue()
    })
    await page.goto("/search/")
    const input = page.getByLabel("搜索关键词")
    await input.fill("backoff")
    await input.press("Enter")
    await input.press("Enter")
    await input.press("Enter")
    await expect(page.getByTestId("search-pending")).toBeVisible()
    await expect(page.getByTestId("search-submit")).toBeDisabled()
    await expect(page.getByTestId("search-submit")).toHaveText(/搜索中/)
    await expect(page.getByTestId("hit-count")).toContainText("已用")
    await expect(page.getByTestId("hit")).toHaveCount(17)
    await expect(page.getByTestId("search-submit")).toBeEnabled()
    expect(calls).toBe(1)
  })

  test("a failed request restores the form and explains why", async ({ page }) => {
    await page.route("**/api/search", (route) => route.abort("failed"))
    await page.goto("/search/")
    await page.getByLabel("搜索关键词").fill("backoff")
    await page.getByTestId("search-submit").click()
    await expect(page.getByText(/^搜索失败：/)).toBeVisible()
    await expect(page.getByTestId("search-submit")).toBeEnabled()
    pageErrors.get(page)!.length = 0 // the aborted fetch logs a console error by design
  })
})

test.describe("session", () => {
  async function openDeliverySession(page: Page) {
    await search(page, "backoff")
    await page.getByTestId("hit").filter({ hasText: "relay/delivery.py" }).filter({ hasText: "rg -n" }).click()
    await expect(page).toHaveURL(/\/session\/\?path=.*line=4/)
    await expect(page.getByTestId("transcript")).toBeVisible()
  }

  test("renders the transcript with timeline, durations and matches", async ({ page }) => {
    await openDeliverySession(page)
    await expect(page.getByTestId("page-title")).toHaveText("会话全文")
    await expect(page.getByTestId("session-meta")).toContainText("14 条消息")
    await expect(page.getByTestId("idle-gap")).toContainText("空闲 2分钟")
    await expect(page.getByTestId("tool-duration").first()).toHaveText("3.0s")
    const hit = page.locator("[data-hit]")
    await expect(hit).toHaveCount(1)
    await expect(hit).toContainText("命中 L4")
    await expect(page.getByTestId("match-prev")).toContainText("1/6")
    await page.getByTestId("match-next").click()
    await expect(page.getByTestId("match-prev")).toContainText("2/6")
    await page.getByTestId("match-prev").click()
    await page.getByTestId("match-prev").click()
    await expect(page.getByTestId("match-prev")).toContainText("6/6")
  })

  test("disclosures toggle and raw mode switches source", async ({ page }) => {
    await openDeliverySession(page)
    const firstTool = page.getByTestId("disclosure").first()
    await firstTool.click()
    await expect(page.getByText("原始全文")).toHaveCount(0)
    await page.getByRole("button", { name: "读取全文" }).click()
    await expect(page.getByText("原始全文")).toBeVisible()
    await page.getByRole("button", { name: "返回索引版" }).click()
    await expect(page.getByText("索引版 · 单条≤20k")).toBeVisible()
  })

  test("back returns to search with results preserved", async ({ page }) => {
    await openDeliverySession(page)
    await page.getByRole("button", { name: "返回" }).click()
    await expect(page.getByTestId("page-title")).toHaveText("会话搜索")
    await expect(page.getByTestId("hit")).toHaveCount(17)
  })

  test("deep link works on a fresh load", async ({ page, request }) => {
    await search(page, "buffer")
    const href = await page.getByTestId("hit").first().getAttribute("href")
    await page.goto(href!)
    await expect(page.getByTestId("transcript")).toBeVisible()
    await expect(page.locator("[data-hit]")).toHaveCount(1)
    const res = await request.get("/api/status")
    expect(res.status()).toBe(403) // API stays token-gated
  })
})

test.describe("chrome", () => {
  test("theme toggle persists across reloads", async ({ page }) => {
    await page.goto("/")
    await expect(page.locator("html")).not.toHaveClass(/dark/)
    await page.getByTestId("theme-toggle").click()
    await expect(page.locator("html")).toHaveClass(/dark/)
    await page.reload()
    await expect(page.locator("html")).toHaveClass(/dark/)
  })

  test("devices page validates the add form", async ({ page }) => {
    await page.goto("/devices/")
    await expect(page.getByText("尚未注册设备")).toBeVisible()
    await page.getByRole("button", { name: "rsync 安装并建索引" }).click()
    await expect(page.getByText("需要填写设备名称")).toBeVisible()
  })

  test("logs record searches", async ({ page }) => {
    await search(page, "backoff")
    await page.getByRole("link", { name: "日志查看" }).click()
    await expect(page.getByTestId("logbox")).toContainText("搜索「backoff」")
  })
})
