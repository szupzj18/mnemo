import { describe, expect, it } from "vitest"
import { clockParts, fmtAgo, fmtDur, fmtGap, hueOf, oneLine, snippetSegments, srcSummary, totals } from "./format"

describe("fmtDur", () => {
  it.each([
    [null, ""],
    [-1, ""],
    [0.25, "250ms"],
    [4, "4.0s"],
    [12.4, "12s"],
    [185, "3分5秒"],
    [120, "2分"],
    [3720, "1小时2分"],
    [7200, "2小时"],
  ])("%s -> %s", (sec, want) => expect(fmtDur(sec as number | null)).toBe(want))
})

describe("fmtGap", () => {
  it.each([
    [140, "2分钟"],
    [3900, "1小时5分"],
    [7200, "2小时"],
    [3 * 86400 + 5, "3天"],
  ])("%s -> %s", (sec, want) => expect(fmtGap(sec)).toBe(want))
})

describe("fmtAgo", () => {
  const now = 1_700_000_000_000
  it.each([
    [null, "从未同步"],
    [now / 1000 - 10, "刚刚同步"],
    [now / 1000 - 4 * 60, "4m前同步"],
    [now / 1000 - 3 * 3600, "3h前同步"],
    [now / 1000 - 2 * 86400, "2d前同步"],
    [now / 1000 + 60, "刚刚同步"], // clock skew never goes negative
  ])("%s -> %s", (ts, want) => expect(fmtAgo(ts, now)).toBe(want))
})

describe("snippetSegments", () => {
  it("splits [[marks]] and collapses whitespace", () => {
    expect(snippetSegments("a [[retry]]\n\n[[budget]] b")).toEqual([
      { text: "a ", mark: false },
      { text: "retry", mark: true },
      { text: " ", mark: false },
      { text: "budget", mark: true },
      { text: " b", mark: false },
    ])
  })
  it("handles text without marks", () => expect(snippetSegments("plain")).toEqual([{ text: "plain", mark: false }]))
})

describe("misc", () => {
  it("totals and srcSummary", () => {
    const s = { claude: { files: 2, messages: 21 }, codex: { files: 2, messages: 17 }, pi: { files: 1, messages: 7 } }
    expect(totals(s)).toEqual({ files: 5, msgs: 45 })
    expect(srcSummary(s)).toBe("claude 2 · codex 2 · pi 1")
    expect(srcSummary(undefined)).toBe("")
  })
  it("oneLine takes the first non-empty line and clips", () => {
    expect(oneLine("\n  first line \nsecond", 50)).toBe("first line")
    expect(oneLine("abcdef", 3)).toBe("abc…")
  })
  it("hueOf is stable and in range", () => {
    expect(hueOf("devbox-126")).toBe(hueOf("devbox-126"))
    expect(hueOf("devbox-126")).toBeGreaterThanOrEqual(0)
    expect(hueOf("devbox-126")).toBeLessThan(360)
  })
  it("clockParts uses local time (UTC in tests)", () => {
    expect(clockParts("2026-09-21T02:14:35Z")).toEqual({ hm: "02:14", ss: ":35" })
  })
})
