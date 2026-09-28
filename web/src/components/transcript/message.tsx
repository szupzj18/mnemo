"use client"

import { ChevronDown } from "lucide-react"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import type { Message } from "@/lib/api"
import { KIND_LABEL, ROLE_LABEL, clockParts, fmtDur, fmtMsgTime, oneLine } from "@/lib/format"
import { isLong, type MsgCtx } from "@/lib/transcript"
import { cn } from "@/lib/utils"
import { Body } from "./body"

/**
 * Timeline rail: each row draws its own 2px segment that overlaps the 16px
 * flex gap above and below, so segments join into one continuous line.
 */
function Rail({ ctx, ts }: { ctx: MsgCtx; ts: string }) {
  const c = clockParts(ts)
  return (
    <>
      <span
        aria-hidden
        className={cn(
          "absolute left-[70px] w-0.5 bg-border",
          ctx.turnStart ? "top-[9px]" : "-top-4",
          ctx.turnEnd ? "bottom-0" : "-bottom-4",
        )}
      />
      {ctx.turnStart ? (
        <span className="tabular absolute top-0.5 left-0 flex w-[76px] items-center justify-end gap-1 text-[11.5px] text-faint" title={ts}>
          <span>
            {c.hm}
            <span className="hidden sm:inline">{c.ss}</span>
          </span>
          {ctx.turnNo ? <em className="font-medium text-brand not-italic">#{ctx.turnNo}</em> : null}
          <span className="size-[7px] shrink-0 rounded-full bg-brand" />
        </span>
      ) : null}
    </>
  )
}

function Meta({ m, hit, align }: { m: Message; hit: boolean; align?: "right" }) {
  return (
    <div className={cn("mt-1.5 flex items-center gap-1.5 text-[11.5px] text-faint", align === "right" && "justify-end")}>
      <span suppressHydrationWarning>{fmtMsgTime(m.ts)}</span> · L{m.lineno}
      {hit ? <span className="rounded bg-brand-soft px-1.5 text-[11px] font-medium text-brand">命中</span> : null}
    </div>
  )
}

function Clamp({ long, expanded, onExpand, children }: { long: boolean; expanded: boolean; onExpand: () => void; children: React.ReactNode }) {
  const clamp = long && !expanded
  return (
    <>
      <div className={cn(clamp && "max-h-[420px] overflow-hidden [mask-image:linear-gradient(to_bottom,black_75%,transparent)]")}>{children}</div>
      {clamp ? (
        <button
          type="button"
          onClick={onExpand}
          className="mt-2 rounded-full bg-muted px-3 py-1 text-xs text-subtle hover:bg-accent"
        >
          展开全部 ↓
        </button>
      ) : null}
    </>
  )
}

export interface MessageRowProps {
  m: Message
  index: number
  ctx: MsgCtx
  hit: boolean
  terms: string[]
  expanded: boolean
  onExpand: (i: number) => void
  open: boolean
  onOpenChange: (i: number, open: boolean) => void
}

export function MessageRow({ m, index, ctx, hit, terms, expanded, onExpand, open, onOpenChange }: MessageRowProps) {
  const text = m.text ?? ""
  const long = isLong(text)
  const rowCls = cn("relative pl-[92px]", hit && "scroll-mt-24")
  const common = { "data-i": index, "data-hit": hit || undefined, "data-kind": m.kind, "data-testid": "msg" }

  if (m.kind === "text" && m.role === "user") {
    return (
      <div className={rowCls} {...common}>
        <Rail ctx={ctx} ts={m.ts} />
        <div className="flex flex-col items-end">
          <div className={cn("max-w-[85%] rounded-2xl bg-brand-soft px-4 py-2.5", hit && "ring-2 ring-brand/40")}>
            <Clamp long={long} expanded={expanded} onExpand={() => onExpand(index)}>
              <Body text={text} terms={terms} />
            </Clamp>
          </div>
          <Meta m={m} hit={hit} align="right" />
        </div>
      </div>
    )
  }

  if ((m.kind === "text" || m.kind == null) && (m.role === "assistant" || m.role == null)) {
    return (
      <div className={rowCls} {...common}>
        <Rail ctx={ctx} ts={m.ts} />
        <Clamp long={long} expanded={expanded} onExpand={() => onExpand(index)}>
          <Body text={text} terms={terms} />
        </Clamp>
        <Meta m={m} hit={hit} />
      </div>
    )
  }

  const disclosure = m.kind === "reasoning" || m.kind === "tool_call" || m.kind === "tool_result" || m.kind === "summary"
  if (!disclosure) {
    return (
      <div className={rowCls} {...common}>
        <Rail ctx={ctx} ts={m.ts} />
        <div className="text-[11.5px] text-faint">
          {ROLE_LABEL[m.role] ?? m.role} · {KIND_LABEL[m.kind] ?? m.kind} · {fmtMsgTime(m.ts)} · L{m.lineno}
        </div>
        <Body text={text} terms={terms} />
      </div>
    )
  }

  const tool = m.kind === "tool_call" || m.kind === "tool_result"
  return (
    <div className={rowCls} {...common}>
      <Rail ctx={ctx} ts={m.ts} />
      <Collapsible open={open} onOpenChange={(o) => onOpenChange(index, o)}>
        <CollapsibleTrigger
          className="group flex w-full min-w-0 items-center gap-2 rounded-md py-1 text-left text-[13px] text-subtle hover:text-foreground"
          data-testid="disclosure"
        >
          <ChevronDown className="size-3.5 shrink-0 -rotate-90 transition-transform group-data-[panel-open]:rotate-0" />
          <span className="shrink-0 font-medium">{KIND_LABEL[m.kind] ?? m.kind}</span>
          <span className="size-[3px] shrink-0 rounded-full bg-faint" />
          <span className="min-w-0 flex-1 truncate text-faint">{oneLine(text, 140)}</span>
          {ctx.dur != null && ctx.dur >= 0.05 ? (
            <span className="tabular shrink-0 text-[11.5px] text-brand" data-testid="tool-duration">
              {fmtDur(ctx.dur)}
            </span>
          ) : null}
          {hit ? (
            <span className="shrink-0 rounded bg-brand-soft px-1.5 text-[11px] font-medium text-brand">命中 L{m.lineno}</span>
          ) : (
            <span className="shrink-0 text-[11.5px] text-faint">L{m.lineno}</span>
          )}
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className={cn("mt-1.5 rounded-lg border bg-muted/60 px-3.5 py-2.5", m.kind === "reasoning" && "italic")}>
            <Clamp long={long} expanded={expanded} onExpand={() => onExpand(index)}>
              <Body text={text} terms={terms} mono={tool} />
            </Clamp>
          </div>
        </CollapsibleContent>
      </Collapsible>
    </div>
  )
}
