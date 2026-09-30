// An agent recalling work done on a device it can only reach through a relay.
// Illustrative session.
export function AgentTranscript() {
  return (
    <div className="overflow-hidden rounded-2xl bg-neutral-950 text-left ring-1 ring-neutral-800">
      <div className="flex items-center gap-1.5 border-b border-neutral-800 px-4 py-2.5">
        {[0, 1, 2].map((i) => <span key={i} className="size-2.5 rounded-full bg-neutral-700" />)}
        <span className="ml-3 text-xs text-neutral-500">claude code · laptop</span>
      </div>
      <pre className="overflow-x-auto p-6 font-mono text-[12px] leading-[1.75] text-neutral-300">
        <span className="text-neutral-500">you    ▸ </span>
        <span className="text-white">sglang OOMs on the GPU box again. What did we change last time?</span>
        {"\n\n"}
        <Call n="search_sessions" args={'"sglang oom"'} />
        <Dim>{"           3 hits · codex · devbox-a/gpu-box · Sep 22\n"}</Dim>
        <Dim>{"                    claude · local · Sep 18\n\n"}</Dim>
        <Call n="get_context" args={'hit 1, host="devbox-a/gpu-box"'} />
        <Dim>{"           --mem-fraction-static 0.88 → 0.80\n"}</Dim>
        <Dim>{"           \"OOM gone at batch 64; throughput −3%\"\n\n"}</Dim>
        <span className="text-brand">claude ▸ </span>
        <span className="text-white">
          Codex lowered --mem-fraction-static to 0.80 on gpu-box on Sep 22{"\n"}
          {"         "}(reached through devbox-a). Your launch script still says 0.88.{"\n"}
          {"         "}Want me to apply the same change?
        </span>
      </pre>
    </div>
  )
}

function Call({ n, args }: { n: string; args: string }) {
  return (
    <>
      <span className="text-brand">claude ▸ </span>
      <span className="text-sky-300">{n}</span>({args}){"\n"}
    </>
  )
}

function Dim({ children }: { children: React.ReactNode }) {
  return <span className="text-neutral-500">{children}</span>
}

export const INTEGRATIONS = [
  { name: "Claude Code", how: "MCP server + skill" },
  { name: "Codex", how: "MCP server" },
  { name: "Pi", how: "extension" },
  { name: "OpenCode", how: "MCP server" },
  { name: "Any MCP client", how: "stdio JSON-RPC" },
  { name: "Scripts", how: "mnemo search --json" },
]

export function IntegrationStrip() {
  return (
    <div className="flex flex-wrap justify-center gap-x-8 gap-y-3 text-sm">
      {INTEGRATIONS.map((i) => (
        <span key={i.name} className="text-neutral-500">
          <span className="font-medium text-neutral-900 dark:text-neutral-100">{i.name}</span> · {i.how}
        </span>
      ))}
    </div>
  )
}
