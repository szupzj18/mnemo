# Getting started

## Requirements

- Python 3.7 or newer, standard library only
- SQLite built with FTS5 (the default on macOS and on current Linux distributions)
- One or more of: [Claude Code](https://docs.anthropic.com/en/docs/claude-code), [Codex CLI](https://github.com/openai/codex), [Pi](https://github.com/badlogic/pi-mono) or [OpenCode](https://github.com/sst/opencode)

Check FTS5:

```bash
python3 -c "import sqlite3; sqlite3.connect(':memory:').execute('create virtual table t using fts5(x)'); print('ok')"
```

## Install

```bash
curl -fsSL https://szupzj18.github.io/mnemo/install.sh | sh
```

The script checks Python 3.7+ and SQLite FTS5, clones to `~/mnemo` (`MNEMO_DIR`), links `mnemo` into `~/.local/bin` (`MNEMO_BIN_DIR`), builds the index and runs `mnemo setup` (skip with `MNEMO_NO_SETUP=1`). Re-running it upgrades: `git pull`, then `mnemo upgrade`. It refuses to touch a checkout with local changes or a directory that is not a mnemo checkout.

Alternatively install the [`mnemo-search`](https://pypi.org/project/mnemo-search/) package with `uv tool install mnemo-search` or `pipx install mnemo-search` (the command is `mnemo`), then `mnemo index && mnemo setup`.

By hand:

```bash
git clone https://github.com/szupzj18/mnemo.git ~/mnemo
ln -s ~/mnemo/bin/mnemo ~/.local/bin/mnemo
mnemo index -v
mnemo setup
```

`bin/mnemo` resolves the package through its own symlink, so a symlink on `PATH` is all you need.

The first `mnemo index` parses every session file. Expect about 30 s for a few thousand sessions. Later runs only re-read files whose size or mtime changed.

## Where Mnemo looks

| Agent | Session logs |
|---|---|
| Claude Code | `~/.claude/projects/**/*.jsonl`, and the same under every `~/.claude-*` account and `$CLAUDE_CONFIG_DIR` |
| Codex | `~/.codex/sessions/**` and `~/.codex/archived_sessions/**`, and the same under every `~/.codex-*` account and `$CODEX_HOME` |
| Pi | `~/.pi/agent/sessions/**` |
| OpenCode | `~/.local/share/opencode/opencode.db` (SQLite) |

Every account folder counts: one made per account by a switcher (`~/.claude-work`, `~/.codex-team`) is indexed like the default, and results name it as the hit's `account`. A folder that is a symlink to one already read is read once. `$CLAUDE_CONFIG_DIR` and `$CODEX_HOME` apply to your own home only, like the XDG variables; once an account outside your home has been indexed, the archive remembers it, so searches started without the variable (by another agent, say) still cover it.

Mnemo keeps its own state in `~/.mnemo/`:

| File | Contents |
|---|---|
| `index.db` | SQLite FTS5 index over this machine's sessions |
| `archive/` | A copy of every session log, kept after the agent deletes the original ([below](#the-session-archive)) |
| `remotes.json` | Registered remote devices |
| `node.json` | This device's name, id, relay and archive settings |
| `ssh-<name>` | SSH ControlMaster sockets |

### The session archive

Claude Code deletes session logs older than `cleanupPeriodDays` (30 days unless you change it), and Codex threads and Pi sessions can be deleted by hand. When a log is gone, its session would normally leave the index with it. To prevent that, every sync copies each Claude Code, Codex and Pi log it indexes to

```text
~/.mnemo/archive/<source>/<account>/<path inside the account>
~/.mnemo/archive/claude/.claude-work/projects/-Users-me-app/<session>.jsonl
```

and once the original is gone, Mnemo keeps the session and reads the copy. Search, `context`, `session` and `--raw` work as before, and results say `archived: true` (the CLI prints `(archived)`). Copies are written atomically, readable by you only (`0600` files in `0700` folders, since logs can contain credentials). A changed log is copied again on the next sync. Each `archive/<source>/<account>/origin.json` records which account folder it mirrors, so an index rebuilt with `mnemo upgrade` or `mnemo index --rebuild` finds archived sessions again.

- **Disk.** The archive takes about as much space as the logs: on one machine, 0.8 GB for Claude Code and 1.8 GB for Codex. `mnemo status` shows its size.
- **Off.** `mnemo node --archive off` stops copying, and sessions whose log is gone leave the index on the next sync, as before. Copies already made stay in `~/.mnemo/archive` until you delete them, and `--archive on` brings their sessions back. `MNEMO_ARCHIVE=off` (or `on`) overrides the setting for one command.
- **Forgetting a session.** Delete its copy under `~/.mnemo/archive` as well as the original log.
- **Moves.** When Codex moves a rollout to `archived_sessions`, or a session is continued under another account, the copy of the old path is not kept beside the live file, and an identical moved copy is removed. OpenCode keeps every session in one SQLite database and is not archived.
- **What it can't recover.** Logs deleted before you ran a version with the archive were never copied, so their sessions stay gone.

## Connect your agents

`mnemo setup` does all of the below for every agent it detects (a CLI on `PATH` or its config directory): it registers the MCP server with `claude mcp add`, appends `[mcp_servers.mnemo]` to `~/.codex/config.toml` after backing it up, adds `mcp.mnemo` to OpenCode's `opencode.json`, and links the Claude skill and the Pi extension. Stale symlinks from an older checkout are repaired; real files are never replaced. `--dry-run` previews, `--agent codex` limits it. The manual steps:

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

Use an absolute path, because MCP servers don't always inherit your shell's `PATH`. The generous `startup_timeout_sec` covers the first full index if the server is the first thing to build it.

### OpenCode

```json
// ~/.config/opencode/opencode.json (or $XDG_CONFIG_HOME/opencode/)
{
  "mcp": {
    "mnemo": {
      "type": "local",
      "command": ["/Users/you/.local/bin/mnemo", "mcp"],
      "enabled": true,
      "timeout": 120000
    }
  }
}
```

The `timeout` (milliseconds; OpenCode's default is 5 s) covers the first full index, like Codex's `startup_timeout_sec`. OpenCode's own sessions are indexed from `$XDG_DATA_HOME/opencode/opencode.db` (default `~/.local/share/opencode/`).

### Pi

```bash
ln -s ~/mnemo/integrations/pi/mnemo.ts ~/.pi/agent/extensions/mnemo.ts
```

The extension shells out to the CLI. It resolves the binary as `$MNEMO_BIN`, then `~/mnemo/bin/mnemo`, then `mnemo` on `PATH`.

### Skills for other agents

`integrations/skills/mnemo/SKILL.md` is a plain skill file that drives the CLI with `--json`. Symlink the directory into any agent's skills folder.

## Keeping the index fresh

Nothing to schedule. Every search, whether from MCP, Pi, the CLI or the dashboard, first runs an incremental sync of the local index, and every remote syncs its own index before answering. A sync with nothing to do takes about 0.1 s. Searches that arrive within 2 s of the last sync skip it, so a burst of queries only pays once.

If another process is writing the index, the search waits up to 10 s. If the sync still can't run, the search goes ahead on the index as it stands and warns that recent sessions may be missing.

`mnemo search --no-sync` skips the refresh, for scripts that issue many queries in a row. `mnemo index` still exists for the first full build and for forcing a sync by hand.

## Upgrading

```bash
git -C ~/mnemo pull          # or: uv tool upgrade mnemo-search / pipx upgrade mnemo-search
mnemo upgrade                # back up, rebuild, verify and swap; then update devices running older code
```

Updating to the release with the session archive changes no schema: the next sync indexes the other accounts and copies every log into `~/.mnemo/archive` once (a few seconds per GB), so run `mnemo index -v` after updating if you prefer that not to happen inside a search.

Running agent sessions pick up the new code on their next tool call: the MCP server runs each call in a fresh process. Only a release that adds or changes tools needs a new session to see them. A running dashboard restarts itself on the same port, and `mnemo link` services are restarted by `mnemo upgrade`. An older mnemo never writes to an index a newer one has upgraded, and rows an old process wrote before that guard existed are repaired on the next sync. If anything looks wrong, `mnemo upgrade --restore` brings back the previous index.

## Try it

```bash
mnemo search retry backoff                  # all agents, all devices
mnemo search 退避 重试 --source codex        # Chinese substring match, Codex only
mnemo search oom --cwd infra --since 2026-09-01 --limit 5
mnemo context <path> <lineno>               # from a hit
mnemo dashboard                             # browser UI
```

## Troubleshooting

Start with `mnemo doctor`: it checks the index, each agent's connection, your devices and links, and prints the command that fixes each problem.

| Symptom | Fix |
|---|---|
| `no matches` for something you know exists | Check `mnemo status` for the source and session counts. A session in a directory Mnemo doesn't scan (see *Where Mnemo looks*) is never indexed. |
| `no such module: fts5` | Your Python's SQLite lacks FTS5. Use the system Python on macOS or a distribution Python ≥ 3.7. |
| Agent doesn't use Mnemo on its own | Install the skill as well as the MCP server, then start a new session. |
| A remote is missing from results | Check stderr for `unreachable` warnings, then run `mnemo remote list` and the dashboard's connectivity test. See [Multi-device](multi-device.md). |
| Hit text ends with `…[truncated]` | Index bodies are capped at 20k characters. Re-read with `--raw`. |
