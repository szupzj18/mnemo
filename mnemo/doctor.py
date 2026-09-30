"""`mnemo doctor`: one look at everything mnemo depends on, and how to fix what is off.

Each check is ok, warn (works, but worth fixing), fail (something does not work)
or skip (does not apply here). It reuses the commands' own logic: `setup
--dry-run` for agents, the topology probe for devices, the link services'
state, `upgrade`'s stale-process scan.
"""
import json
import os
import sqlite3
import subprocess
import time
import urllib.request

from . import __version__, fingerprint, link, service, upgrade
from . import remote as remote_mod
from . import setup as st
from .index import DEFAULT_DB_PATH, SCHEMA_VERSION, Index, IndexTooNew
from .remote import RemoteError

OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"
PYPI = "https://pypi.org/pypi/mnemo-search/json"
MACHINE = "This machine"
GROUPS = (MACHINE, "Agents", "Devices", "Links")
DEVICE_COLUMNS = ("latency", "code", "relay")
SLOW_MS = 1000


class Check:
    def __init__(self, group, name, status, detail, fix=None, fields=None, slow=False):
        self.group, self.name, self.status, self.detail, self.fix = group, name, status, detail, fix
        self.fields = fields  # column values for groups shown as a table (devices)
        self.slow = slow

    def to_dict(self):
        return {"group": self.group, "name": self.name, "status": self.status, "detail": self.detail, "fix": self.fix}

    def __repr__(self):
        return "Check(%r, %r, %r)" % (self.group, self.name, self.status)


def _span(ts, now=None):
    secs = max(0, int((now or time.time()) - ts))
    for unit, size in (("d", 86400), ("h", 3600), ("min", 60)):
        if secs >= size:
            return "%d %s" % (secs // size, unit)
    return None


def _ago(ts, now=None):
    span = _span(ts, now)
    return span + " ago" if span else "just now"


def _size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%.0f %s" if unit == "B" else "%.1f %s") % (n, unit)
        n /= 1024.0


def _version_tuple(v):
    return tuple(int(x) if x.isdigit() else 0 for x in v.split("."))


def _upgrade_hint():
    if os.path.isdir(os.path.join(link.REPO, ".git")):
        return "git -C %s pull && mnemo upgrade (or re-run the installer)" % link.REPO.replace(
            os.path.expanduser("~"), "~")
    return "uv tool upgrade mnemo-search (or pipx upgrade mnemo-search), then mnemo upgrade"


# --------------------------------------------------------------------- checks

def _checkout_commits():
    """Commits since this version's tag in a git checkout: 0 on the tag, None if not a checkout."""
    if not os.path.exists(os.path.join(link.REPO, ".git")):  # a directory, or a file in a worktree
        return None
    try:
        out = subprocess.run(["git", "-C", link.REPO, "describe", "--tags", "--long", "--match", "v*"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    tag, _, rest = out.rpartition("-g")[0].rpartition("-")
    if tag != "v" + __version__ or not rest.isdigit():
        return None
    return int(rest)


def check_version(offline=False, fetch=None, commits=_checkout_commits):
    n = commits() if callable(commits) else commits
    here = __version__ + (" + %d unreleased commit%s" % (n, "" if n == 1 else "s") if n else "")
    parts = [here] + (["checkout"] if n is not None else [])
    if offline:
        return Check(MACHINE, "mnemo", OK, " · ".join(parts + ["update check skipped"]))
    try:
        latest = (fetch or _latest_on_pypi)()
    except Exception:  # offline, proxy, PyPI down: not the user's problem
        return Check(MACHINE, "mnemo", OK, " · ".join(parts + ["could not check for updates"]))
    if _version_tuple(latest) > _version_tuple(__version__):
        return Check(MACHINE, "mnemo", WARN, " · ".join(parts + ["%s is out" % latest]), _upgrade_hint())
    return Check(MACHINE, "mnemo", OK, " · ".join(parts + ["latest release"]))


def _latest_on_pypi():
    with urllib.request.urlopen(PYPI, timeout=5) as r:
        return json.load(r)["info"]["version"]


def check_index(db_path=None, full=False, now=None):
    db_path = db_path or DEFAULT_DB_PATH
    shown = db_path.replace(os.path.expanduser("~"), "~")
    if not os.path.isfile(db_path):
        return [Check(MACHINE, "index", FAIL, "no index at %s yet" % shown, "mnemo index")]
    checks = []
    stored = upgrade._stored_version(db_path)
    try:
        idx = Index(db_path)
    except (sqlite3.Error, IndexTooNew) as exc:
        return [Check(MACHINE, "index", FAIL, "%s cannot be opened: %s" % (shown, exc), "mnemo upgrade --restore")]
    try:
        sessions = sum(c["files"] for c in idx.counts().values())
        last = idx.last_sync()
        detail = "%s sessions · %s" % ("{:,}".format(sessions), _size(os.path.getsize(db_path)))
        detail += " · synced %s" % _ago(last, now) if last else " · never synced"
        if db_path != DEFAULT_DB_PATH:
            detail = shown + " · " + detail
        if idx.too_new:
            checks.append(Check(MACHINE, "index", WARN, detail + " · built by a newer mnemo (schema v%s)" % stored,
                                _upgrade_hint()))
        elif stored != SCHEMA_VERSION:
            checks.append(Check(MACHINE, "index", WARN, detail + " · schema v%s, this mnemo uses v%s"
                                % (stored, SCHEMA_VERSION), "mnemo upgrade"))
        else:
            checks.append(Check(MACHINE, "index", OK, detail))
        stale_rows = len(idx.incomplete_paths())
        if stale_rows:
            checks.append(Check(MACHINE, "old rows", WARN, "%d sessions were written by an older mnemo" % stale_rows,
                                "mnemo index"))
    finally:
        idx.close()
    if full:
        db = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
        try:
            result = db.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            db.close()
        if result == "ok":
            checks.append(Check(MACHINE, "integrity", OK, "quick_check passed"))
        else:
            checks.append(Check(MACHINE, "integrity", FAIL, "quick_check: %s" % result,
                                "mnemo upgrade --restore (or mnemo index --rebuild)"))
    return checks


def check_processes():
    stale = upgrade.stale_processes()
    if not stale:
        return Check(MACHINE, "processes", OK, "none running old code")
    return Check(MACHINE, "processes", WARN, "%d running old code (pid %s)"
                 % (len(stale), ", ".join(str(p) for p, _, _ in stale)),
                 "restart the agent sessions that own them")


def check_agents(steps=None):
    steps = st.run(dry_run=True) if steps is None else steps
    out = []
    for agent in st.AGENTS:
        mine = [s for s in steps if s.agent == agent]
        if not mine:
            continue
        name = st.NAMES.get(agent, agent)
        if any(s.action == "detect" for s in mine):
            out.append(Check("Agents", name, SKIP, "not installed"))
        elif any(s.status == "failed" for s in mine):
            out.append(Check("Agents", name, FAIL, "; ".join(s.detail for s in mine if s.status == "failed"),
                             "mnemo setup --agent %s" % agent))
        elif any(s.status in ("added", "fixed") for s in mine):
            todo = [s.action for s in mine if s.status in ("added", "fixed")]
            out.append(Check("Agents", name, WARN, "not connected: %s" % ", ".join(todo), "mnemo setup"))
        elif any(s.status == "skipped" for s in mine):
            first = next(s.detail for s in mine if s.status == "skipped").split("\n")[0]
            out.append(Check("Agents", name, WARN, first.replace(os.path.expanduser("~"), "~"),
                             "mnemo setup --agent %s (it shows what to add by hand)" % agent))
        else:
            out.append(Check("Agents", name, OK, " + ".join(s.action for s in mine)))
    return out


def check_devices(topology=None):
    remotes = remote_mod.load_remotes()
    if not remotes:
        return [Check("Devices", "devices", SKIP, "none registered (mnemo remote add <host>)")]
    tree = remote_mod.probe_topology() if topology is None else topology
    mine = tree.get("code") or fingerprint.code_fingerprint()
    kinds = {r["name"]: r.get("transport") for r in remotes}
    out = []

    def walk(node, prefix):
        for n in node.get("neighbors", []):
            route = prefix + n["name"]
            if n.get("seen"):
                continue
            if not prefix and kinds.get(n["name"]) == "link":
                up = link.is_up({"name": n["name"]})
                out.append(Check("Devices", route, OK if up else SKIP,
                                 "linked in, " + ("connected" if up else "not linked right now")))
                continue
            if n.get("ok") is False:
                out.append(Check("Devices", route, FAIL, "unreachable: %s" % (n.get("error") or "no answer"),
                                 "check the connection: ssh %s" % route.split("/")[0] if not prefix
                                 else "check %s from the relay in front of it" % route))
                continue
            if "ok" not in n:
                continue  # listed by a relay but beyond the hop budget
            child = n.get("node") or {}
            ms = n.get("ms") or 0
            relay = "?" if n.get("legacy") else ("on" if child.get("forward") else "off")
            code = "older mnemo" if n.get("legacy") else ("same" if child.get("code") == mine else "other")
            status, fix = (OK, None) if code == "same" else (WARN, "mnemo remote upgrade")
            detail = "reachable · %d ms · %s code · relay %s" % (ms, code, relay)
            out.append(Check("Devices", route, status, detail, fix,
                             fields=["%d ms" % ms, code, relay], slow=ms > SLOW_MS))
            walk(child, route + "/")

    walk(tree, "")
    return out


def check_links(items=None, now=None):
    items = service.status() if items is None else items
    out = []
    for it in items:
        if not it["installed"] and it["state"] in ("off", "stopped"):
            continue
        name, state = it["remote"], it["state"]
        span = _span(it["since"], now) if it.get("since") else None
        since = (" for " + span) if span else ""
        if state == "connected":
            out.append(Check("Links", name, OK, "connected%s" % since))
        elif state in ("connecting", "retrying"):
            out.append(Check("Links", name, WARN, "%s%s" % (state, (": " + it["error"]) if it.get("error") else ""),
                             "wait a minute, or check ssh %s" % name))
        elif state == "refused":
            out.append(Check("Links", name, FAIL, "refused: %s" % it.get("error"),
                             "mnemo link %s --uninstall, fix the cause, then --install again" % name))
        else:
            out.append(Check("Links", name, FAIL, "service installed but not running",
                             "mnemo link %s --allow-inbound --install" % name))
    return out


def _devices_or_error():
    try:
        return check_devices()
    except RemoteError as exc:
        return [Check("Devices", "devices", FAIL, str(exc))]


def plan(full=False, offline=False):
    """(group, run) pairs in report order, so the report can print each group as it finishes."""
    return [
        (MACHINE, lambda: [check_version(offline)] + check_index(full=full) + [check_processes()]),
        ("Agents", check_agents),
        ("Devices", _devices_or_error),
        ("Links", check_links),
    ]


def run(full=False, offline=False):
    return [c for _, checks in plan(full, offline) for c in checks()]


# --------------------------------------------------------------------- output

ICONS = {OK: "✓", WARN: "!", FAIL: "✗", SKIP: "·"}
ASCII_ICONS = {OK: "=", WARN: "!", FAIL: "x", SKIP: "-"}
COLORS = {OK: "32", WARN: "33", FAIL: "31", SKIP: "2"}


MIN_NAME = 12


class Style:
    def __init__(self, color=False, unicode=True):
        self.color, self.unicode = color, unicode
        self.icons = ICONS if unicode else ASCII_ICONS
        self.dot = " \u00b7 " if unicode else " - "
        self.arrow = "\u2192 " if unicode else "-> "

    def paint(self, code, text):
        return "\033[%sm%s\033[0m" % (code, text) if self.color else text

    def plain(self, text):
        return text if self.unicode else text.replace(" \u00b7 ", " - ")


def render_header(node_name="", style=None):
    style = style or Style()
    return style.paint("1", "mnemo doctor" + (style.dot + node_name if node_name else ""))


def render_group(group, rows, style=None):
    style = style or Style()
    if not rows:
        return ""
    width = max([MIN_NAME] + [len(c.name) for c in rows])
    table = [c for c in rows if c.fields]
    head = "  " + style.paint("1", group)
    if table:
        cols = [max([len(t)] + [len(c.fields[i]) for c in table]) for i, t in enumerate(DEVICE_COLUMNS)]
        titles = "   ".join(t.ljust(w) for t, w in zip(DEVICE_COLUMNS, cols)).rstrip()
        head = head + " " * (width + 9 - 2 - len(group)) + style.paint("2", titles)
    lines = [head]
    for c in rows:
        icon = style.paint(COLORS[c.status], style.icons[c.status])
        if c.fields:
            cells = []
            for i, (value, w) in enumerate(zip(c.fields, cols)):
                cell = value.ljust(w)
                if (i == 0 and c.slow) or (i == 1 and value != "same"):
                    cell = style.paint(COLORS[WARN], cell)
                cells.append(cell)
            detail = "   ".join(cells).rstrip()
        else:
            detail = style.plain(c.detail)
            if c.status == SKIP:
                detail = style.paint("2", detail)
        lines.append("    %s %s   %s" % (icon, c.name.ljust(width), detail))
    return "\n".join(lines)


def render_summary(checks, style=None):
    style = style or Style()
    fails = [c for c in checks if c.status == FAIL]
    warns = [c for c in checks if c.status == WARN]
    if not fails and not warns:
        passed = sum(1 for c in checks if c.status == OK)
        skipped = sum(1 for c in checks if c.status == SKIP)
        text = "All good: %d check%s passed" % (passed, "" if passed == 1 else "s")
        if skipped:
            text += style.dot + "%d skipped" % skipped
        return "  " + style.paint("1", style.arrow + text)
    counts = []
    if fails:
        counts.append("%d problem%s" % (len(fails), "" if len(fails) == 1 else "s"))
    if warns:
        counts.append("%d warning%s" % (len(warns), "" if len(warns) == 1 else "s"))
    lines = ["  " + style.dot.join(counts)]
    seen = []
    for c in fails + warns:
        if c.fix and c.fix not in seen:
            seen.append(c.fix)
            lines.append("  " + style.paint("1", style.arrow + c.fix))
    return "\n".join(lines)


def render(checks, node_name="", color=False, unicode=True):
    style = Style(color, unicode)
    parts = [render_header(node_name, style), ""]
    for group in GROUPS:
        rows = [c for c in checks if c.group == group]
        if rows:
            parts.append(render_group(group, rows, style))
    return "\n".join(parts + ["", render_summary(checks, style)])
