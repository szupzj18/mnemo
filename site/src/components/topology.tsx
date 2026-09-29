// A search travels through a relay to devices the laptop cannot reach
// directly; hits come back labelled with their route.
type N = { id: string; x: number; y: number; label: string; sub: string; relay?: boolean; you?: boolean }

const NODES: N[] = [
  { id: "laptop", x: 70, y: 165, label: "laptop", sub: "you are here", you: true },
  { id: "d109", x: 300, y: 95, label: "devbox-109", sub: "forwards", relay: true },
  { id: "mini", x: 300, y: 250, label: "mac-mini", sub: "direct link" },
  { id: "gpu", x: 528, y: 40, label: "gpu-box", sub: "via devbox-109" },
  { id: "d126", x: 528, y: 150, label: "devbox-126", sub: "via devbox-109" },
]
const EDGES: [string, string][] = [["laptop", "d109"], ["laptop", "mini"], ["d109", "gpu"], ["d109", "d126"]]
const at = (id: string) => NODES.find((n) => n.id === id)!
const W = 132
const H = 52

export function Topology() {
  return (
    <div className="min-w-0 rounded-2xl border border-neutral-200 bg-white p-6 dark:border-neutral-800 dark:bg-neutral-950">
      <svg viewBox="0 0 600 290" className="w-full" role="img" aria-label="A search from the laptop reaches devbox-126 and gpu-box through devbox-109">
        {EDGES.map(([a, b]) => {
          const p = at(a)
          const q = at(b)
          const x1 = p.x + W / 2
          const x2 = q.x - W / 2
          const mid = (x1 + x2) / 2
          const d = `M${x1} ${p.y} C ${mid} ${p.y}, ${mid} ${q.y}, ${x2} ${q.y}`
          return (
            <g key={a + b}>
              <path d={d} fill="none" className="stroke-neutral-200 dark:stroke-neutral-800" strokeWidth="6" strokeLinecap="round" />
              <path d={d} fill="none" stroke="#4176e6" strokeWidth="1.8" strokeLinecap="round" className="flow" />
            </g>
          )
        })}
        {NODES.map((n) => (
          <g key={n.id} transform={`translate(${n.x - W / 2} ${n.y - H / 2})`}>
            <rect
              width={W}
              height={H}
              rx={12}
              className={n.you ? "fill-neutral-950 dark:fill-white" : "fill-white stroke-neutral-300 dark:fill-neutral-900 dark:stroke-neutral-700"}
            />
            <text x={14} y={22} className={`text-[14px] font-semibold ${n.you ? "fill-white dark:fill-neutral-950" : "fill-neutral-900 dark:fill-neutral-100"}`}>{n.label}</text>
            <text x={14} y={39} className={`text-[11px] ${n.you ? "fill-neutral-400 dark:fill-neutral-500" : "fill-neutral-500"}`}>{n.sub}</text>
            {n.relay ? (
              <g transform={`translate(${W - 46} ${H - 22})`}>
                <rect width={38} height={16} rx={8} fill="#4176e6" fillOpacity="0.14" />
                <text x={19} y={11.5} textAnchor="middle" className="text-[10px] font-medium" fill="#4176e6">relay</text>
              </g>
            ) : null}
          </g>
        ))}
      </svg>
      <div className="mt-4 space-y-1.5 border-t border-neutral-200 pt-4 font-mono text-[12px] dark:border-neutral-800">
        {[
          ["devbox-109/devbox-126", "codex", "clamp the delay after adding jitter"],
          ["devbox-109/gpu-box", "codex", "--mem-fraction-static 0.88 → 0.80"],
          ["mac-mini", "claude", "retry budget: 5 attempts from 200 ms"],
        ].map(([route, agent, text]) => (
          <div key={route} className="flex gap-3 truncate">
            <span className="shrink-0 text-brand">{route}</span>
            <span className="shrink-0 text-neutral-500">{agent}</span>
            <span className="truncate text-neutral-700 dark:text-neutral-300">{text}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
