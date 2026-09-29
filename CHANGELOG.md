# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Multi-hop search across devices: each device lists only its direct neighbors; a neighbor with `mnemo node --forward on` relays searches and reads to its own neighbors. Hits carry routes (`devbox-109/devbox-126`) usable as `--host`; loops stop via node ids and a 3-hop budget; a device reached twice is reported once via the shortest route. Forwarding is off by default; older neighbors are asked the old way.
- `mnemo node`: this device's stable id, name and forwarding policy.
- `mnemo link <remote> --allow-inbound`: lets a device you can reach search and read your sessions when it cannot connect back to you (devboxes to a laptop). The laptop keeps an SSH session open; the remote talks to it through a user-only Unix socket and may run only read-only commands (search, context, session, status, node info, sync), under the laptop's relay policy. A closed link is skipped quietly by searches; it reconnects with backoff.
- Code fingerprints: every device reports a short hash of the code it runs (`mnemo node`, topology probes), so the device you upgrade can tell which others are behind. `mnemo upgrade` (and so re-running the installer) now brings every reachable device running different code up to this code, through relays too (`--no-remotes` to skip). `mnemo remote upgrade [ROUTE...]` does just that step. The dashboard topology view marks outdated devices, with per-device and update-all buttons.
- Dashboard topology view: the devices reachable from here (through relays, within the hop budget) with route, name, relay policy, latency and offline state. The devices page can rename this device and switch relaying on it or on a direct neighbor, with a confirmation before turning a relay on.
- One-line install: `curl -fsSL https://szupzj18.github.io/mnemo/install.sh | sh` checks prerequisites, clones, links `mnemo` onto PATH, indexes and connects agents; re-running upgrades (`git pull` + `mnemo upgrade`).
- `mnemo setup` connects every detected agent: Claude Code MCP + skill, Codex MCP (config backed up), Pi extension. Idempotent, `--dry-run`, `--agent`.
- `pyproject.toml`: `uv tool install` / `pipx install git+https://github.com/szupzj18/mnemo` (the dashboard build and integrations ship as package data; `integrations/` moved into the package, with a root symlink for existing paths).
- `mnemo upgrade`: one command to back up the index (SQLite online backup, newest 3 kept), rebuild it beside the live one, verify, and swap it in atomically; `--remotes` does the same on every device, `--restore` rolls back, `--list` shows backups. It also names running mnemo processes that still use the old code.
- Project logo ("Mesh M": an M drawn as connected device nodes) in `docs/assets/`, used in the README, the dashboard sidebar and as the dashboard favicon.
- Session view timeline rail: turn markers with clock and turn number, idle-gap chips (≥ 90 s), day dividers, tool-call durations, total session span.
- MIT license.
- Documentation site under `docs/`, a Chinese README, `AGENTS.md`, `CONTRIBUTING.md` and `SECURITY.md`.
- `scripts/make-demo-home.py` to generate synthetic sessions for testing and screenshots.
- Python test suite (`tests/`, stdlib `unittest`): index sync, search, remote argv/RRF, dashboard server security and API.
- Vitest unit tests for UI logic; Playwright end-to-end tests with light/dark screenshot baselines over synthetic sessions.
- GitHub Actions CI: Python 3.8/3.12, web lint/typecheck/unit/build plus a stale-build check, and Playwright UI regression in the official Playwright image.
- `mnemo recent` lists recently started sessions with their first real user prompt as the title, so recent work can be recalled without a keyword; the MCP server exposes the same as `list_recent_sessions`.
- `mnemo search --include-injected` also matches injected boilerplate, and `mnemo context`/`session --show-envelope` render those bodies verbatim.
- `mnemo index --rebuild` drops and reparses the whole index from scratch.

### Changed
- Injected user-message boilerplate (workspace `AGENTS.md` instructions, plugin suggestions, ambient browser state, slash-command output, and Codex approval-review wraps) is now kept verbatim in the index but excluded from default search, snippets and session titles; this is an additive schema (v2) that migrates an existing index on its next sync, with no records dropped. Genuine user replies inside such messages remain searchable.
- Dashboard restyled with flat, hairline-bordered surfaces and matching light and dark themes.
- Dashboard device status: avatar cards with a live status dot, host tag, status line, per-agent counts and relative sync time; remotes are probed automatically on load.
- Search results use the official Claude, OpenAI (Codex) and pi marks.
- Generic search placeholder text.
- Dashboard search shows progress: busy button (also when submitted with Enter), per-device pending chips, skeleton results and a live elapsed timer; repeat submissions are ignored and network failures restore the form.
- Dashboard rebuilt with Next.js (static export), shadcn/ui and Tailwind CSS in `web/`, served prebuilt from `mnemo/web_dist` so installs still need only Python. Search results survive navigating into a session and back; results are real links (open in new tab works).
- `dashboard.py` shrinks to the API plus a static file server (token injection, `HEAD` for route prefetches, traversal guard, immutable caching for hashed assets).
- `scripts/make-demo-home.py` produces deterministic session ids.

### Fixed
- Parallel searches that learned several neighbors' ids at once could fail with `FileNotFoundError` or drop one of the updates to `remotes.json`.
- Dashboard search with registered remotes failed with a TypeError since the injected-boilerplate change; it now uses the same routing as the CLI and is covered by a test with real remote processes.
- Index writes are refused when the index was upgraded by a newer mnemo (reads keep working, searches warn), so a long-running MCP server can no longer write old-format rows into a new schema or re-stamp its version. Sync also repairs rows an older process already wrote (the v2 upgrade left 21k messages invisible to default search this way).
- `mnemo remote add/update` no longer rsyncs `web/node_modules`, build output or caches to devices.
- Searches from the CLI, the Pi extension and the dashboard now sync the local index first, and the MCP server syncs before every `search_sessions` instead of only at startup. Previously sessions written after the last `mnemo index` (or after the MCP server started) were invisible. Syncs within 2 s of the last one are skipped, concurrent writers wait up to 10 s, and a failed refresh falls back to the existing index with a warning. `mnemo search --no-sync` opts out.

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
