"use client"

import type { Graph, Positioned, TopoState } from "@/lib/topology"
import { layout } from "@/lib/topology"
import { cn } from "@/lib/utils"

const COL = 230
const ROW = 92
const W = 172
const H = 56

const DOT: Record<TopoState, string> = { ok: "fill-ok", bad: "fill-err", unknown: "fill-faint" }
const EDGE: Record<TopoState, string> = {
  ok: "stroke-ok/70",
  bad: "stroke-err/70 [stroke-dasharray:5_4]",
  unknown: "stroke-faint/60 [stroke-dasharray:3_4]",
}

const label = (n: Positioned) => (n.depth === 0 ? "本机" : n.route.split("/").pop()!)

function edgePath(a: Positioned, b: Positioned): string {
  if (a.x === b.x) {
    // Same hop count: bow out to the right of the column.
    const x = a.x + W / 2
    const bow = x + 36 + Math.abs(a.y - b.y) / 6
    return `M ${x} ${a.y} C ${bow} ${a.y}, ${bow} ${b.y}, ${x} ${b.y}`
  }
  const [l, r] = a.x < b.x ? [a, b] : [b, a]
  const x1 = l.x + W / 2
  const x2 = r.x - W / 2
  const mid = (x1 + x2) / 2
  return `M ${x1} ${l.y} C ${mid} ${l.y}, ${mid} ${r.y}, ${x2} ${r.y}`
}

export function TopologyGraph({ graph }: { graph: Graph }) {
  const { nodes, width, height } = layout(graph, COL, ROW)
  const at = new Map(nodes.map((n) => [n.key, n]))
  const pad = 48
  return (
    <div className="overflow-x-auto" data-testid="topology-graph">
      <svg
        viewBox={`0 0 ${width + pad} ${height}`}
        width={width + pad}
        height={height}
        className="mx-auto block max-w-none"
        role="img"
        aria-label="设备拓扑图"
      >
        {graph.edges.map((e) => {
          const a = at.get(e.from)
          const b = at.get(e.to)
          if (!a || !b) return null
          return (
            <path
              key={e.from + "|" + e.to}
              d={edgePath(a, b)}
              data-testid="topology-edge"
              data-state={e.state}
              className={cn("fill-none stroke-[1.6]", EDGE[e.state])}
            />
          )
        })}
        {nodes.map((n) => (
          <g
            key={n.key}
            transform={`translate(${n.x - W / 2} ${n.y - H / 2})`}
            data-testid="topology-node"
            data-route={n.route}
            data-state={n.state}
          >
            <title>
              {[n.route, n.nodeName && `节点名 ${n.nodeName}`, n.host && `SSH ${n.host}`, n.error]
                .filter(Boolean)
                .join("\n")}
            </title>
            <rect
              width={W}
              height={H}
              rx={14}
              className={cn(
                "stroke-[1.2]",
                n.depth === 0 ? "fill-brand-soft stroke-brand/40" : "fill-card stroke-border",
                n.state === "bad" && "stroke-err/50",
              )}
            />
            <circle cx={18} cy={H / 2} r={5} className={DOT[n.state]} />
            <text x={32} y={23} className="fill-foreground text-[13px] font-semibold">
              {truncate(label(n), 17)}
            </text>
            <text x={32} y={41} className="fill-faint text-[11px]">
              {truncate(subline(n), 22)}
            </text>
            {n.forward ? (
              <g transform={`translate(${W - 40} 8)`}>
                <rect width={32} height={16} rx={8} className="fill-brand-soft" />
                <text x={16} y={11.5} textAnchor="middle" className="fill-brand text-[10px] font-medium">
                  中转
                </text>
              </g>
            ) : null}
          </g>
        ))}
      </svg>
    </div>
  )
}

function subline(n: Positioned): string {
  if (n.state === "bad") return "无法连接"
  if (n.legacy) return "旧版本 · 无法探测后方"
  if (n.state === "unknown") return "未探测"
  const parts = [n.nodeName && n.nodeName !== label(n) ? n.nodeName : null, n.ms !== undefined ? `${n.ms} ms` : null]
  return parts.filter(Boolean).join(" · ") || (n.depth === 0 ? "本机" : "")
}

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n - 1) + "…" : s
}
