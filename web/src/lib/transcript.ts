// Pure transcript logic for the session view: windowing, match finding,
// timeline rows and body tokenizing. Kept framework-free so it is unit tested.
import type { Message } from "./api"

export const WINDOW_THRESHOLD = 240
export const WINDOW_BEFORE = 90
export const WINDOW_AFTER = 91
export const PAGE_STEP = 120
export const IDLE_GAP_SEC = 90

export function isUserTurn(m: Message): boolean {
  return m.kind === "text" && m.role === "user"
}

/** Index of the anchor line (0 when not found). */
export function anchorIndex(messages: Message[], anchorLine: number): number {
  return Math.max(0, messages.findIndex((m) => m.lineno === anchorLine))
}

/** Long sessions render a window around the anchor; short ones render whole. */
export function initialWindow(count: number, anchorIdx: number): { lo: number; hi: number } {
  if (count <= WINDOW_THRESHOLD) return { lo: 0, hi: count }
  return { lo: Math.max(0, anchorIdx - WINDOW_BEFORE), hi: Math.min(count, anchorIdx + WINDOW_AFTER) }
}

/** Messages whose text contains every term (case-insensitive). */
export function matchIndices(messages: Message[], terms: string[]): number[] {
  const tt = terms.map((t) => t.toLowerCase()).filter(Boolean)
  if (!tt.length) return []
  const out: number[] = []
  messages.forEach((m, i) => {
    const txt = (m.text ?? "").toLowerCase()
    if (tt.every((t) => txt.includes(t))) out.push(i)
  })
  return out
}

/** First match at or after the anchor, else the first match, else -1. */
export function firstMatch(matches: number[], anchorIdx: number): number {
  const after = matches.find((i) => i >= anchorIdx)
  if (after !== undefined) return after
  return matches.length ? matches[0] : -1
}

export function stepMatch(matches: number[], cur: number, dir: 1 | -1): number {
  if (!matches.length) return -1
  const pos = matches.indexOf(cur)
  if (pos < 0) return matches[0]
  return matches[(pos + dir + matches.length) % matches.length]
}

/** Widen the window so that index `i` is rendered. */
export function ensureVisible(win: { lo: number; hi: number }, i: number, count: number) {
  let { lo, hi } = win
  if (i < lo) lo = Math.max(0, i - WINDOW_BEFORE)
  if (i >= hi) hi = Math.min(count, i + WINDOW_AFTER)
  return { lo, hi }
}

export interface MsgCtx {
  turnStart: boolean
  turnEnd: boolean
  turnNo: number | null
  /** Seconds from a tool_call to its tool_result. */
  dur: number | null
}

export type Row =
  | { type: "day"; key: string; day: string }
  | { type: "gap"; key: string; label: number }
  | { type: "msg"; key: string; index: number; message: Message; ctx: MsgCtx }

const day = (ts: string | null | undefined) => (ts ?? "").slice(0, 10)
const secsBetween = (a: string, b: string) => (Date.parse(b) - Date.parse(a)) / 1000

/**
 * Timeline rows for messages[lo, hi): day dividers, idle-gap chips (>= 90 s)
 * and per-message rail context (turn start/end, turn number, tool duration).
 */
export function buildRows(messages: Message[], lo: number, hi: number): Row[] {
  const count = messages.length
  const turnOf: number[] = new Array(count)
  let tn = 0
  messages.forEach((m, i) => {
    if (i === 0 || isUserTurn(m)) tn++
    turnOf[i] = tn
  })
  const rows: Row[] = []
  for (let i = lo; i < Math.min(hi, count); i++) {
    const m = messages[i]
    const prev = i > 0 ? messages[i - 1] : null
    const next = i < count - 1 ? messages[i + 1] : null
    const d = day(m.ts)
    const dayChg = !!(d && d !== (prev ? day(prev.ts) : ""))
    if (dayChg) rows.push({ type: "day", key: `d${i}`, day: d })
    if (prev && !dayChg && i > lo) {
      const gap = secsBetween(prev.ts, m.ts)
      if (isFinite(gap) && gap >= IDLE_GAP_SEC) rows.push({ type: "gap", key: `g${i}`, label: gap })
    }
    let dur: number | null = null
    if (m.kind === "tool_call" && next && next.kind === "tool_result") {
      const s = secsBetween(m.ts, next.ts)
      if (isFinite(s) && s >= 0) dur = s
    }
    rows.push({
      type: "msg",
      key: `m${i}`,
      index: i,
      message: m,
      ctx: {
        turnStart: i === 0 || isUserTurn(m) || dayChg,
        turnEnd: !next || isUserTurn(next) || day(m.ts) !== day(next.ts),
        turnNo: isUserTurn(m) ? turnOf[i] : null,
        dur,
      },
    })
  }
  return rows
}

export function isLong(text: string): boolean {
  return text.length > 1200 || text.split("\n").length > 24
}

export function sessionSpanSecs(startedAt: string | null, endedAt: string | null): number | null {
  if (!startedAt || !endedAt) return null
  const s = secsBetween(startedAt, endedAt)
  return isFinite(s) ? s : null
}

// ---------------------------------------------------------------- body tokens

export type Inline = { t: "text"; v: string } | { t: "mark"; v: string } | { t: "code"; v: Inline[] }
export type Block = { t: "para"; v: Inline[] } | { t: "codeblock"; v: Inline[] }

const escRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")

/** Splits text into plain and highlighted runs for the given terms. */
export function highlight(text: string, terms: string[]): Inline[] {
  const tt = terms.filter(Boolean)
  if (!tt.length || !text) return text ? [{ t: "text", v: text }] : []
  const re = new RegExp("(" + tt.map(escRe).join("|") + ")", "gi")
  const out: Inline[] = []
  let last = 0
  let m: RegExpExecArray | null
  while ((m = re.exec(text))) {
    if (m[0] === "") {
      re.lastIndex++
      continue
    }
    if (m.index > last) out.push({ t: "text", v: text.slice(last, m.index) })
    out.push({ t: "mark", v: m[0] })
    last = m.index + m[0].length
  }
  if (last < text.length) out.push({ t: "text", v: text.slice(last) })
  return out
}

const INLINE_CODE = /(^|[\s(\[])`([^`\n]{1,200})`(?=$|[\s).,:;!?\]])/g

function inlines(text: string, terms: string[]): Inline[] {
  const out: Inline[] = []
  let last = 0
  let m: RegExpExecArray | null
  INLINE_CODE.lastIndex = 0
  while ((m = INLINE_CODE.exec(text))) {
    const start = m.index + m[1].length
    if (start > last) out.push(...highlight(text.slice(last, start), terms))
    out.push({ t: "code", v: highlight(m[2], terms) })
    last = start + m[2].length + 2
  }
  if (last < text.length) out.push(...highlight(text.slice(last), terms))
  return out
}

/** Fenced ``` blocks become code blocks; `inline code` and search terms are marked. */
export function tokenizeBody(text: string | null | undefined, terms: string[]): Block[] {
  const src = text ?? ""
  const blocks: Block[] = []
  const fence = /```[^\n]*\n?([\s\S]*?)(?:```|$)/g
  let last = 0
  let m: RegExpExecArray | null
  while ((m = fence.exec(src))) {
    if (m[0] === "") {
      fence.lastIndex++
      continue
    }
    if (m.index > last) blocks.push({ t: "para", v: inlines(src.slice(last, m.index), terms) })
    blocks.push({ t: "codeblock", v: highlight(m[1].replace(/\n$/, ""), terms) })
    last = m.index + m[0].length
  }
  if (last < src.length) blocks.push({ t: "para", v: inlines(src.slice(last), terms) })
  return blocks
}
