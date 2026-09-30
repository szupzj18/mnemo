"""`mnemo doctor`: one look at everything mnemo depends on, and how to fix what is off.

Each check is ok, warn (works, but worth fixing), fail (something does not work)
or skip (does not apply here). It reuses the commands' own logic: `setup
--dry-run` for agents, the topology probe for devices, the link services'
state, `upgrade`'s stale-process scan.
"""
import json
import os
import sqlite3
import time
import urllib.request

from . import __version__, fingerprint, link, service, upgrade
from . import remote as remote_mod
from . import setup as st
from .index import DEFAULT_DB_PATH, SCHEMA_VERSION, Index, IndexTooNew
from .remote import RemoteError

OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"
PYPI = "https://pypi.org/pypi/mnemo-search/json"
GROUPS = ("mnemo", "Agents", "Devices", "Links")


class Check:
    def __init__(self, group, name, status, detail, fix=None):
        self.group, self.name, self.status, self.detail, self.fix = group, name, status, detail, fix

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

def check_version(offline=False, fetch=None):
    if offline:
        return Check("mnemo", "version", OK, "%s (update check skipped)" % __version__)
    try:
        latest = (fetch or _latest_on_pypi)()
    except Exception:  # offline, proxy, PyPI down: not the user's problem
        return Check("mnemo", "version", OK, "%s (could not check for updates)" % __version__)
    if _version_tuple(latest) > _version_tuple(__version__):
        return Check("mnemo", "version", WARN, "%s, and %s is out" % (__version__, latest), _upgrade_hint())
    return Check("mnemo", "version", OK, "%s, the latest" % __version__)


def _latest_on_pypi():
    with urllib.request.urlopen(PYPI, timeout=5) as r:
        return json.load(r)["info"]["version"]


def check_index(db_path=None, full=False, now=None):
    db_path = db_path or DEFAULT_DB_PATH
    shown = db_path.replace(os.path.expanduser("~"), "~")
    if not os.path.isfile(db_path):
        return [Check("mnemo", "index", FAIL, "no index at %s yet" % shown, "mnemo index")]
    checks = []
    stored = upgrade._stored_version(db_path)
    try:
        idx = Index(db_path)
    except (sqlite3.Error, IndexTooNew) as exc:
        return [Check("mnemo", "index", FAIL, "%s cannot be opened: %s" % (shown, exc), "mnemo upgrade --restore")]
    try:
        sessions = sum(c["files"] for c in idx.counts().values())
        last = idx.last_sync()
        detail = "%s, %s sessions, %s" % (shown, "{:,}".format(sessions), _size(os.path.getsize(db_path)))
        detail += ", synced %s" % _ago(last, now) if last else ", never synced"
        if idx.too_new:
            checks.append(Check("mnemo", "index", WARN, detail + "; built by a newer mnemo (schema v%s)" % stored,
                                _upgrade_hint()))
        elif stored != SCHEMA_VERSION:
            checks.append(Check("mnemo", "index", WARN, detail + "; schema v%s, this mnemo uses v%s"
                                % (stored, SCHEMA_VERSION), "mnemo upgrade"))
        else:
            checks.append(Check("mnemo", "index", OK, detail))
        stale_rows = len(idx.incomplete_paths())
        if stale_rows:
            checks.append(Check("mnemo", "old rows", WARN, "%d sessions were written by an older mnemo" % stale_rows,
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
            checks.append(Check("mnemo", "integrity", OK, "quick_check passed"))
        else:
            checks.append(Check("mnemo", "integrity", FAIL, "quick_check: %s" % result,
                                "mnemo upgrade --restore (or mnemo index --rebuild)"))
    return checks


def check_processes():
    stale = upgrade.stale_processes()
    if not stale:
        return Check("mnemo", "processes", OK, "none running old code")
    return Check("mnemo", "processes", WARN, "%d running old code (pid %s)"
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
            if n.get("legacy"):
                out.append(Check("Devices", route, WARN, "reachable, runs an older mnemo", "mnemo remote upgrade"))
            elif child.get("code") != mine:
                out.append(Check("Devices", route, WARN, "reachable, %d ms, runs other code" % (n.get("ms") or 0),
                                 "mnemo remote upgrade"))
            else:
                out.append(Check("Devices", route, OK, "reachable, %d ms, same code, relay %s"
                                 % (n.get("ms") or 0, "on" if child.get("forward") else "off")))
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


def run(full=False, offline=False):
    checks = [check_version(offline)]
    checks += check_index(full=full)
    checks.append(check_processes())
    checks += check_agents()
    try:
        checks += check_devices()
    except RemoteError as exc:
        checks.append(Check("Devices", "devices", FAIL, str(exc)))
    checks += check_links()
    return checks


# --------------------------------------------------------------------- output

ICONS = {OK: "✓", WARN: "!", FAIL: "✗", SKIP: "·"}
ASCII_ICONS = {OK: "=", WARN: "!", FAIL: "x", SKIP: "-"}
COLORS = {OK: "32", WARN: "33", FAIL: "31", SKIP: "2"}


def render(checks, node_name="", color=False, unicode=True):
    icons = ICONS if unicode else ASCII_ICONS
    dot = " · " if unicode else " - "

    def paint(code, text):
        return "\033[%sm%s\033[0m" % (code, text) if color else text

    head = "mnemo doctor" + (dot + node_name if node_name else "") + dot + "mnemo " + __version__
    lines = [paint("1", head), ""]
    width = max([len(c.name) for c in checks] or [0])
    for group in GROUPS:
        rows = [c for c in checks if c.group == group]
        if not rows:
            continue
        lines.append("  " + paint("1", group))
        for c in rows:
            detail = c.detail if c.status != SKIP else paint("2", c.detail)
            lines.append("    %s %s   %s" % (paint(COLORS[c.status], icons[c.status]), c.name.ljust(width), detail))
    fails = [c for c in checks if c.status == FAIL]
    warns = [c for c in checks if c.status == WARN]
    lines.append("")
    if not fails and not warns:
        lines.append("  " + paint("1", ("→ " if unicode else "-> ") + "All good."))
        return "\n".join(lines)
    counts = []
    if fails:
        counts.append("%d problem%s" % (len(fails), "" if len(fails) == 1 else "s"))
    if warns:
        counts.append("%d warning%s" % (len(warns), "" if len(warns) == 1 else "s"))
    lines.append("  " + dot.join(counts))
    seen = []
    for c in fails + warns:
        if c.fix and c.fix not in seen:
            seen.append(c.fix)
            lines.append("  " + paint("1", ("→ " if unicode else "-> ") + c.fix))
    return "\n".join(lines)
