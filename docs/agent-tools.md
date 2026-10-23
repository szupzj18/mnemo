# Agent tools

Mnemo gives agents three tools that form one workflow:

```text
search_sessions  ──▶  get_context  ──▶  get_session
 find the hit         read around it     read the whole thing
```

The same tools are exposed over MCP (`mnemo mcp`), as a Pi extension, and as CLI commands with `--json`. The payloads are identical. Only the names differ slightly:

| MCP | Pi | CLI |
|---|---|---|
| `search_sessions` | `search_sessions` | `mnemo search … --json --coverage` |
| `get_context` | `get_session_context` | `mnemo context <path> <line> --json` |
| `get_session` | `get_full_session` | `mnemo session <path> --json` |
| `list_recent_sessions` | — | `mnemo recent --json` |
| `reindex` | — | `mnemo index` |

Over MCP, `search_sessions`, `get_context`, `get_session` and `list_recent_sessions` are annotated `readOnlyHint: true`, so clients that ask before running a tool (Codex does) skip the prompt for them; that is what lets an unattended run such as `codex exec`, where approvals are off and a prompt cancels the call, search past sessions. `reindex` writes the local index and is annotated as a non-destructive write.

## search_sessions

Returns `{hits, coverage, warnings, excluded_sessions}` over MCP/Pi. `coverage` lists devices with `status` (`searched`, `failed`, `skipped`), refresh state (`synced`, `recent`, `not_requested`, `failed`, `unknown`), device-local sync warnings and the indexed-body cap. A searched device can have an incomplete index refresh; older devices report unknown freshness. Plain CLI `--json` remains an array unless `--coverage` is added.

Full-text search over user prompts, assistant replies, summaries, tool calls and tool results, across every agent on every registered device.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `query` | string | required | Whitespace-separated keywords, all of which must match. English matches word prefixes (`refact` → `refactoring`); Chinese matches substrings (`订阅` → `订阅支出`). |
| `source` | string | all | Comma-separated subset of `claude,codex,opencode,pi,agy,grok` |
| `kinds` | string | `text,summary,tool_call,tool_result` | Add `reasoning` to search model reasoning too. The CLI flags are `--kind` and `--all-kinds`. |
| `cwd` | string | — | Substring of the session's working directory, e.g. `infra` |
| `since` | string | — | `YYYY-MM-DD` |
| `host` | string | all devices | Comma-separated. The MCP tool description lists the registered device names. |
| `limit` | integer | 20 | Capped at 100 over MCP |
| `include_injected` | boolean | false | Also match injected boilerplate (workspace instructions, plugin suggestions, slash-command output, Codex approval-review wraps). See [Message schema](#message-schema). |
| `exclude_session_ids` | string[] | — | Session ids to leave out. CLI: `--exclude-session ID` (repeatable or comma-separated). |
| `include_current_session` | boolean | false | Keep hits from the session making the call, which are left out by default (see below). CLI: `--include-current`. |

**The caller's own session is left out.** Otherwise a search for something just said in the conversation returns that conversation. Mnemo takes the caller's session id from what each agent provides:

| Agent | Where the id comes from |
|---|---|
| Claude Code | `CLAUDE_CODE_SESSION_ID`, set for shell commands and MCP servers |
| Codex | `threadId`/`sessionId` in each MCP call's `_meta`; `CODEX_THREAD_ID`/`CODEX_SESSION_ID` for shell commands. `CODEX_SESSION_ID` is the root session, so a subagent's search also leaves out its parent thread. |
| Pi | the extension passes its current session id |

`excluded_sessions` in the response lists what was left out. Claude Code's MCP servers keep the id they were started with, so after `/clear` the earlier conversation is excluded instead of the new one; pass `exclude_session_ids` when that matters. Neighbors are not sent the ids (older builds would reject them); their hits are filtered on return, so a device can contribute fewer than `limit`.

Hits carry a short snippet (about 18 tokens around the match), never the message body; read bodies with `get_context`/`get_session`.

Each hit:

```json
{
  "host": "local",
  "source": "codex",
  "account": ".codex",
  "cwd": "/home/alex/relay",
  "ts": "2026-09-24T07:40:26Z",
  "role": "assistant",
  "kind": "text",
  "snippet": "All 27 tests pass. The [[budget]] is configured through `RELAY_[[RETRY]]_[[BUDGET]]` …",
  "path": "/home/alex/.codex/sessions/2026/09/24/rollout-2026-09-24T15-40-00-923bbc54….jsonl",
  "lineno": 11,
  "session_id": "923bbc54-87c7-4d09-b28d-6854976be73c",
  "envelope": 0,
  "rank": -3.747,
  "archived": false
}
```

`account` is the agent config dir the session came from (`.claude`, `.claude-work`, `.codex-team`, `.gemini` for Antigravity CLI, `.grok` for Grok CLI, …; `null` for OpenCode). `archived` is `true` when the agent has deleted the original log and Mnemo reads its archived copy; `get_context` and `get_session` work on it the same way, with the hit's `path`.

`envelope` is `1` only when the matched text is injected boilerplate (possible with `include_injected`); the snippet then shows cleaned text and the verbatim body is available under `body`.

Matched terms are wrapped in `[[…]]`. Results from several devices are merged by Reciprocal Rank Fusion. If a device is unreachable, `warnings` and its `coverage` entry report the failure. Answering devices cover their indexed bodies and filters; source warnings, unknown freshness and clipped tails limit absence claims.

## get_context

Returns the indexed messages around one hit. Every message in the window has the same shape, and `hit: true` marks the matched line.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `path` | string | required | From the hit |
| `line` | integer | required | The hit's `lineno` |
| `host` | string | `local` | **Pass the hit's `host`.** The read runs on that device. |
| `before` / `after` | integer | 4 / 8 | Window size in messages |
| `raw` | boolean | false | Read the original JSONL instead of the index (no 20k cap). Implies `full`. |
| `full` | boolean | false | Return whole message bodies instead of clipping them |
| `max_chars` | integer | 2000 | Clip each message to this many characters; `0` returns whole bodies. Takes precedence over `full` and `raw`. |

Each message body longer than 2,000 characters is clipped and ends in `… [clipped: 2000 of N chars; full=true for the whole message]`. The row also gets `"clipped": true` and `"chars": N` (`body_chars` for a mixed envelope's verbatim `body`). Pass `full: true` to read the whole window, or read one session with `get_session`. On 300 random windows from a real index, the default cut the median window from 30k to 7.5k characters and the 90th percentile from 71k to 11k. Messages under the limit, including about 99% of user and assistant text, are returned unchanged.

```json
[
  {"lineno": 10, "ts": "2026-09-24T07:40:24Z", "role": "tool", "kind": "tool_result", "text": "27 passed in 6.02s", "hit": false},
  {"lineno": 11, "ts": "2026-09-24T07:40:26Z", "role": "assistant", "kind": "text", "text": "All 27 tests pass. …", "hit": true}
]
```

Messages whose body contained injected boilerplate carry `"envelope": 1`. A mixed message (boilerplate plus a real reply) shows the cleaned `text` and also exposes the verbatim original under `body`; a boilerplate-only message renders its verbatim text as-is. The CLI `--show-envelope` flag prints verbatim bodies in text mode.

## get_session

Use `limit` (1–500, typically 100) with optional `anchor_line` for the first page, then pass `page.next_cursor` or `page.previous_cursor` as `cursor`. `count` remains the total; `messages` contains only the page. `head`/`tail` are mutually exclusive and cannot be combined with pagination. Raw paging limits returned messages but still parses the full source. See [retrieval](retrieval.md) for cursor and coverage semantics.

Returns every indexed message of the session file, ordered by time, along with session metadata.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `path` | string | required | From the hit |
| `host` | string | `local` | Pass the hit's `host` |
| `head` / `tail` | integer | — | Only the first or last N messages, for skimming long sessions |
| `raw` | boolean | false | Full bodies from the original JSONL. Raw rows return the verbatim stored text (`text` plus `envelope`). |

```json
{
  "path": "…", "source": "codex", "session_id": "…", "cwd": "/home/alex/relay",
  "started_at": "2026-09-24T07:40:00Z", "ended_at": "2026-09-24T07:40:26Z",
  "count": 10,
  "account": ".codex", "archived": false,
  "messages": [{"lineno": 2, "ts": "…", "role": "user", "kind": "text", "text": "…"}]
}
```

An archived session also gives `archive_path`, the copy that `raw` reads.

## list_recent_sessions

Lists recently started sessions newest-first, titled with the first real user prompt (boilerplate stripped), so "what have I been working on" needs no keyword.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `source` | string | all | Comma-separated subset of `claude,codex,opencode,pi,agy,grok` |
| `cwd` | string | — | Substring of the working directory |
| `since` | string | — | `YYYY-MM-DD` |
| `limit` | integer | 25 | Capped at 100 over MCP |

```json
[
  {"source": "codex", "cwd": "/home/alex/relay",
   "started_ts": "2026-09-24T07:40:00Z", "title": "Fix flaky backoff test",
   "session_id": "923bbc54-…", "messages": 10,
   "path": "/home/alex/.codex/sessions/…/rollout-….jsonl"}
]
```

Sessions that contain only injected boilerplate (e.g. Codex approval-review rollouts) have no title and are skipped. This tool reads the local device only; run it on the devbox itself (or over SSH) to list that machine's sessions.

## list_skills

The agent skills installed on this device (and on others with `host`), as `mnemo skill ls` and `mnemo skill search` show them.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `query` | string | — | Return the skills whose name or `SKILL.md` description contains every word, instead of the whole list |
| `host` | string | `local` | `local`, `all`, or comma-separated device names |

Without `query`: `{devices: {local: {dirs, skills}}, issues, warnings}`. Each skill has `name`, `agent`, `account`, `dir`, `kind` (`link`, `dir` or `dangling`), `target` for links, `hash` and `desc`. `issues` lists `dangling` links and `forked` names (copies with different content). With `query`: `{hits: [{skill, desc, where}], warnings}`.

## skill_usage

How often indexed sessions used each skill, as `mnemo skill stats --json` reports it.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `since` | string | `30d` | `Nd` or `YYYY-MM-DD` |
| `by` | string | `device,agent` | Group by `device` and/or `agent`; always by skill |
| `host` | string | `all` | `local`, `all`, or comma-separated device names |
| `unused` | boolean | false | Also list installed skills no device used since `since` |
| `gaps` | boolean | false | Also list skills used in 3+ sessions on one device that another device lacks |
| `limit` | integer | 50 | Rows with uses in the window, at most 500 |

```json
{"since": "30d", "first_day": "2026-09-24", "by": ["device", "agent"], "devices": ["local", "devbox-a"],
 "rows": [{"device": "local", "agent": "codex", "skill": "read-pr", "calls": 56, "sessions": 40,
           "user": 0, "tool": 0, "read": 56, "total": 116, "total_sessions": 65, "last": "2026-10-22"}],
 "warnings": []}
```

`user` counts uses the user asked for by name, `tool` Claude Code's Skill tool, `read` an agent reading the skill's `SKILL.md`; see [mnemo skill](cli.md#mnemo-skill).

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
| `envelope` | `1` when the body contained agent-injected boilerplate |

Injected boilerplate (workspace instructions, plugin suggestions, ambient browser state, slash-command output, Codex approval-review wraps) is **retained verbatim**, never deleted: the original stays in `body`, the searchable `text` holds the remainder with envelopes stripped, and default search matches `text` only. Genuine user replies inside an injected message stay searchable. Pass `include_injected` (or `--include-injected`) to also match the verbatim bodies.

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

With the default clipping, a 13-message `context` window holds at most about 26k characters, and the median on a real index is 7.5k. With `full: true`, a window that lands on large tool output can reach ~18k tokens or more, so use smaller `before`/`after` values.
