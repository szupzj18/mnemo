"use client"

import { Laptop, RefreshCw, Server } from "lucide-react"
import { Button } from "@/components/ui/button"
import { fmtAgo, hueOf } from "@/lib/format"
import { cn } from "@/lib/utils"

export type DeviceState = "ok" | "bad" | "checking" | "unknown"

const STATE_LABEL: Record<DeviceState, string> = { ok: "就绪", bad: "离线", checking: "检测中", unknown: "未检测" }
const DOT: Record<DeviceState, string> = {
  ok: "bg-ok",
  bad: "bg-err",
  checking: "bg-warn animate-pulse",
  unknown: "bg-faint",
}
const BADGE: Record<DeviceState, string> = {
  ok: "bg-ok-soft text-ok",
  bad: "bg-err-soft text-err",
  checking: "bg-warn-soft text-warn",
  unknown: "bg-muted text-faint",
}

export interface DeviceCardProps {
  name: string
  tag: string
  local?: boolean
  state: DeviceState
  description: React.ReactNode
  sources?: string
  lastSync?: number | null
  onSync?: () => void
  actions?: React.ReactNode
}

export function DeviceCard({ name, tag, local, state, description, sources, lastSync, onSync, actions }: DeviceCardProps) {
  const hue = hueOf(name)
  const Icon = local ? Laptop : Server
  return (
    <div
      data-testid="device-card"
      data-device={name}
      data-state={state}
      className="group relative flex min-h-[176px] flex-col gap-3.5 rounded-2xl border bg-card px-5 pt-[18px] pb-4 transition-[border-color,box-shadow] hover:border-foreground/15 hover:shadow-sm"
    >
      <div className="flex min-w-0 items-center gap-3.5">
        <div
          className="relative grid size-[52px] shrink-0 place-items-center rounded-full"
          style={{ background: `hsl(${hue} 70% 92%)`, color: `hsl(${hue} 45% 38%)` }}
        >
          <Icon className="size-6" strokeWidth={1.9} />
          <span
            className={cn("absolute right-0 bottom-px size-[13px] rounded-full ring-[3px] ring-card", DOT[state])}
            aria-hidden
          />
        </div>
        <div className="flex min-w-0 flex-col items-start gap-1.5">
          <span className="max-w-full truncate text-base font-bold tracking-tight" title={name}>
            {name}
          </span>
          <span
            className="max-w-full truncate rounded-full border border-input bg-muted px-2.5 py-px font-mono text-xs text-subtle"
            title={tag}
          >
            {tag}
          </span>
        </div>
      </div>
      <div className="text-[13.5px] leading-relaxed text-subtle">
        {description}
        {sources ? <span className="mt-1 block text-xs text-faint">{sources}</span> : null}
      </div>
      <div className="mt-auto flex items-center gap-2">
        <span className={cn("rounded-lg px-2.5 py-0.5 text-[12.5px] font-semibold", BADGE[state])} data-testid="device-state">
          {STATE_LABEL[state]}
        </span>
        <span className="tabular ml-auto text-[12.5px] text-faint" suppressHydrationWarning data-testid="device-ago">
          {lastSync === undefined ? "" : fmtAgo(lastSync)}
        </span>
        {onSync ? (
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="增量同步"
            className="opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100 [@media(hover:none)]:opacity-100"
            onClick={onSync}
          >
            <RefreshCw />
          </Button>
        ) : null}
      </div>
      {actions ? <div className="flex flex-wrap gap-1.5 border-t pt-3">{actions}</div> : null}
    </div>
  )
}

export function remoteState(
  name: string,
  ping: Record<string, { ok: boolean }>,
  checking: Set<string>,
): DeviceState {
  if (checking.has(name)) return "checking"
  const p = ping[name]
  if (!p) return "unknown"
  return p.ok ? "ok" : "bad"
}
