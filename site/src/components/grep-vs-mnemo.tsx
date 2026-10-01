// grep vs. mnemo on the same query. Figures: docs/benchmarks.md, "Compared with grep"
// (a common English term over 729 session files, measured 2026-09-24).
const NOISE = [
  '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_01H…',
  '{"type":"user","message":{"role":"user","content":[{"tool_use_id":"toolu_0…',
  '{"timestamp":"2026-09-12T03:14:07.992Z","type":"response_item","payload":{"t…',
  '{"type":"assistant","message":{"id":"msg_01","content":[{"type":"text","tex…',
  '{"parentUuid":"4be1…","isSidechain":false,"userType":"external","cwd":"/ho…',
  '{"type":"user","message":{"role":"user","content":"<system-reminder>\\nAs y…',
]

const HITS = [
  { agent: "codex", where: "devbox-b", when: "Sep 26", text: "clamp the delay after adding jitter so retry never exceeds the cap" },
  { agent: "claude", where: "local", when: "Sep 24", text: "retry budget: 5 attempts, exponential backoff from 200 ms" },
  { agent: "pi", where: "devbox-a", when: "Sep 19", text: "flaky retry test: seed the jitter in CI" },
]

export function GrepVsMnemo() {
  return (
    <div className="grid gap-4 text-left md:grid-cols-2">
      <div className="flex flex-col overflow-hidden rounded-2xl bg-neutral-950 ring-1 ring-neutral-800">
        <Bar title="grep over raw logs" />
        <div className="relative flex-1 px-5 pt-4 pb-5 font-mono text-[12px] leading-relaxed">
          <div className="text-neutral-300"><span className="text-neutral-500">$ </span>grep -r &quot;&lt;common term&gt;&quot; ~/.claude ~/.codex ~/.pi</div>
          <div className="mt-3 space-y-1 overflow-hidden text-neutral-600 [mask-image:linear-gradient(black_50%,transparent)]">
            {[...NOISE, ...NOISE].map((l, i) => (
              <div key={i} className="truncate">{l}</div>
            ))}
          </div>
          <Result items={[["4.35 s", "scan"], ["1,541 MB", "output"], ["16,548", "lines, unranked"]]} tone="neutral" />
        </div>
      </div>
      <div className="flex flex-col overflow-hidden rounded-2xl bg-neutral-950 ring-1 ring-brand/60">
        <Bar title="mnemo" accent />
        <div className="flex-1 px-5 pt-4 pb-5 font-mono text-[12px] leading-relaxed">
          <div className="text-neutral-300"><span className="text-neutral-500">$ </span>mnemo search &quot;&lt;common term&gt;&quot;</div>
          <div className="mt-3 space-y-3">
            {HITS.map((h, i) => (
              <div key={i}>
                <div className="text-neutral-500">
                  <span className="text-brand">{i + 1}.</span> {h.agent} · {h.where} · {h.when}
                </div>
                <div className="truncate text-neutral-200">{h.text}</div>
              </div>
            ))}
          </div>
          <Result items={[["<100 ms", "search"], ["~3,200", "tokens, top 20"], ["ranked", "BM25 + RRF"]]} tone="brand" />
        </div>
      </div>
    </div>
  )
}

function Bar({ title, accent }: { title: string; accent?: boolean }) {
  return (
    <div className="flex items-center gap-1.5 border-b border-neutral-800 px-4 py-2.5">
      {[0, 1, 2].map((i) => <span key={i} className="size-2.5 rounded-full bg-neutral-700" />)}
      <span className={`ml-3 text-xs ${accent ? "text-brand" : "text-neutral-500"}`}>{title}</span>
    </div>
  )
}

function Result({ items, tone }: { items: [string, string][]; tone: "brand" | "neutral" }) {
  return (
    <div className="mt-5 grid grid-cols-3 gap-2 border-t border-neutral-800 pt-4 font-sans">
      {items.map(([v, k]) => (
        <div key={k}>
          <div className={`text-lg font-semibold tabular-nums ${tone === "brand" ? "text-white" : "text-neutral-400"}`}>{v}</div>
          <div className="text-[11px] text-neutral-500">{k}</div>
        </div>
      ))}
    </div>
  )
}
