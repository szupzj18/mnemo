<div align="center">

# Mnemo

**One memory for all your coding agents.**

Search every Claude Code, Codex, and Pi session you've ever run, on this machine and your remote devboxes, from inside any of them.

[![Python 3.7+](https://img.shields.io/badge/python-3.7%2B-3776AB?logo=python&logoColor=white)](#install)
[![Zero dependencies](https://img.shields.io/badge/dependencies-0-2ea44f)](#install)
[![SQLite FTS5](https://img.shields.io/badge/SQLite-FTS5-003B57?logo=sqlite&logoColor=white)](docs/architecture.md)
[![MCP](https://img.shields.io/badge/MCP-server-8A2BE2)](docs/agent-tools.md)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

English · [简体中文](README.zh-CN.md)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/search-dark.png">
  <img alt="Mnemo dashboard: one search across Claude Code, Codex and Pi sessions" src="docs/assets/search.png" width="880">
</picture>

</div>

## Why Mnemo

Every coding agent already writes its full working memory to disk: prompts, reasoning, tool calls, tool output. But each agent keeps its own logs, and each machine keeps its own. The result is a set of islands:

- Claude Code can't see what you worked out in Codex yesterday.
- The agent on your laptop can't see last week's session on the devbox.
- Handoff notes and shared `MEMORY.md` files only help if every agent keeps them up to date, and they don't.

Mnemo indexes the logs your agents **already produce** and gives every agent the same three tools to search and read them. Your agents don't have to change how they work, and you don't write any notes.

```text
you ▸ how did we fix the flaky backoff test last week?

agent ▸ search_sessions("backoff flaky")
        → [codex · devbox-126 · 09-26] FAILED test_backoff_is_bounded - assert 30.000000000000004 <= 30.0
      ▸ get_context(hit, host="devbox-126")
        → The bound was a float rounding issue; fixed by clamping min(cap, …) after the jitter …
```

## Highlights

- **Cross-agent.** A single index covers Claude Code, Codex (including archived sessions) and Pi, all normalized to one message schema.
- **Cross-device.** Queries fan out over SSH to your devboxes and merge by rank. Session bodies never leave the machine that produced them.
- **Built for agents.** The three agent tools are search → context → full session, available over MCP, as a Pi extension, and through a CLI with `--json`.
- **Good at CJK.** English uses prefix matching and Chinese uses substring matching (unigram + bigram), all ranked with BM25.
- **Fast and small.** Searches take about 50–100 ms, an idle incremental sync about 0.1 s, and the index is ~22% of raw log size.
- **Zero dependencies.** Mnemo needs only the Python 3.7+ standard library and SQLite FTS5. It installs with `git clone` and needs no daemon.
- **Local dashboard.** A browser UI for search, reading sessions on a timeline, and managing devices. It binds to `127.0.0.1` and is token-gated.

## Install

```bash
git clone https://github.com/szupzj18/mnemo.git ~/mnemo
ln -s ~/mnemo/bin/mnemo ~/.local/bin/mnemo    # any directory on PATH
mnemo index                                   # first full index; later runs are incremental
mnemo search "retry budget"
```

Then connect your agents:

<details open>
<summary><b>Claude Code</b></summary>

```bash
claude mcp add --scope user mnemo -- mnemo mcp
```

For better triggering, also install the skill. It tells the agent *when* to reach for past sessions:

```bash
ln -s ~/mnemo/integrations/skills/mnemo ~/.claude/skills/mnemo
```
</details>

<details>
<summary><b>Codex</b></summary>

`~/.codex/config.toml`:

```toml
[mcp_servers.mnemo]
command = "/Users/you/.local/bin/mnemo"   # absolute path
args = ["mcp"]
startup_timeout_sec = 120
```
</details>

<details>
<summary><b>Pi</b></summary>

```bash
ln -s ~/mnemo/integrations/pi/mnemo.ts ~/.pi/agent/extensions/mnemo.ts
```

The extension calls the `mnemo` CLI. It looks for `~/mnemo/bin/mnemo` first, then `PATH`, and `MNEMO_BIN` overrides both.
</details>

<details>
<summary><b>Any other MCP client</b></summary>

Register the stdio command `mnemo mcp`. For agents that support skills but not MCP, symlink `integrations/skills/mnemo/` into their skills directory. The skill uses the CLI.
</details>

See [Getting started](docs/getting-started.md) for keeping the index fresh and troubleshooting.

## What your agent gets

| Tool (MCP) | Pi | CLI | Purpose |
|---|---|---|---|
| `search_sessions` | `search_sessions` | `mnemo search` | Keyword search (AND) across all agents and devices; filter by agent, device, directory, date, message kind |
| `get_context` | `get_session_context` | `mnemo context` | Messages around a hit, to see what led up to it and what followed |
| `get_session` | `get_full_session` | `mnemo session` | The whole session, with `head`/`tail` for skimming and `raw` for untruncated bodies |
| `reindex` | — | `mnemo index` | Incremental sync of local logs |

Every hit carries `host`, `source`, `cwd`, `ts`, `role`, `kind`, `snippet`, `path` and `lineno`. Pass the hit's `host` back to `get_context` and `get_session`, and Mnemo runs the read on the device that holds the data. Full reference: [Agent tools](docs/agent-tools.md) · [CLI](docs/cli.md).

## Dashboard

```bash
mnemo dashboard
```

Click any search hit to open the full session as a chat thread. Matches are highlighted and you can jump between them. Tool calls fold away. A timeline rail marks each turn, idle gaps, day changes and how long each tool call took.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/session-dark.png">
  <img alt="Session view with a timeline rail, highlighted matches and tool-call durations" src="docs/assets/session.png" width="880">
</picture>

The dashboard also covers index stats, per-device health, device add/update/remove and search diagnostics (latency and hits per device). It has light and dark themes.

## Multiple machines

```bash
mnemo remote add devbox-126          # rsync-installs mnemo over SSH and builds its index
mnemo search "sglang oom"            # now searches local + devbox-126 in parallel
```

```mermaid
flowchart LR
  A["Agent on laptop"] -->|search| L["mnemo (laptop)"]
  L -->|local query| LI[("laptop index")]
  L -->|ssh: mnemo search --host local| R1["mnemo (devbox-109)"]
  L -->|ssh: mnemo search --host local| R2["mnemo (devbox-126)"]
  R1 --> I1[("devbox-109 index")]
  R2 --> I2[("devbox-126 index")]
  R1 -. ranked hits .-> L
  R2 -. ranked hits .-> L
  L -->|RRF merge| A
```

The design is **message passing, not shared storage**. Each machine indexes only its own logs, a search is a message sent to every device, and results come back as ranked hits merged with Reciprocal Rank Fusion. `context` and `session` reads are routed to the device that holds the session, so no central database collects everyone's transcripts. To form a mesh, run `remote add` on each machine. Unreachable devices are skipped with a warning.

Details: [Multi-device](docs/multi-device.md).

## Benchmarks

Measured on a real corpus of 730 sessions and 149,678 messages (3.6 GB of logs):

| | |
|---|---|
| Full index build | 28.9 s (one-time) |
| Incremental sync, nothing changed | 0.07–0.13 s |
| Search (CLI end-to-end) | 46–106 ms |
| `context` lookup, largest session | 7 ms (vs 316 ms re-parsing JSONL) |
| Index size | 784 MB (21.8% of raw) |

In a controlled experiment, fresh agents answered four "what did we do back then" questions, once with Mnemo and once with only `grep`/`rg` over the raw logs. Both groups got every answer right. With Mnemo they used **23% fewer tokens, 52% fewer tool calls and 40% less wall time**. The gain grows with the size of the search space. When the answer sat in a small, guessable directory, plain grep was just as good. Methodology and caveats: [Benchmarks](docs/benchmarks.md).

## Privacy & security

- Everything runs locally. Mnemo makes no network calls except SSH to devices you registered yourself.
- Session bodies are read on the device that produced them. A cross-device query returns only the hits and the messages you asked to read.
- The dashboard listens on `127.0.0.1` only. API calls require a random per-launch token and a local `Host` header.
- The index at `~/.mnemo/index.db` holds your transcripts in plaintext. Treat it like the logs themselves.

See [SECURITY.md](SECURITY.md) to report a vulnerability.

## Documentation

| | |
|---|---|
| [Getting started](docs/getting-started.md) | Install, connect each agent, keep the index fresh |
| [Agent tools](docs/agent-tools.md) | MCP / Pi tool schemas and the search → context → session workflow |
| [CLI reference](docs/cli.md) | Every command and flag |
| [Multi-device](docs/multi-device.md) | Remotes, mesh setup, SSH and Kerberos notes |
| [Architecture](docs/architecture.md) | Index schema, CJK matching, sync, federation |
| [Benchmarks](docs/benchmarks.md) | Latency, token cost, end-to-end experiment |

## Roadmap

- [ ] More agents: Gemini CLI (bodies live in protobuf SQLite blobs), Cursor, OpenCode
- [ ] Hybrid semantic search (`sqlite-vec` + local embeddings) alongside keyword search
- [ ] Length caps on `tool_result` in `context` responses to bound per-call token cost
- [ ] External-content FTS table with compressed bodies (~30–40% smaller index)
- [ ] Auto-sync the local index before CLI / Pi searches

## Contributing

Issues and PRs are welcome, especially new agent adapters (each is about 100 lines). Start with [CONTRIBUTING.md](CONTRIBUTING.md). Coding agents working in this repo should read [AGENTS.md](AGENTS.md).

## License

[MIT](LICENSE)
