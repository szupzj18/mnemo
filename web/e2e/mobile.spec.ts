import { expect, test, type Page } from "@playwright/test"

// Phone-width checks for the website's demo section and the promo page it embeds.
// Runs against serve-site.mjs: site/out served under /mnemo/ like GitHub Pages,
// in a 390x844 touch viewport (see the "mobile" project).
//
// Assertions are measurements, not pixel diffs: the promo is an animation, so a
// baseline image would never be stable. `?t=` freezes the promo at a time.

const PROMO = "/mnemo/promo/index.html"
const INSTALL = "curl -fsSL https://szupzj18.github.io/mnemo/install.sh | sh"

const problems = new WeakMap<Page, string[]>()

test.beforeEach(async ({ page, baseURL }) => {
  const list: string[] = []
  problems.set(page, list)
  page.on("pageerror", (e) => list.push("page error: " + e.message))
  page.on("console", (m) => m.type() === "error" && list.push("console: " + m.text()))
  page.on("requestfailed", (r) => list.push("request failed: " + r.url()))
  // Fonts are inlined, so nothing should leave this origin.
  page.on("request", (r) => {
    if (!r.url().startsWith(baseURL!) && !r.url().startsWith("data:")) list.push("external request: " + r.url())
  })
})

test.afterEach(async ({ page }) => {
  expect(problems.get(page), "no errors or external requests").toEqual([])
})

const overflowX = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)

test("home page does not scroll sideways", async ({ page }) => {
  await page.goto("/mnemo/")
  await page.locator("#demo").scrollIntoViewIfNeeded()
  expect(await overflowX(page)).toBeLessThanOrEqual(0)
})

test("the embedded promo fits its frame", async ({ page }) => {
  await page.goto("/mnemo/")
  const iframe = page.locator("#demo iframe")
  await iframe.scrollIntoViewIfNeeded()
  const frame = page.frameLocator("#demo iframe")
  await frame.locator("#stage").waitFor()

  // The frame is the stage plus its control bar: nothing inside may scroll.
  const doc = await iframe.evaluate((f: HTMLIFrameElement) => {
    const d = f.contentDocument!.documentElement
    return { scrollW: d.scrollWidth, clientW: d.clientWidth, scrollH: d.scrollHeight, clientH: d.clientHeight }
  })
  expect(doc.scrollW).toBeLessThanOrEqual(doc.clientW)
  expect(doc.scrollH).toBeLessThanOrEqual(doc.clientH)

  const box = (await iframe.boundingBox())!
  const stage = (await frame.locator("#screen").boundingBox())!
  expect(stage.width / stage.height).toBeCloseTo(16 / 9, 1)
  // The control bar sits inside the frame, below the stage.
  const bar = (await frame.locator(".ctl").boundingBox())!
  expect(bar.y).toBeGreaterThanOrEqual(stage.y + stage.height - 1)
  expect(bar.y + bar.height).toBeLessThanOrEqual(box.y + box.height + 1)
  // ... and the seek bar in it is usable: it was once squeezed to a dot.
  expect((await frame.locator("#seek").boundingBox())!.width).toBeGreaterThanOrEqual(100)
})

test("the full promo page fits a phone", async ({ page }) => {
  await page.goto(`${PROMO}?lang=en`)
  await page.locator("#stage").waitFor()
  expect(await overflowX(page)).toBeLessThanOrEqual(0)
  expect((await page.locator("#seek").boundingBox())!.width).toBeGreaterThanOrEqual(100)
  for (const id of ["#play", "#seek", '.lang button[data-lang="zh"]']) {
    const b = (await page.locator(id).boundingBox())!
    expect(b.x, id).toBeGreaterThanOrEqual(0)
    expect(b.x + b.width, id).toBeLessThanOrEqual(page.viewportSize()!.width)
  }
})

test("language switch changes the captions", async ({ page }) => {
  await page.goto(`${PROMO}?embed=1&lang=en&t=17.5`)
  const cap = page.locator("#cap")
  await expect(cap).toContainText("search what another already did")
  await page.locator('.lang button[data-lang="zh"]').tap()
  await expect(cap).toContainText("彼此的历史")
  await expect(page.locator('.lang button[data-lang="zh"]')).toHaveAttribute("aria-pressed", "true")
  await page.locator('.lang button[data-lang="en"]').tap()
  await expect(cap).toContainText("search what another already did")
})

test("the timeline seeks and the ending offers a working copy button", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"])
  await page.goto(`${PROMO}?lang=en`)
  await page.locator("#stage").waitFor()
  await page.locator("#seek").fill("1000")
  await expect(page.locator("#cta1")).toContainText("shared past")
  const copy = page.locator("#cmdcopy")
  await expect(copy).toBeVisible()
  await copy.tap()
  await expect(copy).toHaveText("copied")
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(INSTALL)
  const link = page.locator("#url")
  await expect(link).toHaveAttribute("href", "https://github.com/szupzj18/mnemo")
  await expect(link).toHaveAttribute("target", "_blank")
})
