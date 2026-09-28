import { describe, expect, it } from "vitest"
import type { Message } from "./api"
import {
  anchorIndex,
  buildRows,
  ensureVisible,
  firstMatch,
  highlight,
  initialWindow,
  matchIndices,
  stepMatch,
  tokenizeBody,
} from "./transcript"

const msg = (lineno: number, ts: string, role: string, kind: string, text = ""): Message => ({ lineno, ts, role, kind, text })

// Mirrors the demo Claude session: prompt, tool call/result, reply, idle gap, second turn.
const session: Message[] = [
  msg(2, "2026-09-21T02:12:00Z", "user", "text", "Find why the retry loop gives up"),
  msg(4, "2026-09-21T02:12:03Z", "assistant", "tool_call", "Bash(rg retry|backoff)"),
  msg(5, "2026-09-21T02:12:06Z", "user", "tool_result", "42: for attempt in range"),
  msg(6, "2026-09-21T02:12:15Z", "assistant", "text", "Retries use a fixed delay"),
  msg(7, "2026-09-21T02:14:35Z", "user", "text", "Switch to exponential backoff"),
  msg(8, "2026-09-22T09:00:00Z", "assistant", "text", "Done, backoff added"),
]

describe("windowing", () => {
  it("renders short sessions whole and long ones around the anchor", () => {
    expect(initialWindow(100, 50)).toEqual({ lo: 0, hi: 100 })
    expect(initialWindow(1000, 500)).toEqual({ lo: 410, hi: 591 })
    expect(initialWindow(1000, 10)).toEqual({ lo: 0, hi: 101 })
  })
  it("ensureVisible widens only as needed", () => {
    expect(ensureVisible({ lo: 400, hi: 600 }, 500, 1000)).toEqual({ lo: 400, hi: 600 })
    expect(ensureVisible({ lo: 400, hi: 600 }, 100, 1000)).toEqual({ lo: 10, hi: 600 })
    expect(ensureVisible({ lo: 400, hi: 600 }, 950, 1000)).toEqual({ lo: 400, hi: 1000 })
  })
  it("anchorIndex falls back to 0", () => {
    expect(anchorIndex(session, 6)).toBe(3)
    expect(anchorIndex(session, 999)).toBe(0)
  })
})

describe("matches", () => {
  it("requires every term, case-insensitively", () => {
    expect(matchIndices(session, ["BACKOFF"])).toEqual([1, 4, 5])
    expect(matchIndices(session, ["backoff", "exponential"])).toEqual([4])
    expect(matchIndices(session, [])).toEqual([])
  })
  it("starts at the anchor and wraps when stepping", () => {
    const m = [1, 4, 5]
    expect(firstMatch(m, 3)).toBe(4)
    expect(firstMatch(m, 9)).toBe(1)
    expect(firstMatch([], 0)).toBe(-1)
    expect(stepMatch(m, 5, 1)).toBe(1)
    expect(stepMatch(m, 1, -1)).toBe(5)
    expect(stepMatch(m, 99, 1)).toBe(1)
  })
})

describe("buildRows", () => {
  const rows = buildRows(session, 0, session.length)

  it("inserts day dividers and idle gaps >= 90s", () => {
    expect(rows.filter((r) => r.type === "day").map((r) => r.type === "day" && r.day)).toEqual(["2026-09-21", "2026-09-22"])
    const gaps = rows.filter((r) => r.type === "gap")
    expect(gaps).toHaveLength(1) // 02:12:15 -> 02:14:35; the day change suppresses the second
    expect(gaps[0].type === "gap" && gaps[0].label).toBe(140)
  })

  it("numbers turns on user prompts and times tool calls", () => {
    const msgs = rows.flatMap((r) => (r.type === "msg" ? [r] : []))
    expect(msgs.map((r) => r.ctx.turnNo)).toEqual([1, null, null, null, 2, null])
    expect(msgs[1].ctx.dur).toBe(3)
    expect(msgs[0].ctx.turnStart).toBe(true)
    expect(msgs[3].ctx.turnEnd).toBe(true) // next message starts a new turn
    expect(msgs[5].ctx.turnStart).toBe(true) // day change starts a new rail segment
  })

  it("respects the window bounds", () => {
    const part = buildRows(session, 2, 4)
    expect(part.flatMap((r) => (r.type === "msg" ? [r.index] : []))).toEqual([2, 3])
    expect(part.some((r) => r.type === "gap")).toBe(false)
  })
})

describe("body tokens", () => {
  it("highlights terms without regex injection", () => {
    expect(highlight("a (b) a", ["(b)"])).toEqual([
      { t: "text", v: "a " },
      { t: "mark", v: "(b)" },
      { t: "text", v: " a" },
    ])
    expect(highlight("text", [])).toEqual([{ t: "text", v: "text" }])
  })

  it("splits fenced code, inline code and marks", () => {
    const blocks = tokenizeBody("Use `backoff()` here\n```py\nbackoff(1)\n```\nend", ["backoff"])
    expect(blocks.map((b) => b.t)).toEqual(["para", "codeblock", "para"])
    expect(blocks[0].v).toContainEqual({ t: "code", v: [{ t: "mark", v: "backoff" }, { t: "text", v: "()" }] })
    expect(blocks[1].v).toEqual([{ t: "mark", v: "backoff" }, { t: "text", v: "(1)" }])
  })

  it("treats an unterminated fence as code to the end", () => {
    expect(tokenizeBody("x\n```\nopen", []).map((b) => b.t)).toEqual(["para", "codeblock"])
  })

  it("never produces HTML strings (React escapes text runs)", () => {
    const blocks = tokenizeBody("<img src=x onerror=alert(1)>", [])
    expect(blocks).toEqual([{ t: "para", v: [{ t: "text", v: "<img src=x onerror=alert(1)>" }] }])
  })
})
