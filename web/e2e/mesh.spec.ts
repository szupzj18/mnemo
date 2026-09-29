import { expect, test, type Page, type Route } from "@playwright/test"

// Runs against `serve.mjs --mesh`: this device ("laptop") has two neighbors,
//   laptop ─▶ devbox-a (relays, node name build-a) ─▶ devbox-b (build-b)
//          └▶ devbox-down (never comes up)
// each other device holding the same demo sessions as the laptop; devbox-b
// runs an older copy of the code.

const pageErrors = new WeakMap<Page, string[]>()

test.beforeEach(async ({ page }) => {
  const errors: string[] = []
  pageErrors.set(page, errors)
  page.on("pageerror", (e) => errors.push(e.message))
})

test.afterEach(async ({ page }) => {
  expect(pageErrors.get(page), "no page errors").toEqual([])
})

const node = (page: Page, route: string) => page.locator(`[data-testid="topology-node"][data-route="${route}"]`)
const card = (page: Page, name: string) => page.locator(`[data-testid="device-card"][data-device="${name}"]`)

test("topology maps relayed and unreachable devices", async ({ page }) => {
  await page.goto("/topology/")
  await expect(page.getByTestId("page-title")).toHaveText("网络拓扑")
  await expect(page.getByTestId("topology-node")).toHaveCount(4)
  await expect(node(page, "local")).toContainText("本机")
  await expect(node(page, "devbox-a")).toHaveAttribute("data-state", "ok")
  await expect(node(page, "devbox-a")).toContainText("中转")
  await expect(node(page, "devbox-a/devbox-b")).toHaveAttribute("data-state", "ok")
  await expect(node(page, "devbox-down")).toHaveAttribute("data-state", "bad")
  await expect(page.getByTestId("topology-edge")).toHaveCount(3)
  await expect(page.getByTestId("topology-summary")).toContainText("2 台在线 · 共 3 台")
  const row = page.locator('[data-testid="topology-row"][data-route="devbox-a/devbox-b"]')
  await expect(row).toContainText("build-b")
  await expect(row).toContainText("在线")
  await expect(row.getByTestId("topology-code")).toHaveText("需更新")
  await expect(node(page, "devbox-a/devbox-b")).toHaveAttribute("data-outdated", "true")
  await expect(node(page, "devbox-a")).toHaveAttribute("data-outdated", "false")
  await expect(page.getByTestId("topology-summary")).toContainText("1 台需更新")
})

test("this device can be renamed and its relay needs confirmation", async ({ page }) => {
  // The page probes the topology on load; hold that answer until after the
  // rename so a stale probe result cannot put the old name back.
  let release: () => void = () => {}
  const held = new Promise<void>((r) => (release = r))
  await page.route("**/api/topology", async (route) => {
    const res = await route.fetch()
    await held
    await route.fulfill({ response: res })
  })
  await page.goto("/devices/")
  const settings = page.getByTestId("node-settings")
  await expect(settings.getByTestId("node-name")).toHaveText("laptop")

  await settings.getByRole("button", { name: "重命名本机" }).click()
  await settings.getByLabel("本机节点名").fill("laptop-2")
  await settings.getByLabel("本机节点名").press("Enter")
  await expect(settings.getByTestId("node-name")).toHaveText("laptop-2")
  release()
  await expect(page.getByTestId("remote-forward-state").first()).toHaveText("中转：已开启")
  await expect(settings.getByTestId("node-name")).toHaveText("laptop-2")
  await settings.getByRole("button", { name: "重命名本机" }).click()
  await settings.getByLabel("本机节点名").fill("laptop")
  await settings.getByRole("button", { name: "保存" }).click()
  await expect(settings.getByTestId("node-name")).toHaveText("laptop")

  const toggle = settings.getByTestId("node-forward")
  await toggle.click()
  await expect(page.getByRole("alertdialog")).toContainText("在 本机 上开启中转？")
  await page.getByRole("button", { name: "取消" }).click()
  await expect(toggle).not.toBeChecked()

  await toggle.click()
  await page.getByTestId("forward-confirm").click()
  await expect(toggle).toBeChecked()
  await expect(settings).toContainText("中转 已开启")
  await toggle.click() // turning it off needs no confirmation
  await expect(toggle).not.toBeChecked()
})

test("a neighbor's relay can be switched from here", async ({ page }) => {
  await page.goto("/devices/")
  const a = card(page, "devbox-a")
  await expect(a.getByTestId("remote-forward-state")).toHaveText("中转：已开启")
  await expect(card(page, "devbox-down").getByTestId("remote-forward-state")).toContainText("未知")
  await expect(card(page, "devbox-down").getByTestId("remote-forward")).toHaveCount(0)

  await a.getByTestId("remote-forward").click()
  await expect(a.getByTestId("remote-forward-state")).toHaveText("中转：已关闭")
  await page.getByRole("link", { name: "网络拓扑" }).click()
  await expect(page.getByTestId("topology-node")).toHaveCount(3)
  await expect(node(page, "devbox-a/devbox-b")).toHaveCount(0)

  await page.getByRole("link", { name: "设备管理" }).click()
  await a.getByTestId("remote-forward").click()
  await expect(page.getByRole("alertdialog")).toContainText("在 devbox-a 上开启中转？")
  await page.getByTestId("forward-confirm").click()
  await expect(a.getByTestId("remote-forward-state")).toHaveText("中转：已开启")
  await page.getByRole("link", { name: "网络拓扑" }).click()
  await expect(page.getByTestId("topology-node")).toHaveCount(4)
})

test("search reaches devices behind a relay and opens their sessions", async ({ page }) => {
  await page.goto("/search/")
  await page.getByLabel("搜索关键词").fill("backoff")
  await page.getByLabel("搜索关键词").press("Enter")
  await expect(page.getByTestId("hit-count")).toContainText("用时")
  const relayed = page.locator('[data-testid="hit"]').filter({ hasText: "devbox-a/devbox-b" })
  await expect(relayed.first()).toBeVisible()
  await relayed.first().click()
  await expect(page.getByTestId("page-title")).toHaveText("会话全文")
  await expect(page.locator("[data-hit]")).toBeVisible()
})

/** Latencies vary run to run; pin them so the pixels don't. */
async function pinLatency(page: Page) {
  await page.route("**/api/topology", async (route: Route) => {
    const res = await route.fetch()
    const pin = (v: unknown): unknown =>
      Array.isArray(v)
        ? v.map(pin)
        : v && typeof v === "object"
          ? Object.fromEntries(Object.entries(v).map(([k, x]) => [k, k === "ms" ? 42 : pin(x)]))
          : v
    await route.fulfill({ response: res, json: pin(await res.json()) })
  })
}

for (const theme of ["light", "dark"] as const) {
  test(`visual: topology (${theme})`, async ({ page }) => {
    await page.addInitScript((t) => localStorage.setItem("mnemo-theme", t), theme)
    await pinLatency(page)
    await page.goto("/topology/")
    await expect(page.getByTestId("topology-node")).toHaveCount(4)
    await expect(page).toHaveScreenshot(`topology-${theme}.png`)
  })
}

test("a neighbor can be allowed to search this device back", async ({ page }) => {
  await page.goto("/devices/")
  const a = card(page, "devbox-a")
  await expect(a.getByTestId("inbound-state")).toHaveText("反向访问：关闭")
  await a.getByTestId("inbound-toggle").click()
  await expect(page.getByRole("alertdialog")).toContainText("允许 devbox-a 搜索本机？")
  await page.getByRole("button", { name: "确认" }).click()
  await expect(a.getByTestId("inbound-state")).toContainText("已连接", { timeout: 30_000 })
  await expect(page.getByText("devbox-a 现在可以搜索本机")).toBeVisible()

  await a.getByTestId("inbound-toggle").click() // switching off needs no confirmation
  await expect(a.getByTestId("inbound-state")).toHaveText("反向访问：关闭", { timeout: 30_000 })
})

// Last: it changes the fixture (devbox-b gets the current code).
test("an outdated device behind a relay is brought up to date", async ({ page }) => {
  await page.goto("/topology/")
  const row = page.locator('[data-testid="topology-row"][data-route="devbox-a/devbox-b"]')
  await expect(row.getByTestId("topology-code")).toHaveText("需更新")
  await row.getByTestId("topology-upgrade").click()
  await expect(page.getByText("已更新 devbox-a/devbox-b")).toBeVisible({ timeout: 60_000 })
  await expect(row.getByTestId("topology-code")).toHaveText("最新")
  await expect(page.getByRole("button", { name: /更新落后设备/ })).toHaveCount(0)
  await expect(page.getByTestId("topology-summary")).not.toContainText("需更新")
})
