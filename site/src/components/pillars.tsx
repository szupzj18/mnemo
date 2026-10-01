import { Bot, Gauge, Waypoints } from "lucide-react"

// The three things to remember about mnemo. Every number is from docs/benchmarks.md.
export const PILLARS = {
  speed: {
    icon: Gauge,
    id: "speed",
    title: "Found in milliseconds",
    stat: "<100 ms",
    statLabel: "search, end to end",
    body: "A ranked SQLite FTS5 index instead of scanning gigabytes of raw logs. Context around a hit in 12 ms, even in a 27k-message session.",
  },
  topology: {
    icon: Waypoints,
    id: "topology",
    title: "Across any topology",
    stat: "3 hops",
    statLabel: "through relays, loop-free",
    body: "Laptop, devboxes, the box behind the bastion: each device indexes its own sessions; searches pass along relays and merge by rank.",
  },
  agent: {
    icon: Bot,
    id: "for-agents",
    title: "Native to agents",
    stat: "−52%",
    statLabel: "tool calls vs. grep",
    body: "search → context → session over MCP, a Claude Code skill, a Pi extension or a JSON CLI. One command connects every agent it finds.",
  },
} as const

export type PillarKey = keyof typeof PILLARS

export function Pillars({ order, variant = "cards" }: { order: PillarKey[]; variant?: "cards" | "stats" }) {
  if (variant === "stats") {
    return (
      <div className="grid gap-px overflow-hidden rounded-2xl border border-neutral-200 bg-neutral-200 sm:grid-cols-3 dark:border-neutral-800 dark:bg-neutral-800">
        {order.map((k) => {
          const p = PILLARS[k]
          return (
            <a key={k} href={`#${p.id}`} className="group bg-white p-7 transition-colors hover:bg-neutral-50 dark:bg-neutral-950 dark:hover:bg-neutral-900">
              <div className="flex items-center gap-2 text-sm text-neutral-500"><p.icon className="size-4 text-brand" /> {p.title}</div>
              <div className="mt-4 text-4xl font-semibold tracking-tight tabular-nums">{p.stat}</div>
              <div className="mt-1 text-sm text-neutral-500">{p.statLabel}</div>
            </a>
          )
        })}
      </div>
    )
  }
  return (
    <div className="grid gap-4 sm:grid-cols-3">
      {order.map((k) => {
        const p = PILLARS[k]
        return (
          <a key={k} href={`#${p.id}`} className="rounded-2xl border border-neutral-200 p-7 transition-colors hover:border-neutral-300 dark:border-neutral-800 dark:hover:border-neutral-700">
            <span className="grid size-9 place-items-center rounded-lg bg-brand/10 text-brand"><p.icon className="size-5" /></span>
            <h3 className="mt-5 font-semibold">{p.title}</h3>
            <p className="mt-2 text-sm leading-relaxed text-neutral-600 dark:text-neutral-400">{p.body}</p>
            <div className="mt-5 flex items-baseline gap-2 border-t border-neutral-200 pt-4 dark:border-neutral-800">
              <span className="text-xl font-semibold tabular-nums">{p.stat}</span>
              <span className="text-xs text-neutral-500">{p.statLabel}</span>
            </div>
          </a>
        )
      })}
    </div>
  )
}
