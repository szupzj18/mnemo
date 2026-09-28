"""`mnemo setup`: connect every coding agent found on this machine.

Idempotent: anything already configured is left alone and reported as such,
so it is safe to re-run after installing a new agent or upgrading mnemo.
Config files are backed up before they are edited.
"""
import os
import shlex
import shutil
import subprocess
import sys
import time

PKG = os.path.dirname(os.path.abspath(__file__))
INTEGRATIONS = os.path.join(PKG, "integrations")
SKILL_SRC = os.path.join(INTEGRATIONS, "skills", "mnemo")
PI_SRC = os.path.join(INTEGRATIONS, "pi", "mnemo.ts")

AGENTS = ("claude", "codex", "pi")


class Step:
    """One change setup made, would make (dry run), or skipped."""

    def __init__(self, agent, action, detail, status):
        self.agent, self.action, self.detail, self.status = agent, action, detail, status

    def __repr__(self):
        return "Step(%r, %r, %r)" % (self.agent, self.action, self.status)


def mnemo_command():
    """argv agents should run for mnemo: the PATH entry if any, else this checkout's launcher."""
    found = shutil.which("mnemo")
    if found:
        return [os.path.abspath(found)]
    launcher = os.path.join(os.path.dirname(PKG), "bin", "mnemo")
    if os.path.isfile(launcher):
        return [launcher]
    return [sys.executable, "-m", "mnemo"]


def shown(argv):
    return " ".join(shlex.quote(a) for a in argv)


def home(*parts):
    return os.path.join(os.path.expanduser("~"), *parts)


def detect():
    """Agents that look installed: a CLI on PATH or their config directory."""
    return {
        "claude": bool(shutil.which("claude") or os.path.isdir(home(".claude"))),
        "codex": bool(shutil.which("codex") or os.path.isdir(home(".codex"))),
        "pi": bool(shutil.which("pi") or os.path.isdir(home(".pi", "agent"))),
    }


def _link(agent, action, src, dest, dry_run):
    """Symlink dest -> src, repairing a dangling or stale symlink; never replace real files."""
    if os.path.islink(dest):
        current = os.path.realpath(dest)
        if current == os.path.realpath(src):
            return Step(agent, action, dest, "ok")
        if not dry_run:
            os.remove(dest)
            os.symlink(src, dest)
        return Step(agent, action, "%s (was -> %s)" % (dest, os.readlink(dest) if dry_run else current), "fixed")
    if os.path.exists(dest):
        return Step(agent, action, "%s exists and is not a symlink; left alone" % dest, "skipped")
    if not dry_run:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        os.symlink(src, dest)
    return Step(agent, action, dest, "added")


def _backup(path):
    dest = "%s.bak-%s" % (path, time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(path, dest)
    return dest


def setup_claude(cmd, dry_run):
    steps = []
    claude = shutil.which("claude")
    if not claude:
        steps.append(Step("claude", "MCP server", "claude CLI not on PATH; run: claude mcp add --scope user mnemo -- %s mcp" % shown(cmd), "skipped"))
    else:
        registered = subprocess.run(
            [claude, "mcp", "get", "mnemo"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        ).returncode == 0
        if registered:
            steps.append(Step("claude", "MCP server", "already registered", "ok"))
        else:
            if not dry_run:
                p = subprocess.run(
                    [claude, "mcp", "add", "--scope", "user", "mnemo", "--"] + cmd + ["mcp"],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                )
                if p.returncode != 0:
                    steps.append(Step("claude", "MCP server", p.stdout.strip()[:200], "failed"))
                    return steps
            steps.append(Step("claude", "MCP server", "claude mcp add --scope user mnemo -- %s mcp" % shown(cmd), "added"))
    steps.append(_link("claude", "skill", SKILL_SRC, home(".claude", "skills", "mnemo"), dry_run))
    return steps


CODEX_BLOCK = """
[mcp_servers.mnemo]
command = "{command}"
args = [{args}]
startup_timeout_sec = 120
"""


def _toml_str(value):
    return '"%s"' % value.replace("\\", "\\\\").replace('"', '\\"')


def setup_codex(cmd, dry_run):
    config = home(".codex", "config.toml")
    text = ""
    if os.path.isfile(config):
        with open(config, encoding="utf-8") as f:
            text = f.read()
    if "[mcp_servers.mnemo]" in text:
        return [Step("codex", "MCP server", "%s already has [mcp_servers.mnemo]" % config, "ok")]
    args = ", ".join(_toml_str(a) for a in cmd[1:] + ["mcp"])
    block = CODEX_BLOCK.format(command=_toml_str(cmd[0])[1:-1], args=args)
    detail = config
    if not dry_run:
        os.makedirs(os.path.dirname(config), exist_ok=True)
        if text:
            detail = "%s (backup: %s)" % (config, _backup(config))
        with open(config, "a", encoding="utf-8") as f:
            f.write(("\n" if text and not text.endswith("\n") else "") + block)
    return [Step("codex", "MCP server", detail, "added")]


def setup_pi(cmd, dry_run):
    return [_link("pi", "extension", PI_SRC, home(".pi", "agent", "extensions", "mnemo.ts"), dry_run)]


SETUPS = {"claude": setup_claude, "codex": setup_codex, "pi": setup_pi}


def run(agents=None, dry_run=False):
    """Configure the requested agents (default: every one detected). Returns [Step]."""
    found = detect()
    cmd = mnemo_command()
    steps = []
    for agent in AGENTS:
        if agents is not None and agent not in agents:
            continue
        if agents is None and not found[agent]:
            steps.append(Step(agent, "detect", "not installed", "skipped"))
            continue
        steps.extend(SETUPS[agent](cmd, dry_run))
    return steps
