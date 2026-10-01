# Session pages and search coverage

## Codex log representations

The adapter recognizes legacy `response_item`, plaintext `agent_message`, and `event_msg/item_completed` turn items. Completed user/assistant messages, plans, reasoning summaries, command output, function output and MCP calls/results map to the existing role/kind model. Unknown event types and encrypted content are not decoded.

Raw/completed mirrors are deduplicated within a turn by item ID and message kind, or by an untruncated body digest when no ID exists. A counter consumes each mirror once so repeated identical messages remain separate. Bodies are clipped after computing the digest, keeping indexing memory bounded per message.

Plain `.jsonl` and `.jsonl.zst` siblings share the logical `.jsonl` path. The plain sibling wins when both exist. Compressed reads stream through the optional device-local `zstd -dc` executable; line numbers refer to decompressed JSONL. Python has no third-party dependencies.

Without `zstd`, files that require decompression are skipped individually when reindexing is needed, retaining their previous indexed rows and reporting a file warning. New and changed plain Codex files and other sources still sync. Existing indexed context remains readable; raw reads need the decoder. Per-file adapter metadata binds to the file modification signature and rowid range. Updating the adapter reparses unchanged Codex files on the next sync; rows replaced by older writers are reparsed when their signature no longer matches. Run `mnemo upgrade` to update devices and restart agent sessions that still run older code.

## Bounded session reads

```text
search hit (host + path + line)
  → get_context or get_session(limit=100, anchor_line=line)
  → page.next_cursor / page.previous_cursor
  → another read on the same device
```

`get_session` accepts these selectors over CLI, MCP, Pi and the dashboard API:

| Selector | Behavior |
|---|---|
| `head` / `tail` | First/last N indexed messages; only selected bodies are read |
| `limit` | Page size, 1–500; absence retains full-session reads |
| `anchor_line` | Center the first page near a source line; requires `limit` |
| `cursor` | Continue with a returned opaque cursor; requires `limit` |

`head`/`tail` are mutually exclusive and cannot be combined with pagination. `cursor` and `anchor_line` are mutually exclusive. The result retains full-session metadata and `count`, while `messages` contains only selected messages. `page` contains `offset`, `limit`, `next_cursor` and `previous_cursor`.

Cursors bind to the path, file rowid range and indexed modification signature. Reindexing a changed session invalidates its cursors; restart with an anchor or without a cursor. Normalized reads are against the index snapshot. Raw reads still parse the original source before slicing; their response is bounded, their source-reading work is not. Remote content is read on its holding device, and only requested messages return over the route. Every route hop must support pagination; old peers return an error instead of an unbounded fallback.

The dashboard requests 181 messages around the hit and loads adjacent pages on demand. Its match controls apply to loaded messages; the page shows the loaded/total counts when history remains unread. Whole-session search continues to use FTS5.

## Search coverage

MCP and Pi searches return `{hits, coverage, warnings}`. CLI users opt in with `--json --coverage`; plain `--json` retains its array shape. Relay envelopes carry coverage through each hop; older relay/legacy peers are marked with unknown refresh status.

| Field | Meaning |
|---|---|
| `host` | Route from the requesting device |
| `status` | `searched`, `failed`, or `skipped` (a disconnected inbound link) |
| `index_refresh` | `synced`, `recent` (2-second sync throttle), `not_requested`, `failed`, or `unknown` |
| `last_sync` | Last device-local sync timestamp, when available |
| `index_warnings` | Sources/files whose sync failed or was skipped |
| `body_char_limit` | 20,000 indexed characters per message |
| `bodies_may_be_truncated` | The index does not guarantee coverage of full original bodies |

A device can finish searching an existing index while its refresh failed. Zero hits establish absence only within the searched indexed content and filters; unreadable devices, source failures, unknown peer freshness and clipped body tails remain outside that claim. `raw=true` retrieves full bodies but does not make clipped tails searchable.
