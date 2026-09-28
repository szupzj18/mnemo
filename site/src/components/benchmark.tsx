// Controlled experiment from docs/benchmarks.md: fresh agents answered four
// "what did we do back then" questions with Mnemo vs. with grep/rg only.
const ROWS = [
  { label: "Tokens", mnemo: 259_303, grep: 336_048, fmt: (n: number) => `${Math.round(n / 1000)}k` },
  { label: "Tool calls", mnemo: 44, grep: 91, fmt: (n: number) => String(n) },
  { label: "Wall time", mnemo: 810, grep: 1358, fmt: (n: number) => `${Math.round(n / 60)} min` },
]

export function Benchmark() {
  return (
    <div className="space-y-8">
      {ROWS.map((r) => {
        const saved = Math.round((1 - r.mnemo / r.grep) * 100)
        return (
          <div key={r.label}>
            <div className="mb-3 flex items-baseline justify-between">
              <span className="text-sm font-medium">{r.label}</span>
              <span className="text-sm text-brand">−{saved}%</span>
            </div>
            <div className="space-y-2">
              <Bar name="mnemo" value={r.fmt(r.mnemo)} width={(r.mnemo / r.grep) * 100} highlight />
              <Bar name="grep" value={r.fmt(r.grep)} width={100} />
            </div>
          </div>
        )
      })}
    </div>
  )
}

function Bar({ name, value, width, highlight }: { name: string; value: string; width: number; highlight?: boolean }) {
  return (
    <div className="grid grid-cols-[56px_1fr] items-center gap-3 text-sm">
      <span className={highlight ? "font-medium" : "text-neutral-500"}>{name}</span>
      <div className="flex items-center gap-3">
        <div
          className={`h-6 rounded-md ${highlight ? "bg-brand" : "bg-neutral-200 dark:bg-neutral-800"}`}
          style={{ width: `${width}%` }}
        />
        <span className="tabular-nums whitespace-nowrap text-neutral-500">{value}</span>
      </div>
    </div>
  )
}
