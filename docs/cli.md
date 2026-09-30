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
| `--source` | all | `claude,codex,opencode,pi` (comma-separated) |
| `--kind` | `text,summary,tool_call,tool_result` | Message kinds to match |
| `--all-kinds` | off | Also match `reasoning` |
| `--cwd` | — | Substring of the session's working directory |
| `--since` | — | Only messages on or after this date |
| `--host` | everything reachable | Comma-separated devices or routes (`local`, `devbox-a`, `devbox-a/devbox-b`) |
| `--limit` | 20 | Max hits after merging |
| `--no-sync` | off | Skip the local incremental sync (remotes still sync) |
| `--include-injected` | off | Also match injected boilerplate bodies (workspace instructions, plugin suggestions, approval-review wraps) that are kept but hidden from search |
| `--json` | off | JSON array of hits |

Every device, local and remote, syncs its index incrementally before it is searched, so sessions from a minute ago are found. Warnings about unreachable devices, or a local index that couldn't be refreshed, go to stderr and don't fail the command.

Agents inject boilerplate into user messages — `AGENTS.md` instructions, plugin suggestions, ambient browser state, slash-command output and Codex approval-review wraps. Mnemo keeps these **verbatim** but excludes them from search, ranking and titles, so a keyword that only appears in the injected instructions does not surface every session. Pass `--include-injected` to search the verbatim bodies too. Hits from such a message carry `"envelope": 1`.

## mnemo recent

```bash
mnemo recent [--source S] [--cwd SUBSTR] [--since YYYY-MM-DD] [--limit N] [--json]
```

Lists the most recently started sessions, newest first, each with its first real user prompt as the title, the working directory, start time and message count. Use it to recall recent work without a keyword, or to choose a session before `mnemo session`. Boilerplate-only and approval-review rollouts have no title and are skipped.

## mnemo context

```bash
mnemo context <path> <line> [--before 4] [--after 8] [--host H] [--raw] [--show-envelope] [--json]
```

Prints the messages around a hit. `--host` must be the hit's `host` for remote hits. `--raw` reads the original JSONL with untruncated bodies. By default injected messages render their cleaned text; `--show-envelope` shows the verbatim body instead. Envelope messages are marked `env`.

## mnemo session

```bash
mnemo session <path> [--head N] [--tail N] [--host H] [--raw] [--show-envelope] [--json]
```

Prints every message of one session file in time order. `--show-envelope` shows verbatim injected bodies (otherwise the cleaned text); each envelope message is flagged and its original is available in the JSON as `body`.

## mnemo index

```bash
mnemo index [--source S] [--rebuild] [-v]
```

Incremental sync of local session logs. Files whose size or mtime are unchanged are skipped, and deleted files are dropped from the index. `-v` logs each file. `--rebuild` drops the index and reparses every session from scratch in place; prefer `mnemo upgrade` after updating Mnemo.

## mnemo setup

```bash
mnemo setup [--agent claude,codex,opencode,pi] [--dry-run]
```

Connects every coding agent found on this machine and reports each step as added, fixed, ok, skipped or failed:

| Agent | What it does |
|---|---|
| Claude Code | `claude mcp add --scope user mnemo -- <mnemo> mcp`, and links the skill into `~/.claude/skills/mnemo` |
| Codex | appends `[mcp_servers.mnemo]` to `~/.codex/config.toml` (backed up first) |
| OpenCode | adds `mcp.mnemo` to `opencode.json` (or an existing `opencode.jsonc`) under `$XDG_CONFIG_HOME/opencode`, default `~/.config/opencode` (backed up first). A `.jsonc` with comments is left alone, and the block to add is printed instead |
| Pi | links the extension into `~/.pi/agent/extensions/mnemo.ts` |

Idempotent: already-configured agents are left alone, stale symlinks are repaired, real files are never replaced. Restart running agent sessions afterwards.

## mnemo upgrade

```bash
mnemo upgrade [--no-backup] [--keep 3] [--remotes | --no-remotes] [-v]
mnemo upgrade --list
mnemo upgrade --restore [BACKUP]
```

The safe way to adopt a new index schema after updating Mnemo:

1. Backs up the live index to `~/.mnemo/backups/index-v<schema>-<time>.db` with SQLite's online backup API, keeping the newest `--keep`.
2. Rebuilds into a temporary file beside it, so searches keep working on the old index meanwhile.
3. Verifies the result (schema version, no incomplete rows) and swaps it in with an atomic rename. On any failure the live index is left untouched.
4. Lists `mnemo mcp` / `mnemo dashboard` processes started before the update. They still run the old code; restart the agent sessions that own them.
5. Brings every other device that runs different code up to this code, as `mnemo remote upgrade` does. Unreachable devices are reported and skipped. `--no-remotes` leaves them alone.

`--remotes` instead rsyncs the code to every registered device and runs a full upgrade there, even on devices already current. `--restore` puts back a backup (the newest by default) after saving the current index as a `pre-restore` backup.

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

## mnemo node

```bash
mnemo node [--name NAME] [--forward on|off] [--json]
```

This device's identity and relay policy, stored in `~/.mnemo/node.json`: a stable id (dedupes a device reached over several routes), a display name, and `forward` (off by default) which lets neighbors search and read *through* this device to its own neighbors. Also lists neighbors with the ids learned from them. See [Multi-device](multi-device.md#topologies-direct-links-and-relays).

## mnemo remote

```bash
mnemo remote add <name> [ssh-host] [--bin PATH]   # install over SSH, build index, register
mnemo remote list
mnemo remote upgrade [<route> ...]                # bring devices running other code up to this code
mnemo remote update [<name>]                      # re-sync code and re-index (default: all)
mnemo remote remove <name>                        # unregister; leaves files on the device
```

`ssh-host` defaults to `name` and can be any alias from `~/.ssh/config`. `--bin` sets the remote launcher path (default `~/mnemo/bin/mnemo`). See [Multi-device](multi-device.md).

`remote upgrade` compares code fingerprints (`mnemo node` shows this device's) and updates only the devices that differ: every device reachable, including ones behind relays, or just the given routes, e.g. `devbox-a/devbox-b`. Each gets the code by rsync, then an incremental sync, or a backed-up rebuild if the index schema changed.

## mnemo link

```bash
mnemo link <remote> --allow-inbound [--install]
mnemo link <remote> --uninstall
mnemo link --list [--json]
```

Keeps an SSH session open to a registered remote so that it can search and read this device's sessions, for devices that cannot connect back here (a laptop behind NAT or VPN). The remote sees this device as a neighbor named after it (`mnemo node --name`). Over the link it may only run read-only commands, and this device's relay policy applies. Runs in the foreground and reconnects on its own; stop it to revoke. `--install` runs it as a launchd agent (macOS) or systemd user unit (Linux) that starts at login instead (`MNEMO_SERVICE_MANAGER=background` forces a plain background process), `--uninstall` stops and removes it, and `--list` shows each link's state: `connected`, `connecting`, `retrying` (with the reason), `refused`, `stopped` or `off`. See [Multi-device](multi-device.md#links-that-only-work-one-way).

## mnemo mcp

Runs the stdio MCP server (JSON-RPC, zero dependencies). It syncs the local index at startup, then serves `search_sessions` (which syncs again before each search; pass `include_injected` to also match boilerplate), `list_recent_sessions`, `get_context`, `get_session` and `reindex`.

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
