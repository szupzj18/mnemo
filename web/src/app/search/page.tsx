"use client"

import * as React from "react"
import Link from "next/link"
import { ChevronRight, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { SourceIcon } from "@/components/source-icon"
import type { Hit } from "@/lib/api"
import { KIND_LABEL, ROLE_LABEL, fmtSecs, snippetSegments } from "@/lib/format"
import { sessionHref } from "@/lib/links"
import { useStore } from "@/lib/store"
import { cn } from "@/lib/utils"

function Elapsed({ since }: { since: number }) {
  const [now, setNow] = React.useState(() => Date.now())
  React.useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 100)
    return () => clearInterval(t)
  }, [])
  return <>已用 {fmtSecs(now - since)}</>
}

function HitRow({ hit, index, terms }: { hit: Hit; index: number; terms: string[] }) {
  return (
    <Link
      href={sessionHref(hit, terms)}
      data-testid="hit"
      data-source={hit.source}
      className="grid grid-cols-[28px_36px_1fr_18px] items-start gap-3 rounded-xl border bg-card px-4 py-3.5 transition-colors outline-none hover:border-foreground/15 hover:bg-muted/40 focus-visible:ring-3 focus-visible:ring-ring"
    >
      <span className="tabular pt-2 text-center text-[13px] text-faint">{index + 1}</span>
      <SourceIcon source={hit.source} />
      <span className="flex min-w-0 flex-col gap-1">
        <span className="flex flex-wrap items-center gap-2 text-xs text-subtle">
          <span
            className={cn(
              "rounded-md px-2 py-0.5 font-medium",
              hit.host === "local" ? "bg-muted text-subtle" : "bg-brand-soft text-brand",
            )}
          >
            {hit.host}
          </span>
          <span className="rounded-md border px-2 py-0.5 font-semibold text-subtle">{hit.source}</span>
          <span>
            {ROLE_LABEL[hit.role] ?? hit.role} · {KIND_LABEL[hit.kind] ?? hit.kind}
          </span>
          <span className="text-faint">{(hit.ts || "").slice(0, 16).replace("T", " ")}</span>
        </span>
        <span className="line-clamp-2 text-sm break-words">
          {snippetSegments(hit.snippet || "").map((s, i) => (s.mark ? <mark key={i}>{s.text}</mark> : <span key={i}>{s.text}</span>))}
        </span>
        <span className="truncate font-mono text-xs text-faint">
          {hit.path.split("/").pop()} : {hit.lineno}
        </span>
      </span>
      <ChevronRight className="mt-2 size-4 text-faint" />
    </Link>
  )
}

export default function SearchPage() {
  const { status, search, setSearch, runSearch } = useStore()
  const hosts = ["local", ...(status?.remotes ?? []).map((r) => r.name)]
  const inputRef = React.useRef<HTMLInputElement>(null)

  function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!search.query.trim()) {
      inputRef.current?.focus()
      return
    }
    void runSearch()
  }

  const togglePick = (h: string, on: boolean) =>
    setSearch({ picked: on ? [...new Set([...search.picked, h])] : search.picked.filter((x) => x !== h) })

  return (
    <div className="flex flex-col gap-4.5">
      <Card className="gap-3.5 px-6 py-5">
        <h2 className="text-sm font-semibold">会话搜索</h2>
        <form className="flex flex-wrap items-center gap-2.5" onSubmit={submit} role="search">
          <Input
            ref={inputRef}
            className="min-w-64 flex-1"
            placeholder="搜索历史会话，多个关键词用空格分隔，如：部署 报错"
            value={search.query}
            onChange={(e) => setSearch({ query: e.target.value })}
            aria-label="搜索关键词"
            autoFocus
          />
          <Input
            type="number"
            className="w-20"
            min={1}
            max={50}
            value={search.limit}
            onChange={(e) => setSearch({ limit: parseInt(e.target.value, 10) || 20 })}
            title="每设备取数"
            aria-label="每设备取数"
          />
          <label className="flex items-center gap-2 text-sm text-subtle">
            <Checkbox
              checked={search.allHosts}
              onCheckedChange={(v) => setSearch({ allHosts: !!v, picked: v ? [] : hosts })}
            />
            全部设备
          </label>
          {!search.allHosts
            ? hosts.map((h) => (
                <label key={h} className="flex items-center gap-2 text-sm text-subtle">
                  <Checkbox checked={search.picked.includes(h)} onCheckedChange={(v) => togglePick(h, !!v)} />
                  {h}
                </label>
              ))
            : null}
          <Button type="submit" disabled={search.running} data-testid="search-submit">
            {search.running ? <Loader2 className="animate-spin" /> : null}
            {search.running ? "搜索中" : "查询"}
          </Button>
        </form>
        {search.running || search.perHost.length ? (
          <div className="flex flex-wrap gap-1.5" data-testid="host-chips">
            {search.running
              ? search.targets.map((h) => (
                  <span key={h} className="inline-flex items-center gap-1.5 rounded-full bg-muted px-2.5 py-0.5 text-xs text-subtle">
                    <Loader2 className="size-3 animate-spin" />
                    {h} · 搜索中
                  </span>
                ))
              : search.perHost.map((h) => (
                  <span
                    key={h.host}
                    className={cn(
                      "rounded-full px-2.5 py-0.5 text-xs font-medium",
                      h.ok ? "bg-ok-soft text-ok" : "bg-err-soft text-err",
                    )}
                  >
                    {h.host} · {h.ok ? `${h.ms} ms · ${h.hits} 命中` : "不可达"}
                  </span>
                ))}
          </div>
        ) : null}
        {!search.running && search.warnings.length ? (
          <div className="text-xs text-faint">
            {search.warnings.map((w) => (
              <div key={w}>{w}</div>
            ))}
          </div>
        ) : null}
      </Card>

      <Card className="gap-3.5 px-6 py-5" aria-busy={search.running}>
        <div className="flex items-center">
          <h2 className="text-sm font-semibold">搜索结果</h2>
          <span className="flex-1" />
          <span className="tabular text-xs text-subtle" data-testid="hit-count">
            {search.running && search.startedAt ? (
              <Elapsed since={search.startedAt} />
            ) : search.took ? (
              search.hits.length ? (
                `${search.hits.length} 条 · 用时 ${search.took} · 点击查看会话全文`
              ) : (
                `用时 ${search.took}`
              )
            ) : null}
          </span>
        </div>
        {search.running ? (
          <div data-testid="search-pending">
            <div className="mb-2.5 flex items-center gap-2 text-[12.5px] text-faint">
              <Loader2 className="size-3.5 animate-spin" />
              正在同步索引并搜索 {search.targets.length > 1 ? `${search.targets.length} 台设备` : search.targets[0] ?? "local"}…
            </div>
            <div className="flex flex-col gap-2.5">
              {[0, 1, 2].map((i) => (
                <div key={i} className="skeleton-shimmer h-[88px] rounded-xl border" />
              ))}
            </div>
          </div>
        ) : search.error ? (
          <div className="py-11 text-center text-[13px] text-faint">搜索失败：{search.error}</div>
        ) : search.hits.length ? (
          <div className="flex flex-col gap-2.5" data-testid="hits">
            {search.hits.map((h, i) => (
              <HitRow key={`${h.host}:${h.path}:${h.lineno}`} hit={h} index={i} terms={search.terms} />
            ))}
          </div>
        ) : (
          <div className="py-11 text-center text-[13px] text-faint">
            {search.searched ? "没有匹配结果。" : "输入查询词并查询；结果支持点击，可直接打开该条会话的完整全文。"}
          </div>
        )}
      </Card>
    </div>
  )
}
