# Agent tools

Mnemo gives agents three tools that form one workflow:

```text
search_sessions  ──▶  get_context  ──▶  get_session
 find the hit         read around it     read the whole thing
```

The same tools are exposed over MCP (`mnemo mcp`), as a Pi extension, and as CLI commands with `--json`. The payloads are identical. Only the names differ slightly:

| MCP | Pi | CLI |
|---|---|---|
| `search_sessions` | `search_sessions` | `mnemo search … --json` |
| `get_context` | `get_session_context` | `mnemo context <path> <line> --json` |
| `get_session` | `get_full_session` | `mnemo session <path> --json` |
| `reindex` | — | `mnemo index` |

## search_sessions

Full-text search over user prompts, assistant replies, summaries, tool calls and tool results, across every agent on every registered device.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `query` | string | required | Whitespace-separated keywords, all of which must match. English matches word prefixes (`refact` → `refactoring`); Chinese matches substrings (`订阅` → `订阅支出`). |
| `source` | string | all | Comma-separated subset of `claude,codex,pi` |
| `kinds` | string | `text,summary,tool_call,tool_result` | Add `reasoning` to search model reasoning too. The CLI flags are `--kind` and `--all-kinds`. |
| `cwd` | string | — | Substring of the session's working directory, e.g. `infra` |
| `since` | string | — | `YYYY-MM-DD` |
| `host` | string | all devices | Comma-separated. The MCP tool description lists the registered device names. |
| `limit` | integer | 20 | Capped at 100 over MCP |

Each hit:

```json
{
  "host": "local",
  "source": "codex",
  "cwd": "/home/alex/relay",
  "ts": "2026-09-24T07:40:26Z",
  "role": "assistant",
  "kind": "text",
  "snippet": "All 27 tests pass. The [[budget]] is configured through `RELAY_[[RETRY]]_[[BUDGET]]` …",
  "path": "/home/alex/.codex/sessions/2026/09/24/rollout-2026-09-24T15-40-00-923bbc54….jsonl",
  "lineno": 11,
  "session_id": "923bbc54-87c7-4d09-b28d-6854976be73c",
  "rank": -3.747
}
```

Matched terms are wrapped in `[[…]]`. Results from several devices are merged by Reciprocal Rank Fusion. If a device is unreachable, the response ends with an `unreachable devices` note, and hits from the other devices are still complete.

## get_context

Returns the indexed messages around one hit. Every message in the window has the same shape, and `hit: true` marks the matched line.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `path` | string | required | From the hit |
| `line` | integer | required | The hit's `lineno` |
| `host` | string | `local` | **Pass the hit's `host`.** The read runs on that device. |
| `before` / `after` | integer | 4 / 8 | Window size in messages |
| `raw` | boolean | false | Read the original JSONL instead of the index (no 20k cap) |

```json
[
  {"lineno": 10, "ts": "2026-09-24T07:40:24Z", "role": "tool", "kind": "tool_result", "text": "27 passed in 6.02s", "hit": false},
  {"lineno": 11, "ts": "2026-09-24T07:40:26Z", "role": "assistant", "kind": "text", "text": "All 27 tests pass. …", "hit": true}
]
```

## get_session

Returns every indexed message of the session file, ordered by time, along with session metadata.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `path` | string | required | From the hit |
| `host` | string | `local` | Pass the hit's `host` |
| `head` / `tail` | integer | — | Only the first or last N messages, for skimming long sessions |
| `raw` | boolean | false | Full bodies from the original JSONL |

```json
{
  "path": "…", "source": "codex", "session_id": "…", "cwd": "/home/alex/relay",
  "started_at": "2026-09-24T07:40:00Z", "ended_at": "2026-09-24T07:40:26Z",
  "count": 10,
  "messages": [{"lineno": 2, "ts": "…", "role": "user", "kind": "text", "text": "…"}]
}
```

## reindex

Incrementally re-scans local session files. It costs about 0.1 s when nothing changed, and it accepts an optional `source` filter. `search_sessions` already syncs before every search, so agents rarely need this; it is kept for forcing a sync explicitly.

## Message schema

Every agent's log format is normalized into:

| Field | Values |
|---|---|
| `role` | `user`, `assistant`, `tool` |
| `kind` | `text`, `summary` (compaction summaries), `reasoning`, `tool_call` (`name(args-json)`), `tool_result` |
| `ts` | ISO-8601 UTC |
| `text` | Body, capped at 20,000 characters in the index (`…[truncated]`) |

Injected environment context, progress events and other noise are filtered at parse time.

## Usage guidance for agents

These rules are baked into the bundled skill (`integrations/skills/mnemo/SKILL.md`), and they are good defaults for your own prompts:

- **Search first, read second.** Snippets are short. Call `get_context` before quoting a detail or reusing code from a hit.
- **Prefer `context` over `session`.** Pull whole sessions only when you need the full arc, and skim with `head`/`tail` first.
- **Always pass the hit's `host` back.** Remote content can only be read on the device that holds it.
- **Don't use it for the current conversation.** That's already in context.
- **Summarize, don't forward.** Past sessions can contain secrets, so quote only what the user needs.

## Token cost

Measured on a real corpus (see [Benchmarks](benchmarks.md)):

| Call | Approx. tokens |
|---|---|
| `search_sessions`, limit 5 / 10 / 20 | ~800 / ~1,600 / ~3,300 |
| One round of search(10) + 2 × context | ~7,000 median |
| MCP tool schemas (always loaded) | ~500 |
| Skill description (always loaded) / full skill (on trigger) | ~130 / ~755 |

A `context` window that lands on a large tool output can reach ~18k tokens. In that case use smaller `before`/`after` values.
