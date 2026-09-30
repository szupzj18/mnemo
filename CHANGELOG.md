# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed
- `mnemo setup` output is grouped by agent with status marks (`+` added, `↻` repaired, `✓` unchanged, `·` skipped, `✗` failed), puts backups and repaired links on their own lines, and ends with a summary and the next step (which sessions to restart, or what to finish by hand). Colors on a terminal only, `NO_COLOR` honoured, ASCII marks on non-UTF-8 terminals; `--json` for scripts.

### Fixed
- `search`, `recent`, `context` and `session` wrote terminal escape codes even when piped or redirected; styles are now off unless stdout is a terminal, and `NO_COLOR` and `TERM=dumb` turn them off everywhere.
- Session titles (`recent`, `list_recent_sessions`, the dashboard) no longer show injected text: a Claude Code skill's body ("Base directory for this skill: …") is treated as injected boilerplate like other envelopes, and Codex's approval-review ("guardian") sessions, whose user turns are all generated, no longer appear as sessions of their own; the reviewer's replies stay searchable. `/loop` prompts, also marked as meta, still title their sessions. Existing sessions pick up the new titles after `mnemo upgrade`.

## [0.4.1] - 2026-09-30

### Fixed
- Codex cancelled mnemo's tools in unattended runs (`codex exec`, where approvals are off and any tool not marked read-only needs one). The MCP tools now declare annotations: `search_sessions`, `get_context`, `get_session` and `list_recent_sessions` are marked `readOnlyHint`, so Codex no longer cancels them in unattended runs (`codex exec`, where approvals are off and any tool without the hint needs one). `reindex` is marked as a non-destructive write.

## [0.4.0] - 2026-09-30

OpenCode joins Claude Code, Codex and Pi as a fourth source, and `mnemo setup` connects it. Upgrades now reach agent sessions that are already running, and the dashboard, without a restart. Concurrent syncs no longer fail on SQLite locks, and searches are about twice as fast on large indexes.

### Added
- OpenCode adapter: `~/.local/share/opencode/opencode.db` is indexed as a fourth source. Each session is addressed as `<db path>::<session id>` and reports its own change key through the new `Source.records()` hook, so a new message reindexes one session instead of re-reading the database. `part.rowid` is the `lineno`, so `context`, `session` and `--raw` work as usual.
- `mnemo setup` connects OpenCode: it adds `mcp.mnemo` to OpenCode's `opencode.json` (or an existing comment-free `opencode.jsonc`) under `$XDG_CONFIG_HOME/opencode`, backing it up first; a `.jsonc` with comments is left alone and the block to add is printed.

### Changed
- Upgrades reach running agent sessions: the MCP server runs every tool call in a fresh process, so after `mnemo upgrade` (or a `git pull`) the next search in an already-open Claude Code or Codex session uses the new code, with no restart. Only a changed tool list still needs a new session. MCP servers started by older versions need one last restart.
- The dashboard restarts itself in place when its code changes, on the same port and with the same token, so open tabs keep working.
- `mnemo upgrade` no longer lists processes that follow updates by themselves, and restarts links before listing the rest.

### Fixed
- OpenCode's database is looked up under `$XDG_DATA_HOME/opencode` when that is set, as OpenCode itself does (default `~/.local/share/opencode`).
- Concurrent syncs of one index could fail with "database is locked": index writes began with a deferred transaction that read first and then upgraded to a write lock, which SQLite refuses at once (without waiting) when another writer does the same, and the failed transaction kept its lock so the other writer then waited out the 10 s timeout. This hit whenever the MCP server, the CLI, the dashboard or a neighbor's relayed search synced at the same time, most on a fresh index, and made the multi-device test flaky. Writes now take the lock up front (`BEGIN IMMEDIATE`) and roll back on failure, and a session's old rows are replaced in the same transaction as its new ones.
- pip/uv installs started their own subprocesses (link answers, tool calls, services) with `python -m mnemo`, which imports a `mnemo/` directory from the current working directory if there is one; they now import the installed package explicitly.
- Search could fall back to a slower SQL plan: `bm25(messages) AS rank` shadowed FTS5's hidden `rank` column, so `ORDER BY rank` sorted the expression through a temp B-tree (every match materialised) instead of streaming in rank order. A 66k-match query on a 151k-message index drops from ~85 ms to ~38 ms; results and their `rank` values are unchanged.

## [0.3.0] - 2026-09-29

### Added
- Scheduled releases: every other Monday a workflow opens a "Release x.y.z" PR (when `CHANGELOG.md` has entries under *Unreleased*) that previews the release notes and merges itself once CI passes; merging tags the version and publishes the GitHub Release. The version follows the entries: Added/Changed bump the minor version, fixes alone the patch. `scripts/release.py` does the changelog and version work.
- PyPI package `mnemo-search` (the command stays `mnemo`), published by each release with trusted publishing.

### Fixed
- pip/uv installs: `mnemo remote add/update` synced the whole `site-packages` directory to the remote; it now sends just the package and a launcher. The wheel also lacked the dashboard's `.source-hash`, which made pip-installed devices look outdated to checkouts forever; code fingerprints no longer include the launcher, so both kinds of install agree.

## [0.2.0] - 2026-09-29

Mnemo now spans any device topology: searches pass through relays, devices that cannot connect back can still be searched (`mnemo link`), and upgrading one machine brings every reachable device up to date. The dashboard gains a topology view, and installing takes one command.

### Added
- Multi-hop search across devices: each device lists only its direct neighbors; a neighbor with `mnemo node --forward on` relays searches and reads to its own neighbors. Hits carry routes (`devbox-109/devbox-126`) usable as `--host`; loops stop via node ids and a 3-hop budget; a device reached twice is reported once via the shortest route. Forwarding is off by default; older neighbors are asked the old way.
- `mnemo node`: this device's stable id, name and forwarding policy.
- `mnemo link --install` / `--uninstall` / `--list`: run a link as a login service (launchd agent, systemd user unit, or a background process elsewhere), see each link's state, revoke it. `mnemo upgrade` restarts installed links. The dashboard's device cards show whether each remote can search this device back and switch it (with confirmation); devices that linked in are marked as such and skipped by code updates.
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
- The dashboard sidebar shows the running version (from `/api/status`) instead of a hardcoded one.
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
- `mnemo link` answered requests with `bin/mnemo`, which pip/uv installs do not have; it now runs itself the way it was started.
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

[Unreleased]: https://github.com/szupzj18/mnemo/compare/v0.4.1...HEAD
[0.4.1]: https://github.com/szupzj18/mnemo/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/szupzj18/mnemo/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/szupzj18/mnemo/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/szupzj18/mnemo/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/szupzj18/mnemo/releases/tag/v0.1.0
