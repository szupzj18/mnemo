"""`mnemo setup`: connect every coding agent found on this machine.

Idempotent: anything already configured is left alone and reported as such,
so it is safe to re-run after installing a new agent or upgrading mnemo.
Config files are backed up before they are edited.
"""
import json
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

from . import xdg

AGENTS = ("claude", "codex", "opencode", "pi")


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
        "opencode": bool(shutil.which("opencode") or os.path.isdir(os.path.join(xdg.config(), "opencode"))
                         or os.path.isdir(os.path.join(xdg.data(), "opencode"))),
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


def opencode_entry(cmd):
    # The first start builds the index before answering; OpenCode's default is 5 s.
    return {"type": "local", "command": cmd + ["mcp"], "enabled": True, "timeout": 120000}


def setup_opencode(cmd, dry_run):
    folder = os.path.join(xdg.config(), "opencode")
    jsonc = os.path.join(folder, "opencode.jsonc")
    config = jsonc if os.path.isfile(jsonc) else os.path.join(folder, "opencode.json")
    snippet = '"mnemo": ' + json.dumps(opencode_entry(cmd), indent=2)
    data = {"$schema": "https://opencode.ai/config.json"}
    if os.path.isfile(config):
        with open(config, encoding="utf-8") as f:
            text = f.read()
        try:
            data = json.loads(text) if text.strip() else data
        except ValueError:
            # JSONC with comments: rewriting it would drop them.
            return [Step("opencode", "MCP server", "%s has comments, so it was left alone\n"
                         "add this inside its \"mcp\" object:\n%s" % (config, snippet), "skipped")]
        if not isinstance(data, dict) or not isinstance(data.get("mcp", {}), dict):
            return [Step("opencode", "MCP server", "%s is not a config object; left alone" % config, "skipped")]
    if "mnemo" in data.get("mcp", {}):
        return [Step("opencode", "MCP server", "%s already has mcp.mnemo" % config, "ok")]
    data.setdefault("mcp", {})["mnemo"] = opencode_entry(cmd)
    detail = config
    if not dry_run:
        os.makedirs(folder, exist_ok=True)
        if os.path.isfile(config):
            detail = "%s (backup: %s)" % (config, _backup(config))
        with open(config, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
    return [Step("opencode", "MCP server", detail, "added")]


def setup_pi(cmd, dry_run):
    return [_link("pi", "extension", PI_SRC, home(".pi", "agent", "extensions", "mnemo.ts"), dry_run)]


SETUPS = {"claude": setup_claude, "codex": setup_codex, "opencode": setup_opencode, "pi": setup_pi}


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


# ------------------------------------------------------------------- output

NAMES = {"claude": "Claude Code", "codex": "Codex", "opencode": "OpenCode", "pi": "Pi"}
ICONS = {"added": "+", "fixed": "\u21bb", "ok": "\u2713", "skipped": "\u00b7", "failed": "\u2717"}
ASCII_ICONS = {"added": "+", "fixed": "~", "ok": "=", "skipped": "-", "failed": "!"}
COLORS = {"added": "34", "fixed": "33", "ok": "32", "skipped": "2", "failed": "31"}
SUMMARY = (("added", "added", "would add"), ("fixed", "repaired", "would repair"),
           ("ok", "unchanged", "unchanged"), ("skipped", "skipped", "skipped"), ("failed", "failed", "failed"))


def _and(names):
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def render(steps, dry_run=False, color=False, unicode=True, home_dir=None):
    """Steps grouped by agent, a summary line and what to do next (plain text if color is off)."""
    icons = ICONS if unicode else ASCII_ICONS
    home_dir = home_dir or os.path.expanduser("~")

    def paint(code, text):
        return "\033[%sm%s\033[0m" % (code, text) if color else text

    def tidy(text):
        return text.replace(home_dir, "~")

    agents = [a for a in AGENTS if any(s.agent == a for s in steps)]
    found = [a for a in agents if not any(s.agent == a and s.action == "detect" for s in steps)]
    head = "mnemo setup \u00b7 %d agent%s found" % (len(found), "" if len(found) == 1 else "s")
    if not unicode:
        head = head.replace("\u00b7", "-")
    lines = [paint("1", head) + (paint("2", "  (dry run: nothing is changed)") if dry_run else ""), ""]
    width = max([len(s.action) for s in steps if s.action != "detect"] or [0])

    for agent in agents:
        lines.append("  " + paint("1", NAMES.get(agent, agent)))
        for s in (s for s in steps if s.agent == agent):
            icon = paint(COLORS[s.status], icons[s.status])
            if s.action == "detect":
                lines.append("    %s %s" % (icon, paint("2", s.detail)))
                continue
            detail, extra = tidy(s.detail), []
            if " (backup: " in detail and detail.endswith(")"):
                detail, backup = detail[:-1].split(" (backup: ", 1)
                extra.append("backup: " + os.path.basename(backup))
            if " (was -> " in detail and detail.endswith(")"):
                detail, was = detail[:-1].split(" (was -> ", 1)
                extra.append("repaired link, was -> " + was)
            first, *rest = detail.split("\n")
            lines.append("    %s %s   %s" % (icon, s.action.ljust(width), first))
            pad = " " * (width + 9)
            lines.extend(pad + paint("2", x) for x in extra + rest)

    counts = {k: sum(1 for s in steps if s.status == k) for k, _, _ in SUMMARY}
    parts = ["%d %s" % (counts[k], would if dry_run else done) for k, done, would in SUMMARY if counts[k]]
    lines += ["", "  " + (" \u00b7 " if unicode else " - ").join(parts)]

    changed = [NAMES.get(a, a) for a in agents if any(s.agent == a and s.status in ("added", "fixed") for s in steps)]
    by_hand = [NAMES.get(a, a) for a in agents
               if any(s.agent == a and s.status == "skipped" and s.action != "detect" for s in steps)]
    arrow = "\u2192" if unicode else "->"
    if counts["failed"]:
        nxt = "Fix the failed steps above, then run mnemo setup again."
    elif dry_run and changed:
        nxt = "Run mnemo setup without --dry-run to apply."
    elif changed:
        nxt = "Restart %s sessions to load mnemo." % _and(changed)
    elif by_hand:
        nxt = None
    elif found:
        nxt = "Everything is already set up."
    else:
        nxt = "No agents found. Install Claude Code, Codex, OpenCode or Pi, then run mnemo setup again."
    if nxt:
        lines.append("  " + paint("1", arrow + " " + nxt))
    if by_hand and not counts["failed"]:
        lines.append("  " + paint("1", arrow + " Finish %s by hand, as shown above." % _and(by_hand)))
    return "\n".join(lines)

