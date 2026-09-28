# Getting started

## Requirements

- Python 3.7 or newer, standard library only
- SQLite built with FTS5 (the default on macOS and on current Linux distributions)
- One or more of: [Claude Code](https://docs.anthropic.com/en/docs/claude-code), [Codex CLI](https://github.com/openai/codex), [Pi](https://github.com/badlogic/pi-mono)

Check FTS5:

```bash
python3 -c "import sqlite3; sqlite3.connect(':memory:').execute('create virtual table t using fts5(x)'); print('ok')"
```

## Install

```bash
git clone https://github.com/szupzj18/mnemo.git ~/mnemo
ln -s ~/mnemo/bin/mnemo ~/.local/bin/mnemo
mnemo index -v
mnemo status
```

`bin/mnemo` resolves the package through its own symlink, so a symlink on `PATH` is all you need. To update, run `git pull` in the clone.

The first `mnemo index` parses every session file. Expect about 30 s for a few thousand sessions. Later runs only re-read files whose size or mtime changed.

## Where Mnemo looks

| Agent | Session logs |
|---|---|
| Claude Code | `~/.claude/projects/**/*.jsonl` |
| Codex | `~/.codex/sessions/**` and `~/.codex/archived_sessions/**` |
| Pi | `~/.pi/agent/sessions/**` |

Mnemo keeps its own state in `~/.mnemo/`:

| File | Contents |
|---|---|
| `index.db` | SQLite FTS5 index over this machine's sessions |
| `remotes.json` | Registered remote devices |
| `ssh-<name>` | SSH ControlMaster sockets |

## Connect your agents

### Claude Code

```bash
claude mcp add --scope user mnemo -- mnemo mcp
ln -s ~/mnemo/integrations/skills/mnemo ~/.claude/skills/mnemo   # optional but recommended
```

The MCP server exposes the tools. The skill describes *when* to use them, for example "what did we decide last time…" or "did Codex ever try…", so the agent reaches for Mnemo without being told to. Start a new session after installing.

### Codex

```toml
# ~/.codex/config.toml
[mcp_servers.mnemo]
command = "/Users/you/.local/bin/mnemo"
args = ["mcp"]
startup_timeout_sec = 120
```

Use an absolute path, because MCP servers don't always inherit your shell's `PATH`. The generous `startup_timeout_sec` covers the incremental sync the server runs at startup.

### Pi

```bash
ln -s ~/mnemo/integrations/pi/mnemo.ts ~/.pi/agent/extensions/mnemo.ts
```

The extension shells out to the CLI. It resolves the binary as `$MNEMO_BIN`, then `~/mnemo/bin/mnemo`, then `mnemo` on `PATH`.

### Skills for other agents

`integrations/skills/mnemo/SKILL.md` is a plain skill file that drives the CLI with `--json`. Symlink the directory into any agent's skills folder.

## Keeping the index fresh

| Entry point | When the local index syncs |
|---|---|
| MCP server | On server startup, plus on demand through the `reindex` tool |
| CLI / Pi extension | Only when you run `mnemo index` |
| Remote devices | Automatically, right before each federated search |

A sync with nothing to do takes about 0.1 s. To keep CLI and Pi searches current, run it on a schedule:

```bash
# crontab -e
*/10 * * * * $HOME/.local/bin/mnemo index >/dev/null 2>&1
```

## Try it

```bash
mnemo search retry backoff                  # all agents, all devices
mnemo search 退避 重试 --source codex        # Chinese substring match, Codex only
mnemo search oom --cwd infra --since 2026-09-01 --limit 5
mnemo context <path> <lineno>               # from a hit
mnemo dashboard                             # browser UI
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `no matches (try mnemo index first)` | The index is empty or stale. Run `mnemo index -v`. |
| `no such module: fts5` | Your Python's SQLite lacks FTS5. Use the system Python on macOS or a distribution Python ≥ 3.7. |
| Agent doesn't use Mnemo on its own | Install the skill as well as the MCP server, then start a new session. |
| A remote is missing from results | Check stderr for `unreachable` warnings, then run `mnemo remote list` and the dashboard's connectivity test. See [Multi-device](multi-device.md). |
| Hit text ends with `…[truncated]` | Index bodies are capped at 20k characters. Re-read with `--raw`. |
