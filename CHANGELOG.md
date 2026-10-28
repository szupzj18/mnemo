# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Fixed
- `--since` on `mnemo search` and `mnemo recent` (and `since` on the `search_sessions` and `list_recent_sessions` tools) accepts `Nd`, the last N days, as the bundled skill already showed with `mnemo recent --since 7d`. Before, `Nd` was compared to timestamps as text, so `--since 3d` or `7d` listed nothing and `--since 10d` didn't filter at all. A value that is neither `Nd`, a date nor an ISO timestamp is now an error rather than a silent mismatch.

## [0.7.0] - 2026-10-26

### Added
- Every Claude Code and Codex account on the device is indexed, not just the default one: `~/.claude-*/projects` and `$CLAUDE_CONFIG_DIR` beside `~/.claude/projects`, and `~/.codex-*/sessions` (and `archived_sessions`) and `$CODEX_HOME` beside `~/.codex`. A folder reached twice through a symlink (such as `~/.codex-work/sessions` pointing to `~/.codex/sessions`), and a session file linked into two accounts, are indexed once. Hits, `recent` rows and session reads carry `account`, the config dir the session came from (`.claude-work`); the CLI shows it next to the source for accounts other than the default.
- Session archive, on by default: sync copies every Claude Code, Codex and Pi session log it indexes to `~/.mnemo/archive/<source>/<account>/…` (written atomically, files `0600` in `0700` folders), and when the agent deletes the original (Claude Code does after `cleanupPeriodDays`, 30 days by default) the session stays in the index and is read from the copy. `search`, `context`, `session` and `--raw` work as before; hits, `recent` rows and session reads say `archived: true`, sessions also give `archive_path`, and the CLI marks them `(archived)`. `mnemo status`, `mnemo doctor` and `mnemo node` show the archive. The copy roughly doubles the disk space the logs take. Turn it off with `mnemo node --archive off` (or `MNEMO_ARCHIVE=off` for one command); OpenCode, which keeps every session in one database, is not archived.
- Antigravity CLI (`agy`) conversations are indexed as source `agy`, from `~/.gemini/antigravity-cli/conversations/<conversation id>.db`, one SQLite database per conversation. Prompts, replies, thinking, tool calls and results, and checkpoint summaries are searchable; background-task notices are kept but left out of default search, like other injected text, and API error steps are skipped. Steps agy still holds in its write-ahead log are picked up without waiting for a checkpoint. Conversations are archived like the other file-per-session logs, copied with SQLite's backup API, so one deleted in agy stays searchable. Hits show as `[local/agy]`. `mnemo setup` adds the MCP server to `~/.gemini/config/mcp_config.json` (backed up first). The Antigravity desktop app's conversations are not indexed.
- Grok CLI (`grok`) sessions are indexed as source `grok`, from `~/.grok/sessions/<URL-encoded cwd>/<session id>/updates.jsonl`, the conversation log grok resumes sessions from. Prompts, replies, reasoning summaries, tool calls and results (including the server's own web searches) and session recaps are searchable; a reminder grok puts ahead of a scheduled task's prompt is kept but left out of default search. Every `~/.grok-*` home and `$GROK_HOME` are indexed like Codex accounts, and sessions are archived, so one deleted in grok stays searchable. Hits show as `[local/grok]`. `mnemo setup` appends `[mcp_servers.mnemo]` to `~/.grok/config.toml` (backed up first), unless grok already loads Claude Code's `mnemo` server from `~/.claude.json`; `mnemo doctor` lists Grok CLI under Agents. The Grok desktop app's chats are not indexed.

- `mnemo skill ls | search | stats`: read-only views of the agent skills on each device. `ls` lists every agent's user-level skill folders (`~/.claude*/skills`, `~/.codex*/skills`, `~/.agents/skills`, `~/.pi/agent/skills`, `~/.grok*/skills`, OpenCode's and agy's), each skill as a link, a folder or a dangling link with a hash of its content, and flags dangling links and names whose copies differ; folders the agents manage themselves (Claude Code's `synced/`, Codex's `.system/`) are left out. `search` matches the `SKILL.md` name and description. `stats --since 30d --by device,agent` counts how often indexed sessions used each skill, split into uses the user asked for by name (`/name`, `$name`, `/skill:name`), Claude Code's Skill tool, and an agent reading the skill's `SKILL.md`; `--unused` lists installed skills nobody used and `--gaps` skills one device uses that another lacks. `--host all` asks every registered device, which must run this version. MCP gains `list_skills` and `skill_usage`, and `mnemo doctor` reports dangling skill links and differing copies. Nothing writes to a skill folder.
- `mnemo skill sync [--dry-run] [--device NAME|all]` and `mnemo skill rollback`: link the skills listed in `~/.mnemo/skills.toml` from git repositories into `~/.claude/skills` and `~/.agents/skills`, one symlink per skill, on this device or registered ones. A link that already resolves to the same source folder is taken over (`adopt`), not rebuilt; a name taken by anything else is reported as a conflict and left alone; Claude Code's `synced/`, Codex's `.system/` and entries outside the manifest are never touched. Each device keeps `~/.mnemo/skills.lock.json` (link, target, source commit, content hash, files) and earlier locks for `rollback`. Devices clone or `git pull --ff-only` the sources themselves; when that fails, or the device holds a copy without git history, this device pushes its checkout with rsync. A source with `scope = "internal"` only reaches the devices listed for it. The device side is sent over SSH, so devices need no upgrade. See [docs/cli.md](docs/cli.md#mnemo-skill-sync--rollback).

### Changed
- The index schema is now v4: a `skill_events` table records each skill use found while indexing. Run `mnemo upgrade`: a v3 index is copied, the table is filled from the messages already stored (seconds, no reparse), verified and swapped in, so the live index is never changed in place; a search with new code on a v3 index does the same fill in place on its first sync. Once an index is v4, mnemo processes still on older code (MCP servers of open agent sessions, links, other devices) stop indexing into it until restarted or upgraded, as with any schema change.
- With the archive on, deleting a session log no longer removes it from the index. Sessions deleted before this release can't be brought back, because there is no copy of them. Delete a session's copy under `~/.mnemo/archive` to drop it for good. The index schema is unchanged, so no rebuild is needed: the next sync indexes the other accounts and copies every log once (about as long as the copy of that many bytes takes).

### Fixed
- `mnemo --db <path> mcp` used the default index (`~/.mnemo/index.db`) for its startup sync and for every tool call, ignoring `--db`. Both now use the index named.

## [0.6.0] - 2026-10-17

### Added
- Searches leave out the calling agent's own session, which otherwise answers any query about what was just said. The id comes from `CLAUDE_CODE_SESSION_ID` (Claude Code), the MCP request's `_meta.threadId`/`sessionId` or `CODEX_THREAD_ID`/`CODEX_SESSION_ID` (Codex), and the Pi extension. `--include-current` / `include_current_session` keeps it; `--exclude-session` / `exclude_session_ids` leaves out others. Responses with coverage list `excluded_sessions`.
- `mnemo context` and `get_context` (MCP, Pi) clip each message at 2,000 characters by default, so a window that lands on 20k-character tool results no longer floods the caller's context. A clipped message ends in `… [clipped: 2000 of N chars; --full for the whole message]` and carries `"clipped": true` and `"chars": N`. `--full` / `full: true` returns whole bodies, and so does `--raw` / `raw: true`. `--max-chars N` / `max_chars` sets another length, with `0` meaning whole bodies. On 300 random windows from a real index, the median window shrank from 30k to 7.5k characters.

### Changed
- `get_context` output is clipped by default, as above. Scripts that relied on `mnemo context --json` returning whole bodies should add `--full`. Relayed reads (`--relay`) stay unclipped, so the device that asked does the clipping, and devices on older code keep working.

### Fixed
- `mnemo setup` and `mnemo doctor` shorten the home directory to `~` only where it starts a path. Where `$HOME` is a symlink (such as `/home/u` pointing to `/data00/home/u`), a path like `/data00/home/u/mnemo/bin/mnemo` was shown as `/data00~/mnemo/bin/mnemo`. This only affected the display; the config that was written was correct.

## [0.5.0] - 2026-10-12

### Added
- Codex completed-item and `agent_message` parsing, plus `.jsonl.zst` reads using an optional device-local `zstd` executable. Plain/compressed siblings keep the same logical path and line anchors. Updating Mnemo automatically reparses unchanged Codex files with the new adapter.
- Session pagination (`limit`, `cursor`, `anchor_line`) across CLI, MCP, Pi and routed remote reads. Indexed head/tail reads select bodies directly; the dashboard loads 181 messages around a hit and requests adjacent pages.
- Structured device/index coverage for MCP/Pi searches and `mnemo search --json --coverage`.
- Reproducible synthetic retrieval benchmark and format-recall evaluation, including a known miss beyond the 20k indexed-body cap.
- `mnemo doctor`: one check of everything mnemo depends on (a newer release, the index, processes still running old code, each agent's connection, every reachable device and its code, link services), grouped like `setup` and printed group by group as each finishes, devices as a table (latency, code, relay), ending with the command that fixes each problem. Exits 1 on a problem; `--full` also verifies the index file, `--offline` skips the release check, `--json` for scripts.

### Changed
- Index schema v3 stores each message's text once: the verbatim body is kept only when it differs from the searchable text (injected envelopes), instead of beside every message. On a 4.1 GB real corpus the index shrinks from 1.84 GB to 1.30 GB (44% → 31% of raw); search and reads are unchanged or faster. Run `mnemo upgrade` to rebuild with a backup (about a minute); otherwise the next sync rebuilds in place. An older mnemo treats a v3 index as read-only.
- **Breaking:** MCP `search_sessions` text responses now contain `{hits, coverage, warnings}` instead of a hit array; callers that parse the previous shape must read `hits`. Plain CLI JSON remains an array unless `--coverage` is requested. Restart agent sessions after updating tools.
- `mnemo setup` output is grouped by agent with status marks (`+` added, `↻` repaired, `✓` unchanged, `·` skipped, `✗` failed), puts backups and repaired links on their own lines, and ends with a summary and the next step (which sessions to restart, or what to finish by hand). Colors on a terminal only, `NO_COLOR` honoured, ASCII marks on non-UTF-8 terminals; `--json` for scripts.

### Fixed
- Missing optional `zstd` skips only the compressed files that need reading and retains their indexed rows; new and changed plain Codex sessions continue syncing.
- A peer rejecting session pagination keeps its relay protocol unchanged and returns an upgrade hint. Only rejection of `--relay` downgrades a direct peer to the legacy protocol.
- `search`, `recent`, `context` and `session` wrote terminal escape codes even when piped or redirected; styles are now off unless stdout is a terminal, and `NO_COLOR` and `TERM=dumb` turn them off everywhere.
- Session titles (`recent`, `list_recent_sessions`, the dashboard) no longer show injected text: a Claude Code skill's body ("Base directory for this skill: …") is treated as injected boilerplate like other envelopes, and Codex's approval-review ("guardian") sessions, whose user turns are all generated, no longer appear as sessions of their own; the reviewer's replies stay searchable. `/loop` prompts, also marked as meta, still title their sessions. Also treated as injected: Claude Code's background-task notices (`<task-notification>`) and worker-fork instructions (`<fork-boilerplate>`, so the fork's directive becomes its title), and Codex's attached-file header ahead of "## My request:" (the request itself is kept). A session started with a slash command or skill and never typed into is titled by that command (`/disk-scan`) instead of being left out. Existing sessions pick up the new titles after `mnemo upgrade`.

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

[Unreleased]: https://github.com/szupzj18/mnemo/compare/v0.7.0...HEAD
[0.7.0]: https://github.com/szupzj18/mnemo/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/szupzj18/mnemo/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/szupzj18/mnemo/compare/v0.4.1...v0.5.0
[0.4.1]: https://github.com/szupzj18/mnemo/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/szupzj18/mnemo/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/szupzj18/mnemo/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/szupzj18/mnemo/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/szupzj18/mnemo/releases/tag/v0.1.0
