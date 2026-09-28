"use client"

import { useState } from "react"
import { CopyCommand } from "./copy-command"

const TABS = [
  {
    id: "claude",
    label: "Claude Code",
    steps: [
      { note: "Register the MCP server", cmd: "claude mcp add --scope user mnemo -- mnemo mcp" },
      { note: "Teach it when to look back (skill)", cmd: "ln -s ~/mnemo/integrations/skills/mnemo ~/.claude/skills/mnemo" },
    ],
  },
  {
    id: "codex",
    label: "Codex",
    steps: [
      {
        note: "Add to ~/.codex/config.toml",
        cmd: '[mcp_servers.mnemo]\ncommand = "/Users/you/.local/bin/mnemo"\nargs = ["mcp"]',
      },
    ],
  },
  {
    id: "pi",
    label: "Pi",
    steps: [{ note: "Link the extension", cmd: "ln -s ~/mnemo/integrations/pi/mnemo.ts ~/.pi/agent/extensions/mnemo.ts" }],
  },
  {
    id: "any",
    label: "Any MCP client",
    steps: [{ note: "Register the stdio server", cmd: "mnemo mcp" }],
  },
]

export function InstallTabs() {
  const [active, setActive] = useState(TABS[0].id)
  const tab = TABS.find((t) => t.id === active)!
  return (
    <div className="rounded-2xl border border-neutral-200 p-2 dark:border-neutral-800">
      <div role="tablist" className="flex flex-wrap gap-1 p-1">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={t.id === active}
            onClick={() => setActive(t.id)}
            className={`rounded-lg px-3 py-1.5 text-sm transition ${
              t.id === active
                ? "bg-neutral-100 font-medium text-neutral-950 dark:bg-neutral-800 dark:text-white"
                : "text-neutral-500 hover:text-neutral-900 dark:hover:text-white"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div role="tabpanel" className="space-y-4 px-3 pt-3 pb-4">
        {tab.steps.map((s) => (
          <div key={s.cmd}>
            <div className="mb-2 text-sm text-neutral-500">{s.note}</div>
            <CopyCommand command={s.cmd} />
          </div>
        ))}
      </div>
    </div>
  )
}
