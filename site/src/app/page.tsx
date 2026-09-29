import { ArrowRight } from "lucide-react"
import { AgentTranscript, IntegrationStrip } from "@/components/agent-transcript"
import { Benchmark } from "@/components/benchmark"
import { CopyCommand } from "@/components/copy-command"
import { GrepVsMnemo } from "@/components/grep-vs-mnemo"
import { InstallTabs } from "@/components/install-tabs"
import { GitHubMark, Logo } from "@/components/logo"
import { Pillars } from "@/components/pillars"
import { Topology } from "@/components/topology"
import { DOCS, INSTALL, REPO, UV_INSTALL, asset } from "@/lib/site"

const muted = "text-neutral-600 dark:text-neutral-400"

const LOOP = [
  { tool: "search_sessions", q: "Where is it?", body: "Ranked hits: agent, device route, directory, time and a marked snippet." },
  { tool: "get_context", q: "What happened?", body: "The messages around a hit, read on the device that holds it." },
  { tool: "get_session", q: "Walk me through it", body: "The whole session, or just its head or tail." },
  { tool: "list_recent_sessions", q: "What was I doing?", body: "Recent sessions titled by their first real prompt, no keyword needed." },
]

const TOPOLOGY = [
  ["Direct links and relays", "Each device lists only its neighbors. Turn on forwarding on a device and searches pass through it to the devices behind it."],
  ["Routes you can follow", "Every hit carries its route, like devbox-109/devbox-126. Pass it back and reads travel the same path."],
  ["Loop-free, deduplicated", "Stable node ids and a hop budget stop cycles; a device reached twice is reported once, via the shortest route."],
  ["Nothing central", "No server, no shared store. Queries and the messages you ask for travel; indexes and raw logs stay on their device."],
  ["Kept in step", "Upgrade one machine and it brings every reachable device to the same code, relays included."],
]

const STATS = [
  ["~50 ms", "search, CLI end to end"],
  ["7 ms", "context on a 44k-line session"],
  ["0.1 s", "incremental sync when idle"],
  ["~3,200", "tokens for a top-20 result"],
]

function Themed({ light, dark, alt, className }: { light: string; dark: string; alt: string; className?: string }) {
  return (
    <picture>
      <source media="(prefers-color-scheme: dark)" srcSet={asset(dark)} />
      <img src={asset(light)} alt={alt} className={className} />
    </picture>
  )
}

function Section({ id, eyebrow, title, lead, tint, children }: { id?: string; eyebrow: string; title: string; lead?: string; tint?: boolean; children: React.ReactNode }) {
  return (
    <section id={id} className={`scroll-mt-14 ${tint ? "border-y border-neutral-200 bg-neutral-50 dark:border-neutral-800 dark:bg-neutral-900/40" : ""}`}>
      <div className="mx-auto max-w-6xl px-6 py-24">
        <div className="max-w-2xl">
          <div className="text-sm font-medium text-brand">{eyebrow}</div>
          <h2 className="mt-3 text-3xl font-semibold tracking-tight text-balance sm:text-4xl">{title}</h2>
          {lead ? <p className={`mt-4 text-lg ${muted}`}>{lead}</p> : null}
        </div>
        <div className="mt-12">{children}</div>
      </div>
    </section>
  )
}

function NavLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a href={href} className="hidden text-neutral-600 hover:text-neutral-950 sm:block dark:text-neutral-400 dark:hover:text-white">
      {children}
    </a>
  )
}

export default function Home() {
  return (
    <>
      <header className="sticky top-0 z-30 border-b border-neutral-200/70 bg-white/80 backdrop-blur-md dark:border-neutral-800/70 dark:bg-neutral-950/80">
        <nav className="mx-auto flex h-14 max-w-6xl items-center gap-6 px-6 text-sm">
          <a href="#" className="flex items-center gap-2 font-semibold">
            <Logo className="size-6" /> mnemo
          </a>
          <span className="flex-1" />
          <NavLink href="#for-agents">For agents</NavLink>
          <NavLink href="#speed">Speed</NavLink>
          <NavLink href="#topology">Topology</NavLink>
          <a href={DOCS} className="text-neutral-600 hover:text-neutral-950 dark:text-neutral-400 dark:hover:text-white">Docs</a>
          <a href={REPO} className="flex items-center gap-1.5 rounded-lg border border-neutral-200 px-3 py-1.5 font-medium hover:bg-neutral-50 dark:border-neutral-800 dark:hover:bg-neutral-900">
            <GitHubMark /> GitHub
          </a>
        </nav>
      </header>

      <main>
        {/* hero */}
        <section className="relative overflow-hidden">
          <div className="hero-grid pointer-events-none absolute inset-0" />
          <div className="relative mx-auto max-w-6xl px-6 pt-20 pb-12 text-center sm:pt-28">
            <a
              href={`${REPO}/blob/main/CHANGELOG.md`}
              className="inline-flex items-center gap-2 rounded-full border border-neutral-200 bg-white px-3 py-1 text-xs text-neutral-600 hover:border-neutral-300 dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-400"
            >
              <span className="size-1.5 rounded-full bg-brand" /> Open source · MIT · zero dependencies
            </a>
            <h1 className="mx-auto mt-6 max-w-3xl text-5xl font-semibold tracking-tight text-balance sm:text-6xl">
              Memory your agents call as a tool
            </h1>
            <p className={`mx-auto mt-6 max-w-2xl text-lg text-balance ${muted}`}>
              Mnemo indexes every Claude Code, Codex and Pi session on your machines. Any agent can look up what another
              already worked out, on this laptop or a box three hops away, and read only as much as it needs.
            </p>
            <div className="mx-auto mt-10 flex max-w-2xl flex-col gap-3 sm:flex-row">
              <CopyCommand command={INSTALL} className="flex-1 text-left" />
              <a
                href="#install"
                className="flex shrink-0 items-center justify-center gap-1.5 rounded-xl bg-neutral-950 px-5 py-3 text-sm font-medium whitespace-nowrap text-white hover:bg-neutral-800 dark:bg-white dark:text-neutral-950 dark:hover:bg-neutral-200"
              >
                Get started <ArrowRight className="size-4" />
              </a>
            </div>
          </div>
          <div className="relative mx-auto max-w-4xl px-6 pb-10">
            <AgentTranscript />
          </div>
          <div className="relative mx-auto max-w-6xl px-6 pb-16">
            <IntegrationStrip />
          </div>
        </section>

        <div className="mx-auto max-w-6xl px-6">
          <Pillars order={["agent", "speed", "topology"]} />
        </div>

        {/* retrieval efficiency */}
        <Section id="speed" eyebrow="Retrieval efficiency" title="Less digging, same answers." lead="Grep hands an agent gigabytes of unranked JSON to narrow down. Mnemo hands it the few messages that matter, ranked, with the agent, device and time attached.">
          <GrepVsMnemo />
          <p className="mt-3 text-xs text-neutral-500">One common English term over 729 real session files (3.6 GB of logs).</p>
          <div className="mt-16 grid gap-12 lg:grid-cols-[1.3fr_1fr] [&>*]:min-w-0">
            <div>
              <div className="mb-5 text-sm text-neutral-500">Fresh agents answered four real “what did we do back then” questions; both groups got every answer right.</div>
              <Benchmark />
            </div>
            <dl className="grid grid-cols-2 gap-px self-start overflow-hidden rounded-2xl border border-neutral-200 bg-neutral-200 dark:border-neutral-800 dark:bg-neutral-800">
              {STATS.map(([v, k]) => (
                <div key={k} className="bg-white p-6 dark:bg-neutral-950">
                  <dt className="text-2xl font-semibold tracking-tight tabular-nums">{v}</dt>
                  <dd className="mt-1 text-sm text-neutral-500">{k}</dd>
                </div>
              ))}
            </dl>
          </div>
          <a href={`${REPO}/blob/main/docs/benchmarks.md`} className={`mt-8 inline-flex items-center gap-1 text-sm hover:text-neutral-950 dark:hover:text-white ${muted}`}>
            Methodology and caveats (n = 4 tasks) <ArrowRight className="size-3.5" />
          </a>
        </Section>

        {/* agent native */}
        <Section id="for-agents" tint eyebrow="Agent native" title="Built to be called by agents, not read by people." lead="Snippets first, full text on demand: the agent stops reading as soon as it has the answer.">
          <div className="grid gap-12 lg:grid-cols-2 lg:items-start [&>*]:min-w-0">
            <ol className="space-y-5">
              {LOOP.map((s, i) => (
                <li key={s.tool} className="flex gap-4">
                  <span className="grid size-7 shrink-0 place-items-center rounded-full border border-neutral-300 font-mono text-xs dark:border-neutral-700">{i + 1}</span>
                  <div>
                    <div className="flex flex-wrap items-baseline gap-x-3">
                      <code className="font-mono text-sm font-medium">{s.tool}</code>
                      <span className="text-sm text-neutral-500">{s.q}</span>
                    </div>
                    <p className={`mt-1 text-sm leading-relaxed ${muted}`}>{s.body}</p>
                  </div>
                </li>
              ))}
            </ol>
            <div className="space-y-4">
              <CopyCommand command="mnemo setup" />
              <p className={`text-sm leading-relaxed ${muted}`}>
                Registers the MCP server for Claude Code and Codex, links the Claude Code skill and the Pi extension.
                Anything already configured is left alone. The tool schemas cost about 500 tokens per session.
              </p>
              <CopyCommand command='mnemo search "sglang oom" --json' />
              <p className={`text-sm leading-relaxed ${muted}`}>The same search from scripts and any other tool, as JSON.</p>
            </div>
          </div>
        </Section>

        {/* topology */}
        <Section id="topology" eyebrow="Multi-level topology" title="Every machine your agents touch, even the ones you can’t reach." lead="Each device indexes only its own sessions. A search is a message that travels the links you already have, through relays to boxes behind them, and comes back as one ranked list.">
          <div className="grid gap-12 lg:grid-cols-[1.25fr_1fr] lg:items-start [&>*]:min-w-0">
            <Topology />
            <dl className="space-y-6">
              {TOPOLOGY.map(([t, b]) => (
                <div key={t}>
                  <dt className="font-medium">{t}</dt>
                  <dd className={`mt-1 text-sm leading-relaxed ${muted}`}>{b}</dd>
                </div>
              ))}
            </dl>
          </div>
          <div className="mt-10 max-w-2xl space-y-3">
            <CopyCommand command={"mnemo remote add devbox-109\nssh devbox-109 mnemo node --forward on"} />
          </div>
        </Section>

        {/* dashboard */}
        <Section tint eyebrow="Dashboard" title="A window for you, too." lead="mnemo dashboard opens a local UI: the same search across devices, full sessions on a timeline, and a map of every device a search can reach.">
          <div className="grid gap-6 lg:grid-cols-[1.4fr_1fr]">
            <div className="overflow-hidden rounded-2xl border border-neutral-200 shadow-xl shadow-neutral-900/5 dark:border-neutral-800">
              <Themed light="/img/session.png" dark="/img/session-dark.png" alt="Session view with a timeline rail and tool-call durations" className="w-full" />
            </div>
            <div className="self-start overflow-hidden rounded-2xl border border-neutral-200 shadow-xl shadow-neutral-900/5 dark:border-neutral-800">
              <picture>
                <img src={asset("/img/topology.png")} alt="Topology view: the laptop reaches devbox-126 through devbox-109" className="w-full" />
              </picture>
            </div>
          </div>
        </Section>

        {/* install */}
        <Section id="install" eyebrow="Get started" title="One command." lead="Installs mnemo, builds the index and connects every agent it finds. Run it again to upgrade this machine and every device behind it.">
          <div className="grid gap-10 lg:grid-cols-2 [&>*]:min-w-0">
            <div className="space-y-6">
              <CopyCommand command={INSTALL} />
              <ul className={`space-y-2 text-sm ${muted}`}>
                <li>Checks Python 3.7+ and SQLite FTS5, clones to <code className="font-mono text-[13px]">~/mnemo</code>, links <code className="font-mono text-[13px]">mnemo</code> into <code className="font-mono text-[13px]">~/.local/bin</code>.</li>
                <li>Runs <code className="font-mono text-[13px]">mnemo setup</code>: registers the MCP server for Claude Code and Codex, links the skill and the Pi extension.</li>
                <li>On re-run: pulls, then <code className="font-mono text-[13px]">mnemo upgrade</code> backs up and rebuilds the index and updates devices running older code.</li>
              </ul>
              <div>
                <div className="mb-2 text-sm text-neutral-500">Prefer a Python tool manager?</div>
                <CopyCommand command={UV_INSTALL} />
              </div>
            </div>
            <div>
              <div className="mb-3 text-sm text-neutral-500">Or connect agents by hand</div>
              <InstallTabs />
            </div>
          </div>
        </Section>
      </main>

      <footer className="border-t border-neutral-200 dark:border-neutral-800">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-3 px-6 py-10 text-sm text-neutral-500">
          <span className="flex items-center gap-2 font-medium text-neutral-900 dark:text-neutral-100">
            <Logo className="size-5" /> mnemo
          </span>
          <span>MIT licensed</span>
          <span className="flex-1" />
          <a href={DOCS} className="hover:text-neutral-900 dark:hover:text-white">Docs</a>
          <a href={`${REPO}/blob/main/CHANGELOG.md`} className="hover:text-neutral-900 dark:hover:text-white">Changelog</a>
          <a href={REPO} className="hover:text-neutral-900 dark:hover:text-white">GitHub</a>
        </div>
      </footer>
    </>
  )
}
