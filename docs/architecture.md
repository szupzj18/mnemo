# Architecture

```text
 ~/.claude/projects  ~/.codex/sessions  ~/.pi/agent/sessions
          │                 │                  │
          ▼                 ▼                  ▼
   sources/claude.py  sources/codex.py   sources/pi.py      parse + normalize
          └────────────────┬┴──────────────────┘
                           ▼
                  index.py  (SQLite FTS5, incremental)       ~/.mnemo/index.db
                           │
                  search.py (query builder, BM25, context/session)
                           │
                  remote.py (SSH fan-out, RRF merge, remote install)
          ┌────────────┬───┴──────────┬──────────────┐
          ▼            ▼              ▼              ▼
       cli.py    mcp_server.py   dashboard.py   integrations/pi/mnemo.ts
```

| Module | Responsibility |
|---|---|
| `mnemo/sources/` | One adapter per agent: find session files, parse JSONL into normalized messages |
| `mnemo/model.py` | `Msg` record, timestamp normalization, 20k clipping, CJK gram generation |
| `mnemo/index.py` | Schema, incremental sync, per-file rowid ranges |
| `mnemo/search.py` | FTS5 query construction, BM25 search, context and session lookup, raw reads |
| `mnemo/remote.py` | Remote registry, SSH execution, parallel fan-out, RRF merge, rsync install |
| `mnemo/mcp_server.py` | Zero-dependency JSON-RPC stdio MCP server |
| `mnemo/dashboard.py` | Stdlib `ThreadingHTTPServer` plus a single-file HTML/CSS/JS UI |
| `mnemo/cli.py` | argparse front end |

## Normalization

Each adapter yields `(session_id, cwd, [(lineno, Msg)])`, where `Msg = (ts, role, kind, text)`:

- `role` ∈ `user | assistant | tool`
- `kind` ∈ `text | summary | reasoning | tool_call | tool_result`
- Tool calls are rendered as `name(args-json)`.
- Noise is dropped: Codex `developer`/`system` messages and `<environment_context>` preambles, Claude Code command caveats and slash-command wrappers, image blocks, and encrypted reasoning without a summary.
- `lineno` is the line in the source JSONL, which is what makes raw reads and deep links possible.

## Index

```sql
CREATE VIRTUAL TABLE messages USING fts5(
  body,                -- normalized text, unicode61 tokenizer
  grams,               -- CJK unigrams + bigrams for substring matching
  source UNINDEXED, session_id UNINDEXED, cwd UNINDEXED, ts UNINDEXED,
  role UNINDEXED, kind UNINDEXED, path UNINDEXED, lineno UNINDEXED,
  tokenize = "unicode61"
);
CREATE TABLE files       (path PRIMARY KEY, source, session_id, cwd, mtime, size);
CREATE TABLE file_ranges (path, lo, hi);   -- contiguous rowid range per file
CREATE TABLE meta        (key PRIMARY KEY, value);
```

**Incremental sync.** Mnemo stats every session file and compares its `(mtime, size)` with `files`. When a file changed, its rowid range is deleted and the file is re-inserted as one contiguous block, which is simple and correct for append-only JSONL. Deleted files are dropped.

**Context in O(1) of file size.** `file_ranges` maps a file to `[lo, hi]` rowids. A context window is a rowid range query, so it doesn't need to re-parse the file. On the largest session (44k lines), a lookup takes 7 ms compared with 316 ms for re-parsing.

## Query construction

For each whitespace-separated token, Mnemo builds an alternative, and all tokens are ANDed:

```text
retry 退避  →  (body : "retry"*) AND (body : "退避"* OR grams : ("退避"))
```

- English and code: `unicode61` prefix match on `body`.
- CJK: each run is split into bigrams and ANDed against `grams`, which gives substring matching without a CJK tokenizer. Single characters use unigrams.
- Filters (`source`, `kind`, `cwd LIKE`, `ts >=`) are applied on UNINDEXED columns.
- Ranking uses `bm25(messages)`. Snippets come from FTS5 `snippet()` with `[[…]]` markers.

## Federation

`fan_out_search` runs the local search and one SSH call per remote in a `ThreadPoolExecutor`. Each remote runs `mnemo index` and then `mnemo search … --json --host local`, and its hits are tagged with the device name. Results are merged by Reciprocal Rank Fusion keyed on `(host, path, lineno)`:

```text
score(hit) = Σ 1 / (60 + rank_on_device)
```

RRF was chosen over a single global index for two reasons:

- It needs no schema change and no data movement. FTS5 can't `ALTER TABLE ADD COLUMN`, so a unified index would mean full rebuilds, about 12.5 s per 150k rows.
- Its top-20 overlap with a hypothetical unified index was measured at about 64%, versus 62% for merging by raw score. Per-corpus BM25 statistics differ anyway, so rank fusion is the honest merge.

## Dashboard

The dashboard is a single Python module with an inline page and no build step.

- It binds to `127.0.0.1`, and the port scans 7787–7796.
- Each launch generates a `secrets.token_urlsafe(16)` token. `/api/*` requires the `X-Dashboard-Token` header and a local `Host` header.
- Sessions longer than 240 messages are rendered in a window around the anchor hit.

## Why Python and SQLite

Everything has to run unchanged on stock devboxes, which often have only Python 3.7 or 3.8 and no package manager access. A standard-library-only implementation installs with `rsync`, and SQLite FTS5 comfortably handles millions of messages on one machine. The hot paths are SQLite queries and SSH round-trips, not Python.
