# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Project logo ("Mesh M": an M drawn as connected device nodes) in `docs/assets/`, used in the README, the dashboard sidebar and as the dashboard favicon.
- Session view timeline rail: turn markers with clock and turn number, idle-gap chips (≥ 90 s), day dividers, tool-call durations, total session span.
- MIT license.
- Documentation site under `docs/`, a Chinese README, `AGENTS.md`, `CONTRIBUTING.md` and `SECURITY.md`.
- `scripts/make-demo-home.py` to generate synthetic sessions for testing and screenshots.
- `mnemo recent` lists recently started sessions with their first real user prompt as the title, so recent work can be recalled without a keyword; the MCP server exposes the same as `list_recent_sessions`.
- `mnemo search --include-injected` also matches injected boilerplate, and `mnemo context`/`session --show-envelope` render those bodies verbatim.
- `mnemo index --rebuild` drops and reparses the whole index from scratch.

### Fixed
- Searches from the CLI, the Pi extension and the dashboard now sync the local index first, and the MCP server syncs before every `search_sessions` instead of only at startup. Previously sessions written after the last `mnemo index` (or after the MCP server started) were invisible. Syncs within 2 s of the last one are skipped, concurrent writers wait up to 10 s, and a failed refresh falls back to the existing index with a warning. `mnemo search --no-sync` opts out.

### Changed
- Injected user-message boilerplate (workspace `AGENTS.md` instructions, plugin suggestions, ambient browser state, slash-command output, and Codex approval-review wraps) is now kept verbatim in the index but excluded from default search, snippets and session titles; this is an additive schema (v2) that migrates an existing index on its next sync, with no records dropped. Genuine user replies inside such messages remain searchable.
- Dashboard restyled with flat, hairline-bordered surfaces and matching light and dark themes.
- Dashboard device status: avatar cards with a live status dot, host tag, status line, per-agent counts and relative sync time; remotes are probed automatically on load.
- Search results use the official Claude, OpenAI (Codex) and pi marks.
- Generic search placeholder text.
- Dashboard search shows progress: busy button (also when submitted with Enter), per-device pending chips, skeleton results and a live elapsed timer; repeat submissions are ignored and network failures restore the form.

## [0.1.0] - 2026-09-24

First public release, as *agentsearch* (renamed to **Mnemo** the same day).

### Added
- SQLite FTS5 index over Claude Code, Codex (including archived sessions) and Pi logs, with incremental sync by mtime and size.
- English prefix and Chinese substring (unigram + bigram) matching, ranked with BM25.
- CLI (`search`, `context`, `session`, `index`, `status`), a zero-dependency MCP server and a Pi extension.
- Index-backed `context` lookups (7 ms on a 44k-line session, versus 316 ms re-parsing).
- Full-session reads (`session` / `get_session`) and raw reads of untruncated bodies (`--raw`).
- Multi-device federated search over SSH with Reciprocal Rank Fusion; `remote add/list/update/remove`; reads routed to the device that holds the data.
- Local dashboard: search with per-device diagnostics, click-through to a chat-style session transcript with match navigation, device management, and a token-gated `127.0.0.1` API.
- Agent skill in `integrations/skills/mnemo`.

[Unreleased]: https://github.com/szupzj18/mnemo/compare/4733c41...HEAD
[0.1.0]: https://github.com/szupzj18/mnemo/commit/4733c41
