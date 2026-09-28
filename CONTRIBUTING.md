# Contributing to Mnemo

Thanks for your interest! Mnemo is small on purpose: standard-library Python, one SQLite file, and no services to run. Contributions that keep it that way are the easiest to merge.

## Good first contributions

- **A new agent adapter** (Gemini CLI, Cursor, OpenCode, Aider, …). Each adapter is about 100 lines. See *Adding an agent adapter* in [AGENTS.md](AGENTS.md).
- **Tests.** More coverage for the source adapters and remote paths (`tests/`, stdlib `unittest`) is always welcome.
- **Docs and examples** for more MCP clients.
- Items on the [roadmap](README.md#roadmap).

## Development setup

```bash
git clone https://github.com/szupzj18/mnemo.git && cd mnemo
python3 scripts/make-demo-home.py /tmp/mnemo-demo     # synthetic sessions
HOME=/tmp/mnemo-demo ./bin/mnemo index -v
HOME=/tmp/mnemo-demo ./bin/mnemo dashboard
```

Working on the web UI needs Node 20+ and pnpm; see [web/README.md](web/README.md) for the hot-reload setup and the test commands.

Using a synthetic `HOME` keeps your real sessions out of bug reports, screenshots and test fixtures.

## Pull requests

1. Open an issue first for anything larger than a bug fix, so we can agree on the approach.
2. Keep PRs focused, one change per PR.
3. Run the checks in [AGENTS.md → Verify your change](AGENTS.md#verify-your-change).
4. Update the docs and `CHANGELOG.md` (*Unreleased*) when behavior changes.
5. **Never include real session content** in issues, PRs, fixtures or screenshots, because it may contain secrets.

## Code style

- Python: 3.7 compatible, standard library only.
- Web: TypeScript, shadcn/ui components, Tailwind utilities; keep pure logic in `web/src/lib` with Vitest tests.
- Match the surrounding code: small functions, short comments that explain *why*.
