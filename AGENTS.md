# AGENTS.md

Guidance for coding agents (Claude Code, Codex, Pi, …) working in this repository. Human contributors should read [CONTRIBUTING.md](CONTRIBUTING.md) first.

## Project in one paragraph

Mnemo indexes the local session logs of coding agents into SQLite FTS5 and exposes search, context and full-session reads through a CLI, an MCP server, a Pi extension and a local web dashboard. Remote devices are searched by SSH fan-out, and the results are merged with Reciprocal Rank Fusion. See [docs/architecture.md](docs/architecture.md).

## Hard constraints

- **Python is standard library only.** Don't add third-party Python packages. The code must run unchanged on Python 3.7 (stock devboxes). Avoid features newer than 3.7, such as the walrus operator, `dict | dict`, `match` and `str.removeprefix`.
- **The web UI is built, then committed.** `web/` is Next.js + shadcn/ui + Tailwind; `pnpm build` exports it into `mnemo/web_dist`, which is committed so installs need no Node. Commit `mnemo/web_dist` in the same change as the `web/` sources (CI's `pnpm check:dist` fails otherwise). Never hand-edit `mnemo/web_dist`.
- **The dashboard stays local.** It binds to `127.0.0.1`, every `/api/*` call must carry the per-launch token, and the `Host` allowlist must stay in place.
- **Session content stays on its device.** Remote reads (`context`, `session`, raw) execute on the remote and return only the requested messages. Don't add code paths that copy remote indexes or logs.
- **Never commit indexes or session data.** `*.db` and `*.db-*` are gitignored, so keep it that way. Don't commit screenshots of real sessions. Generate synthetic ones with `scripts/make-demo-home.py`.
- **Tests stay hermetic.** Python tests call `DemoHome.activate()` so syncs read the demo HOME and no registered remote is contacted; e2e runs the dashboard with a temp HOME.
- **Forwarded searches must keep `--host local`,** or meshed devices will chain queries (see `remote.search_argv`).

## Layout

```text
bin/mnemo                    launcher (resolves its own symlink)
mnemo/cli.py                 argparse front end
mnemo/index.py               schema + incremental sync
mnemo/search.py              FTS5 query builder, BM25, context/session/raw reads
mnemo/remote.py              SSH exec, fan-out, RRF, rsync install
mnemo/mcp_server.py          stdio JSON-RPC MCP server
mnemo/dashboard.py           HTTP API + static server for mnemo/web_dist
mnemo/web_dist/              committed static export of web/ (generated)
mnemo/sources/{claude,codex,pi}.py   per-agent log adapters
mnemo/setup.py               `mnemo setup`: wires Claude Code, Codex, Pi (idempotent)
mnemo/upgrade.py             `mnemo upgrade`: backup, rebuild beside, verify, swap
mnemo/integrations/          Pi extension + agent skill (package data; `integrations/` is a symlink)
scripts/install.sh           curl | sh installer, served at szupzj18.github.io/mnemo/install.sh
pyproject.toml               packaging for `uv tool install` / `pipx`
scripts/make-demo-home.py    deterministic synthetic sessions for tests and screenshots
tests/                       Python unit + dashboard server tests (unittest)
web/                         Next.js UI: src/app pages, src/lib logic (+ Vitest), e2e/ (Playwright)
.github/workflows/ci.yml     Python matrix, web lint/types/tests/build, Playwright UI regression
```

## Verify your change

```bash
python3 -m unittest discover -s tests          # backend + dashboard server

cd web
pnpm lint && pnpm typecheck && pnpm test       # UI static checks + unit tests
pnpm build                                     # re-export into mnemo/web_dist
pnpm e2e                                       # Playwright over synthetic sessions
pnpm e2e:docker                                # + pixel diffs in CI's Linux image
```

- **UI changes:** run `pnpm e2e`, and if the change is visual, regenerate baselines with `pnpm e2e:docker --update` and review the new PNGs before committing them.
- **MCP changes:** send `initialize` and `tools/list` over stdio and check the schemas.
- **Remote changes:** test against a real SSH host when you can. Unreachable hosts must degrade to a warning, never a failure.
- **Schema changes:** bump `SCHEMA_VERSION` in `mnemo/index.py`, keep reads working on the previous schema, and make `Index.incomplete_paths()` recognize rows an older writer would produce. Tell users to run `mnemo upgrade --remotes` (CHANGELOG + docs). Long-running MCP servers keep the old code until their agent session restarts, so never assume every writer has upgraded.

## Adding an agent adapter

1. Create `mnemo/sources/<agent>.py` with a `Source` subclass that implements `files()` and `parse(path, clip_text=True)`. `parse` returns `(session_id, cwd, [(lineno, Msg), …])`.
2. Map the agent's records onto `role` ∈ {user, assistant, tool} and `kind` ∈ {text, summary, reasoning, tool_call, tool_result}. Keep injected boilerplate verbatim in `raw` but strip it from searchable `text` (flag `envelope=1`); see `model.strip_envelopes`. Free-form user replies are not boilerplate.
3. Register it in `mnemo/sources/__init__.py`.
4. Add the source to the `source` descriptions in `mcp_server.py`, `integrations/pi/mnemo.ts` and the skill.
5. Extend `scripts/make-demo-home.py` with a sample session, and update the docs (README tables, `docs/getting-started.md` log locations).

## Docs

When you change user-visible behavior, update in the same PR:

- `README.md` and `README.zh-CN.md`, which should stay in sync
- the relevant page under `docs/`
- `integrations/skills/mnemo/SKILL.md`, if agents should use a tool differently
- `CHANGELOG.md`, under *Unreleased*
