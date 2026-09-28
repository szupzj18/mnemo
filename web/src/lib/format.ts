import type { SourceCounts } from "./api"

export const ROLE_LABEL: Record<string, string> = { user: "用户", assistant: "助手", tool: "工具", system: "系统" }
export const KIND_LABEL: Record<string, string> = {
  text: "对话",
  summary: "摘要",
  tool_call: "工具调用",
  tool_result: "工具结果",
  reasoning: "思考",
}

export function totals(sources: SourceCounts) {
  let files = 0
  let msgs = 0
  for (const k in sources) {
    files += sources[k].files
    msgs += sources[k].messages
  }
  return { files, msgs }
}

export function srcSummary(sources: SourceCounts | undefined): string {
  return Object.entries(sources ?? {})
    .map(([k, v]) => `${k} ${v.files}`)
    .join(" · ")
}

/** "刚刚同步" / "4m前同步" / "3h前同步" / "2d前同步" from a unix timestamp (seconds). */
export function fmtAgo(ts: number | null | undefined, now: number = Date.now()): string {
  if (!ts) return "从未同步"
  const d = Math.max(0, now / 1000 - ts)
  if (d < 60) return "刚刚同步"
  if (d < 3600) return `${Math.floor(d / 60)}m前同步`
  if (d < 86400) return `${Math.floor(d / 3600)}h前同步`
  return `${Math.floor(d / 86400)}d前同步`
}

export function fmtTs(ts: number | null | undefined): string {
  return ts ? new Date(ts * 1000).toLocaleString() : "从未同步"
}

/** Tool-call durations: 850ms, 4.0s, 12s, 3分5秒, 1小时2分. */
export function fmtDur(sec: number | null | undefined): string {
  if (sec == null || !isFinite(sec) || sec < 0) return ""
  if (sec < 1) return `${Math.round(sec * 1000)}ms`
  if (sec < 60) return (sec < 10 ? sec.toFixed(1) : String(Math.round(sec))) + "s"
  const m = Math.floor(sec / 60)
  const ss = Math.floor(sec % 60)
  if (m < 60) return `${m}分${ss ? ss + "秒" : ""}`
  const h = Math.floor(m / 60)
  return `${h}小时${m % 60 ? (m % 60) + "分" : ""}`
}

/** Idle gaps between messages: 2分钟, 1小时5分, 3天. */
export function fmtGap(sec: number): string {
  if (sec < 3600) return `${Math.round(sec / 60)}分钟`
  if (sec < 86400) {
    const h = Math.floor(sec / 3600)
    const mm = Math.floor(sec / 60) % 60
    return `${h}小时${mm ? mm + "分" : ""}`
  }
  return `${Math.floor(sec / 86400)}天`
}

export function fmtSecs(ms: number): string {
  return (ms / 1000).toFixed(1) + "s"
}

/** HH:MM and :SS of an ISO timestamp in local time; falls back to the raw slice. */
export function clockParts(ts: string | null | undefined): { hm: string; ss: string } {
  const d = new Date(ts ?? "")
  if (isNaN(d.getTime())) return { hm: String(ts ?? "").slice(11, 16), ss: String(ts ?? "").slice(16, 19) }
  const p = (n: number) => String(n).padStart(2, "0")
  return { hm: `${p(d.getHours())}:${p(d.getMinutes())}`, ss: `:${p(d.getSeconds())}` }
}

export function fmtMsgTime(ts: string | null | undefined): string {
  if (!ts) return ""
  const d = new Date(ts)
  return isNaN(d.getTime()) ? String(ts).slice(11, 19) : d.toLocaleTimeString()
}

export function oneLine(text: string | null | undefined, n: number): string {
  const line =
    String(text ?? "")
      .split("\n")
      .map((s) => s.trim())
      .find(Boolean) ?? ""
  return line.length > n ? line.slice(0, n) + "…" : line
}

/** Stable hue per device name for avatar tints. */
export function hueOf(name: string): number {
  let h = 0
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) % 360
  return h
}

/** Splits an FTS snippet with [[term]] markers into plain and marked segments. */
export function snippetSegments(snippet: string): { text: string; mark: boolean }[] {
  const out: { text: string; mark: boolean }[] = []
  const re = /\[\[([\s\S]*?)\]\]/g
  const clean = snippet.replace(/\s+/g, " ")
  let last = 0
  let m: RegExpExecArray | null
  while ((m = re.exec(clean))) {
    if (m.index > last) out.push({ text: clean.slice(last, m.index), mark: false })
    out.push({ text: m[1], mark: true })
    last = m.index + m[0].length
  }
  if (last < clean.length) out.push({ text: clean.slice(last), mark: false })
  return out
}
