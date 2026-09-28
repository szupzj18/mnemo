"use client"

import * as React from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { ArrowDown, ArrowUp, ChevronLeft, Copy, Loader2 } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { MessageRow } from "@/components/transcript/message"
import { api, type Session } from "@/lib/api"
import { fmtDur, fmtGap } from "@/lib/format"
import {
  PAGE_STEP,
  anchorIndex,
  buildRows,
  ensureVisible,
  firstMatch,
  initialWindow,
  matchIndices,
  sessionSpanSecs,
  stepMatch,
} from "@/lib/transcript"
import { cn } from "@/lib/utils"

type ScrollMode = "anchor" | "match" | "older" | "stay"

interface View {
  data: Session
  raw: boolean
  lo: number
  hi: number
  matches: number[]
  cur: number
}

function initView(data: Session, anchorLine: number, terms: string[], raw: boolean): View {
  const idx = anchorIndex(data.messages, anchorLine)
  const win = initialWindow(data.count, idx)
  const matches = matchIndices(data.messages, terms)
  return { data, raw, ...win, matches, cur: firstMatch(matches, idx) }
}

export function SessionView() {
  const params = useSearchParams()
  // Remount per session so all view state starts fresh without resetting it in an effect.
  return (
    <SessionInner
      key={params.toString()}
      path={params.get("path") ?? ""}
      host={params.get("host") || "local"}
      anchor={parseInt(params.get("line") ?? "", 10) || 0}
      q={params.get("q") ?? ""}
    />
  )
}

function SessionInner({ path, host, anchor, q }: { path: string; host: string; anchor: number; q: string }) {
  const router = useRouter()
  const terms = React.useMemo(() => q.split(/\s+/).filter(Boolean), [q])

  const [view, setView] = React.useState<View | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [loadingRaw, setLoadingRaw] = React.useState(false)
  const [expanded, setExpanded] = React.useState<Set<number>>(new Set())
  const [open, setOpen] = React.useState<Set<number>>(new Set())
  const [scroll, setScroll] = React.useState<{ mode: ScrollMode; seq: number }>({ mode: "anchor", seq: 0 })
  const prevHeight = React.useRef(0)

  const fetchSession = React.useCallback(
    async (raw: boolean) => {
      const r = await api.session(path, host, raw)
      if (!r.ok) throw new Error(r.error)
      return r.session
    },
    [path, host],
  )

  const apply = React.useCallback(
    (session: Session, raw: boolean) => {
      setExpanded(new Set())
      // Disclosures start open on the hit line so the match is visible.
      const hitIdx = session.messages.findIndex((m) => m.lineno === anchor)
      setOpen(new Set(hitIdx >= 0 ? [hitIdx] : []))
      setView(initView(session, anchor, terms, raw))
      setScroll((s) => ({ mode: "anchor", seq: s.seq + 1 }))
    },
    [anchor, terms],
  )

  React.useEffect(() => {
    if (!path) return
    let alive = true
    fetchSession(false)
      .then((session) => {
        if (alive) apply(session, false)
      })
      .catch((e) => {
        if (!alive) return
        const msg = String((e as Error)?.message ?? e)
        setError(msg)
        toast.error("会话加载失败", { description: msg })
      })
    return () => {
      alive = false
    }
  }, [path, fetchSession, apply])

  React.useLayoutEffect(() => {
    if (!view) return
    const { mode } = scroll
    if (mode === "anchor" || mode === "match") {
      const sel = mode === "match" ? `[data-i="${view.cur}"]` : "[data-hit]"
      const el = document.querySelector<HTMLElement>(sel)
      if (el) el.scrollIntoView({ block: mode === "anchor" ? "start" : "center" })
    } else if (mode === "older") {
      window.scrollBy(0, document.body.scrollHeight - prevHeight.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scroll.seq])

  const toggleSet = (setter: typeof setOpen) => (i: number, on: boolean) =>
    setter((s) => {
      const n = new Set(s)
      if (on) n.add(i)
      else n.delete(i)
      return n
    })

  function gotoMatch(dir: 1 | -1) {
    if (!view || !view.matches.length) return
    const cur = stepMatch(view.matches, view.cur, dir)
    setOpen((s) => new Set(s).add(cur))
    setView({ ...view, cur, ...ensureVisible(view, cur, view.data.count) })
    setScroll((s) => ({ mode: "match", seq: s.seq + 1 }))
  }

  async function toggleRaw() {
    if (!view || loadingRaw) return
    const next = !view.raw
    setLoadingRaw(true)
    try {
      apply(await fetchSession(next), next)
    } catch (e) {
      toast.error("读取失败", { description: String((e as Error)?.message ?? e) })
    } finally {
      setLoadingRaw(false)
    }
  }

  function back() {
    if (window.history.length > 1) router.back()
    else router.push("/search/")
  }

  if (!path) return <div className="py-16 text-center text-sm text-faint">从搜索结果点击一条记录打开会话。</div>

  const s = view?.data
  const span = s ? sessionSpanSecs(s.started_at, s.ended_at) : null
  const pos = view && view.matches.length ? view.matches.indexOf(view.cur) + 1 : 0

  return (
    <div className="flex flex-col">
      <div className="sticky top-0 z-20 -mx-4 flex flex-wrap items-center gap-x-3 gap-y-2 border-b bg-background/95 px-4 py-2.5 backdrop-blur md:-mx-8 md:px-8">
        <Button variant="ghost" size="sm" onClick={back}>
          <ChevronLeft /> 返回
        </Button>
        <div className="min-w-0 max-w-[46ch]">
          <div className="truncate font-mono text-sm font-semibold" data-testid="session-title">
            {path.split("/").pop()}
          </div>
          <div className="truncate font-mono text-xs text-faint" title={path}>
            {host} · {path}
          </div>
        </div>
        <span className="flex-1" />
        {s ? (
          <div className="flex min-w-0 flex-wrap items-center gap-2 text-xs text-subtle" data-testid="session-meta">
            <span className="rounded-md bg-muted px-2 py-0.5 font-medium">{host}</span>
            <span className="rounded-md border px-2 py-0.5 font-semibold">{s.source}</span>
            <span className="tabular">
              {s.count} 条消息 · {(s.started_at ?? "").slice(0, 16).replace("T", " ")} → {(s.ended_at ?? "").slice(11, 19)}
              {span != null && span >= 60 ? ` · 共 ${fmtDur(span)}` : ""}
            </span>
            <span className={cn("rounded-full px-2 py-0.5", view?.raw ? "bg-warn-soft text-warn" : "bg-muted text-faint")}>
              {view?.raw ? "原始全文" : "索引版 · 单条≤20k"}
            </span>
            {s.cwd ? <span className="truncate font-mono text-faint">{s.cwd}</span> : null}
          </div>
        ) : null}
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="sm"
            disabled={!view?.matches.length}
            onClick={() => gotoMatch(-1)}
            title="上一处匹配"
            data-testid="match-prev"
          >
            <ArrowUp />
            {view?.matches.length ? `${pos}/${view.matches.length}` : null}
          </Button>
          <Button variant="ghost" size="icon-sm" disabled={!view?.matches.length} onClick={() => gotoMatch(1)} title="下一处匹配" data-testid="match-next">
            <ArrowDown />
          </Button>
          <Button size="sm" variant={view?.raw ? "outline" : "default"} disabled={!view || loadingRaw} onClick={() => void toggleRaw()}>
            {loadingRaw ? <Loader2 className="animate-spin" /> : null}
            {view?.raw ? "返回索引版" : "读取全文"}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() =>
              navigator.clipboard.writeText(path).then(
                () => toast.success("已复制会话路径"),
                () => toast.error("复制失败"),
              )
            }
          >
            <Copy /> 复制路径
          </Button>
        </div>
      </div>

      {error ? (
        <div className="py-16 text-center text-sm text-faint">加载失败：{error}</div>
      ) : !view ? (
        <div className="flex flex-col items-center gap-3 py-16 text-sm text-faint">
          <Loader2 className="size-5 animate-spin" />
          正在读取会话…
        </div>
      ) : (
        <div className="mx-auto flex w-full max-w-[836px] flex-col gap-4 py-6" data-testid="transcript">
          {view.lo > 0 ? (
            <div className="flex justify-center">
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  prevHeight.current = document.body.scrollHeight
                  setView({ ...view, lo: Math.max(0, view.lo - PAGE_STEP) })
                  setScroll((x) => ({ mode: "older", seq: x.seq + 1 }))
                }}
              >
                ↑ 加载更早 {Math.min(PAGE_STEP, view.lo)} 条
              </Button>
            </div>
          ) : null}
          {buildRows(view.data.messages, view.lo, view.hi).map((row) =>
            row.type === "day" ? (
              <div key={row.key} className="flex items-center gap-3 text-xs text-faint" data-testid="day-divider">
                <span className="h-px flex-1 bg-border" />
                {row.day}
                <span className="h-px flex-1 bg-border" />
              </div>
            ) : row.type === "gap" ? (
              <div key={row.key} className="relative -my-2 h-5 pl-[92px]" data-testid="idle-gap">
                <span aria-hidden className="absolute -top-4 -bottom-4 left-[70px] w-0.5 bg-border" />
                <span className="absolute top-0.5 left-[71px] -translate-x-1/2 rounded-full bg-background px-2 text-[11.5px] whitespace-nowrap text-faint">
                  空闲 {fmtGap(row.label)}
                </span>
              </div>
            ) : (
              <MessageRow
                key={row.key}
                m={row.message}
                index={row.index}
                ctx={row.ctx}
                hit={row.message.lineno === anchor}
                terms={terms}
                expanded={expanded.has(row.index)}
                onExpand={(i) => setExpanded((x) => new Set(x).add(i))}
                open={open.has(row.index)}
                onOpenChange={toggleSet(setOpen)}
              />
            ),
          )}
          {view.hi < view.data.count ? (
            <div className="flex justify-center">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setView({ ...view, hi: Math.min(view.data.count, view.hi + PAGE_STEP) })}
              >
                加载更晚 {Math.min(PAGE_STEP, view.data.count - view.hi)} 条 ↓
              </Button>
            </div>
          ) : null}
        </div>
      )}
    </div>
  )
}
