import { ArrowRight, Boxes, Cpu, Languages, Lock, Network, Search } from "lucide-react"
import { Benchmark } from "@/components/benchmark"
import { CopyCommand } from "@/components/copy-command"
import { InstallTabs } from "@/components/install-tabs"
import { GitHubMark, Logo } from "@/components/logo"
import { Mesh } from "@/components/mesh"
import { DOCS, INSTALL, REPO, UV_INSTALL, asset } from "@/lib/site"

const FEATURES = [
  { icon: Boxes, title: "Every agent, one index", body: "Claude Code, Codex and Pi logs, normalized into one SQLite FTS5 index." },
  { icon: Network, title: "Every machine", body: "Searches fan out over SSH to your devboxes and merge by rank. Sessions stay where they were written." },
  { icon: Search, title: "Built for agents", body: "search → context → session over MCP, a Pi extension or a JSON CLI. Snippets first, full text on demand." },
  { icon: Languages, title: "English and 中文", body: "Prefix matching for code and English, substring matching for CJK, ranked with BM25." },
  { icon: Lock, title: "Local-first", body: "No server, no account. The dashboard binds to 127.0.0.1 behind a per-launch token." },
  { icon: Cpu, title: "Zero dependencies", body: "Python 3.7+ standard library and SQLite. Installs with git clone; searches in ~50 ms." },
]

const LOOP = [
  { tool: "search_sessions", q: "Where is it?", body: "Ranked hits with agent, device, directory, time and a marked snippet." },
  { tool: "get_context", q: "What happened?", body: "The messages around a hit: the prompt, the tool calls, the conclusion." },
  { tool: "get_session", q: "Walk me through it", body: "The whole session, or its head and tail, read on the device that holds it." },
]

function Themed({ light, dark, alt, className }: { light: string; dark: string; alt: string; className?: string }) {
  return (
    <picture>
      <source media="(prefers-color-scheme: dark)" srcSet={asset(dark)} />
      <img src={asset(light)} alt={alt} className={className} />
    </picture>
  )
}

function Section({ id, eyebrow, title, lead, children }: { id?: string; eyebrow: string; title: string; lead?: string; children: React.ReactNode }) {
  return (
    <section id={id} className="mx-auto max-w-6xl scroll-mt-20 px-6 py-24">
      <div className="max-w-2xl">
        <div className="text-sm font-medium text-brand">{eyebrow}</div>
        <h2 className="mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">{title}</h2>
        {lead ? <p className="mt-4 text-lg text-neutral-600 dark:text-neutral-400">{lead}</p> : null}
      </div>
      <div className="mt-12">{children}</div>
    </section>
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
          <a href="#how" className="hidden text-neutral-600 hover:text-neutral-950 sm:block dark:text-neutral-400 dark:hover:text-white">How it works</a>
          <a href="#benchmarks" className="hidden text-neutral-600 hover:text-neutral-950 sm:block dark:text-neutral-400 dark:hover:text-white">Benchmarks</a>
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
          <div className="relative mx-auto max-w-6xl px-6 pt-20 pb-16 text-center sm:pt-28">
            <a
              href={`${REPO}/blob/main/CHANGELOG.md`}
              className="inline-flex items-center gap-2 rounded-full border border-neutral-200 bg-white px-3 py-1 text-xs text-neutral-600 hover:border-neutral-300 dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-400"
            >
              <span className="size-1.5 rounded-full bg-brand" /> Open source · MIT · zero dependencies
            </a>
            <h1 className="mx-auto mt-6 max-w-3xl text-5xl font-semibold tracking-tight text-balance sm:text-6xl">
              One memory for all your coding agents
            </h1>
            <p className="mx-auto mt-6 max-w-2xl text-lg text-balance text-neutral-600 dark:text-neutral-400">
              Mnemo indexes every Claude Code, Codex and Pi session on your laptop and devboxes, so any agent can recall
              what any other one already worked out.
            </p>
            <div className="mx-auto mt-10 flex max-w-2xl flex-col gap-3 sm:flex-row">
              <CopyCommand command={INSTALL} className="flex-1 text-left" />
              <a
                href={`${REPO}#install`}
                className="flex shrink-0 items-center justify-center gap-1.5 rounded-xl bg-neutral-950 px-5 py-3 whitespace-nowrap text-sm font-medium text-white hover:bg-neutral-800 dark:bg-white dark:text-neutral-950 dark:hover:bg-neutral-200"
              >
                Get started <ArrowRight className="size-4" />
              </a>
            </div>
            <div className="mt-6 text-sm text-neutral-500">Works with Claude Code, Codex, Pi and any MCP client</div>
          </div>
          <div className="relative mx-auto max-w-6xl px-6 pb-8">
            <div className="overflow-hidden rounded-2xl border border-neutral-200 shadow-2xl shadow-neutral-900/10 dark:border-neutral-800 dark:shadow-black/40">
              <Themed light="/img/search.png" dark="/img/search-dark.png" alt="Mnemo dashboard searching Claude Code, Codex and Pi sessions" className="w-full" />
            </div>
          </div>
        </section>

        {/* features */}
        <Section eyebrow="Why Mnemo" title="Your agents already remember everything. Separately." lead="Every agent writes its full working memory to disk, but each keeps its own logs on its own machine. Mnemo reads what they already produce; nothing to maintain, no notes to write.">
          <div className="grid gap-px overflow-hidden rounded-2xl border border-neutral-200 bg-neutral-200 sm:grid-cols-2 lg:grid-cols-3 dark:border-neutral-800 dark:bg-neutral-800">
            {FEATURES.map(({ icon: Icon, title, body }) => (
              <div key={title} className="bg-white p-7 dark:bg-neutral-950">
                <Icon className="size-5 text-brand" />
                <h3 className="mt-4 font-medium">{title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-neutral-600 dark:text-neutral-400">{body}</p>
              </div>
            ))}
          </div>
        </Section>

        {/* how agents use it */}
        <Section id="how" eyebrow="How agents use it" title="Ask. The agent looks it up." lead="Three tools, each reading more than the last. The agent stops as soon as it has the answer.">
          <div className="grid gap-10 lg:grid-cols-[1fr_1.1fr] lg:items-start">
            <ol className="space-y-6">
              {LOOP.map((s, i) => (
                <li key={s.tool} className="flex gap-4">
                  <span className="grid size-7 shrink-0 place-items-center rounded-full border border-neutral-300 font-mono text-xs dark:border-neutral-700">{i + 1}</span>
                  <div>
                    <div className="flex flex-wrap items-baseline gap-x-3">
                      <code className="font-mono text-sm font-medium">{s.tool}</code>
                      <span className="text-sm text-neutral-500">{s.q}</span>
                    </div>
                    <p className="mt-1 text-sm leading-relaxed text-neutral-600 dark:text-neutral-400">{s.body}</p>
                  </div>
                </li>
              ))}
            </ol>
            <pre className="overflow-x-auto rounded-2xl bg-neutral-950 p-6 font-mono text-[12.5px] leading-relaxed text-neutral-300 ring-1 ring-neutral-800">
              <span className="text-neutral-500">you    ▸ </span>
              <span className="text-white">test_backoff_is_bounded is failing again. Didn&apos;t we fix this?</span>
              {"\n\n"}
              <span className="text-brand">claude ▸ </span>search_sessions(&quot;backoff flaky&quot;){"\n"}
              <span className="text-neutral-500">{"         "}1 hit · codex · devbox-126 · 2026-09-26{"\n\n"}</span>
              <span className="text-brand">       ▸ </span>get_context(hit, host=&quot;devbox-126&quot;){"\n"}
              <span className="text-neutral-500">{"         "}assert 30.000000000000004 {"<="} 30.0{"\n"}{"         "}fix: clamp after the jitter{"\n\n"}</span>
              <span className="text-brand">claude ▸ </span>
              <span className="text-white">
                Yes. Codex fixed it on devbox-126 on Sep 26;{"\n"}
                {"         "}that patch isn&apos;t on this branch. Apply it?
              </span>
            </pre>
          </div>
        </Section>

        {/* multi-device */}
        <Section eyebrow="Across machines" title="Share memory by passing messages." lead="Each device indexes only its own logs. A search is a message sent to every device; ranked hits come back and merge with Reciprocal Rank Fusion. No central database ever collects your transcripts.">
          <div className="grid gap-10 lg:grid-cols-2 lg:items-center">
            <div className="rounded-2xl border border-neutral-200 p-8 dark:border-neutral-800">
              <Mesh />
            </div>
            <div className="space-y-4">
              <CopyCommand command="mnemo remote add devbox-126" />
              <CopyCommand command='mnemo search "sglang oom"' />
              <p className="text-sm leading-relaxed text-neutral-600 dark:text-neutral-400">
                Remotes install over rsync and answer over your existing SSH trust. Unreachable devices degrade to a
                warning; results from the rest come back complete.
              </p>
            </div>
          </div>
        </Section>

        {/* benchmarks */}
        <Section id="benchmarks" eyebrow="Benchmarks" title="Less digging, same answers." lead="Fresh agents answered four real “what did we do back then” questions, once with Mnemo and once with grep over raw logs. Both got every answer right.">
          <div className="grid gap-12 lg:grid-cols-[1.3fr_1fr]">
            <Benchmark />
            <dl className="grid grid-cols-2 gap-px self-start overflow-hidden rounded-2xl border border-neutral-200 bg-neutral-200 dark:border-neutral-800 dark:bg-neutral-800">
              {[
                ["~50 ms", "search, CLI end to end"],
                ["7 ms", "context on a 44k-line session"],
                ["0.1 s", "incremental sync, idle"],
                ["22%", "index size vs. raw logs"],
              ].map(([v, k]) => (
                <div key={k} className="bg-white p-6 dark:bg-neutral-950">
                  <dt className="text-2xl font-semibold tabular-nums tracking-tight">{v}</dt>
                  <dd className="mt-1 text-sm text-neutral-500">{k}</dd>
                </div>
              ))}
            </dl>
          </div>
          <a href={`${REPO}/blob/main/docs/benchmarks.md`} className="mt-8 inline-flex items-center gap-1 text-sm text-neutral-600 hover:text-neutral-950 dark:text-neutral-400 dark:hover:text-white">
            Methodology and caveats <ArrowRight className="size-3.5" />
          </a>
        </Section>

        {/* dashboard */}
        <Section eyebrow="Dashboard" title="A window for you, too." lead="mnemo dashboard opens a local UI: the same search across devices, full sessions on a timeline, device health.">
          <div className="overflow-hidden rounded-2xl border border-neutral-200 shadow-xl shadow-neutral-900/5 dark:border-neutral-800">
            <Themed light="/img/session.png" dark="/img/session-dark.png" alt="Session view with a timeline rail and tool-call durations" className="w-full" />
          </div>
        </Section>

        {/* install */}
        <Section id="install" eyebrow="Get started" title="One command." lead="Installs mnemo, builds the index and connects every agent it finds. Run it again to upgrade.">
          <div className="grid gap-10 lg:grid-cols-2">
            <div className="space-y-6">
              <CopyCommand command={INSTALL} />
              <ul className="space-y-2 text-sm text-neutral-600 dark:text-neutral-400">
                <li>Checks Python 3.7+ and SQLite FTS5, clones to <code className="font-mono text-[13px]">~/mnemo</code>, links <code className="font-mono text-[13px]">mnemo</code> into <code className="font-mono text-[13px]">~/.local/bin</code>.</li>
                <li>Runs <code className="font-mono text-[13px]">mnemo setup</code>: registers the MCP server for Claude Code and Codex, links the skill and the Pi extension. Anything already configured is left alone.</li>
                <li>On re-run: pulls, then <code className="font-mono text-[13px]">mnemo upgrade</code> backs up and rebuilds the index.</li>
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
