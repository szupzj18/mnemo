# CLI reference

```text
mnemo <command> [options]
mnemo --version
```

All read commands accept `--json` for machine-readable output. The JSON shapes are documented in [Agent tools](agent-tools.md).

## mnemo search

```bash
mnemo search <keywords…> [--source S] [--kind K | --all-kinds] [--cwd SUBSTR]
                         [--since YYYY-MM-DD] [--host H] [--limit N] [--json]
```

Alias: `mnemo query`.

| Option | Default | Description |
|---|---|---|
| `<keywords…>` | — | All keywords must match (AND). English matches word prefixes; Chinese matches substrings. |
| `--source` | all | `claude,codex,pi` (comma-separated) |
| `--kind` | `text,summary,tool_call,tool_result` | Message kinds to match |
| `--all-kinds` | off | Also match `reasoning` |
| `--cwd` | — | Substring of the session's working directory |
| `--since` | — | Only messages on or after this date |
| `--host` | local + all remotes | Comma-separated device names; `local` means this machine |
| `--limit` | 20 | Max hits after merging |
| `--json` | off | JSON array of hits |

Warnings about unreachable devices go to stderr and don't fail the command. Remote devices re-index incrementally before each search. The local index doesn't, so run `mnemo index` first if you need the latest sessions.

## mnemo context

```bash
mnemo context <path> <line> [--before 4] [--after 8] [--host H] [--raw] [--json]
```

Prints the messages around a hit. `--host` must be the hit's `host` for remote hits. `--raw` reads the original JSONL with untruncated bodies.

## mnemo session

```bash
mnemo session <path> [--head N] [--tail N] [--host H] [--raw] [--json]
```

Prints every message of one session file in time order.

## mnemo index

```bash
mnemo index [--source S] [-v]
```

Incremental sync of local session logs. Files whose size or mtime are unchanged are skipped, and deleted files are dropped from the index. `-v` logs each file.

## mnemo status

```bash
mnemo status [--json]
```

Shows session and message counts per agent, the last sync time and the database path.

```text
claude       2 sessions        21 messages
codex        2 sessions        17 messages
pi           1 sessions         7 messages
last sync: 2026-09-28 12:07:00
db:       /home/alex/.mnemo/index.db
```

## mnemo remote

```bash
mnemo remote add <name> [ssh-host] [--bin PATH]   # install over SSH, build index, register
mnemo remote list
mnemo remote update [<name>]                      # re-sync code and re-index (default: all)
mnemo remote remove <name>                        # unregister; leaves files on the device
```

`ssh-host` defaults to `name` and can be any alias from `~/.ssh/config`. `--bin` sets the remote launcher path (default `~/mnemo/bin/mnemo`). See [Multi-device](multi-device.md).

## mnemo mcp

Runs the stdio MCP server (JSON-RPC, zero dependencies). It syncs the local index at startup and then serves `search_sessions`, `get_context`, `get_session` and `reindex`.

## mnemo dashboard

```bash
mnemo dashboard [--port 7787] [--no-open]
```

Starts the web UI on `127.0.0.1`. If the port is busy, the next free port up to +9 is used. A random token is generated per launch and embedded in the page, and every API call must carry it.

| View | What it does |
|---|---|
| Dashboard | Index stats, last sync, per-device health and per-agent message counts |
| Search | Federated search with per-device latency and hit counts. Click a hit to open its session. |
| Session | Chat-style transcript with highlighted matches, prev/next match navigation, collapsible reasoning and tool blocks, timeline rail (turns, idle gaps, day dividers, tool durations), raw mode |
| Devices | Add / test / sync / update / remove remotes |
| Logs | Operation log for the current dashboard session |
