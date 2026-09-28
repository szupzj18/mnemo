# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Session view timeline rail: turn markers with clock and turn number, idle-gap chips (≥ 90 s), day dividers, tool-call durations, total session span.
- MIT license.
- Documentation site under `docs/`, a Chinese README, `AGENTS.md`, `CONTRIBUTING.md` and `SECURITY.md`.
- `scripts/make-demo-home.py` to generate synthetic sessions for testing and screenshots.

### Changed
- Dashboard restyled with flat, hairline-bordered surfaces and matching light and dark themes.
- Dashboard device status: avatar cards with a live status dot, host tag, status line, per-agent counts and relative sync time; remotes are probed automatically on load.
- Search results use the official Claude, OpenAI (Codex) and pi marks.
- Generic search placeholder text.

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
