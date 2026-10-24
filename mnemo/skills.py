"""Agent skills: which ones each device has, and how often its sessions use them.

Three read-only views, all answered by the device that holds the data:

- ls: every skill folder in the agents' user-level skill directories, as a link,
  a real folder or a dangling link, with a hash of its content;
- search: the same folders matched by their SKILL.md name and description;
- stats: skill uses found in indexed sessions (the skill_events table).

Uses come in three kinds (TRIGGERS): the user asked for the skill by name
(/name, $name, /skill:name), Claude Code's Skill tool loaded it (the model chose
it), or an agent read its SKILL.md by path (how Codex, Pi, agy and Grok load a
skill they chose, and also how any agent follows an instruction to read one).

Only the standard library is imported at module level: the event rules must run
on a stock devbox, and a read-only check can pipe this file to one over SSH.
"""
import datetime
import hashlib
import json
import os
import re
import subprocess

TRIGGERS = ("user", "tool", "read")
# Stored besides TRIGGERS, never counted on their own:
#   slash: a Claude Code slash command; a use (user) only if its name is a skill;
#   load:  Claude Code's "Base directory for this skill" message, proof the name is one.
SLASH, LOAD = "slash", "load"

# --------------------------------------------------------------------- events

_SKILL_TOOL = re.compile(r"^Skill\((\{.*\})\)\s*$", re.S)
_BASE_DIR = re.compile(r"^Base directory for this skill: (\S+)")
_SLASH = re.compile(r"<command-name>/([\w.:-]+)</command-name>")
_CODEX_INJECT = re.compile(r"<skill>\s*<name>([^<]+)</name>")
_NAMED_INJECT = re.compile(r'<skill name="([^"]+)"')
# A tool call that reads <...>/skills/<name>/SKILL.md, one rule per agent's read tool.
_READ = {
    "claude": re.compile(r'^(?:Read\(\{"file_path": "[^"]*|Bash\(\{"command": "(?:cat|sed -n \S+|head(?: -\S+)*|nl -ba)'
                         r'\s+[^"]*?)/skills/([\w.-]+)/SKILL\.md'),
    # Older Codex logs the shell as ["/bin/zsh", "-lc", "<command>"], newer ones as {"cmd": "<command>"}.
    "codex": re.compile(r'^exec_command\((?:\[[^\]]*"-lc", "|\{"cmd":\s*")(?:cat|sed -n \S+|head(?: -\S+)*|nl -ba|bat)'
                        r'\s+[^"]*?/skills/([\w.-]+)/SKILL\.md'),
    "pi": re.compile(r'^read\(\{.*"path": "[^"]*/skills/([\w.-]+)/SKILL\.md"'),
    "agy": re.compile(r'^view_file\(\{"AbsolutePath":"[^"]*/skills/([\w.-]+)/SKILL\.md"'),
    "grok": re.compile(r'^read_file\(\{"target_file": "[^"]*/skills/([\w.-]+)/SKILL\.md"'),
    "opencode": re.compile(r'^(?:read|skill)\(\{.*?/skills/([\w.-]+)/SKILL\.md'),
}
# Cheap substring test before any regex: almost no message mentions a skill.
_MARKERS = ("SKILL.md", "Skill(", "<skill", "<command-name>/", "Base directory for this skill")


def _bare(name):
    """Plugin skills are named plugin:skill; uses are counted by the skill's own name."""
    return name.strip().split(":")[-1]


def events(source, role, kind, body):
    """[(skill, trigger)] for one indexed message.

    body is the message as stored: the verbatim original when the indexer stripped
    an injected envelope (the <skill> and <command-name> wrappers live there),
    otherwise its text.
    """
    if not body or not any(m in body for m in _MARKERS):
        return []
    if kind == "tool_call":
        if source == "claude":
            m = _SKILL_TOOL.match(body)
            if m:
                try:
                    name = json.loads(m.group(1)).get("skill") or ""
                except (ValueError, AttributeError):
                    return []
                return [(_bare(name), "tool")] if name else []
        rule = _READ.get(source)
        m = rule.match(body) if rule else None
        return [(m.group(1), "read")] if m else []
    if role != "user" or kind != "text":
        return []
    if source == "claude":
        m = _BASE_DIR.match(body)
        if m:
            return [(os.path.basename(m.group(1).rstrip("/")), LOAD)]
        m = _SLASH.search(body)
        return [(_bare(m.group(1)), SLASH)] if m else []
    rule = _CODEX_INJECT if source == "codex" else _NAMED_INJECT
    return [(m.group(1).strip(), "user") for m in rule.finditer(body)]


# SQL that finds every message events() could accept: the indexer runs events()
# on each message it writes, and the v3 → v4 migration on rows matching this.
CANDIDATES = (
    "(kind = 'tool_call' AND (COALESCE(body, text) LIKE 'Skill(%' OR COALESCE(body, text) LIKE '%SKILL.md%'))"
    " OR (role = 'user' AND kind = 'text' AND (COALESCE(body, text) LIKE 'Base directory for this skill:%'"
    " OR COALESCE(body, text) LIKE '%<skill%' OR COALESCE(body, text) LIKE '%<command-name>/%'))"
)


# ------------------------------------------------------------------ inventory

# Entries an agent writes into its own skills folder: never ours to report or touch.
MANAGED = {"claude": ("synced",)}


def agent_dirs(home=None):
    """[(agent, account, path)] of the user-level skill folders on this device, each real folder once."""
    from . import xdg
    from .sources import get_sources

    home = os.path.expanduser(home or "~")
    out = []
    for name in ("claude", "codex", "grok"):
        for config in get_sources([name], home=home)[0].config_dirs():
            out.append((name, os.path.basename(config.rstrip("/")), os.path.join(config, "skills")))
    out.append(("agents", ".agents", os.path.join(home, ".agents", "skills")))
    out.append(("pi", ".pi", os.path.join(home, ".pi", "agent", "skills")))
    for sub in ("skills", "skill"):
        out.append(("opencode", "opencode", os.path.join(xdg.config(), "opencode", sub)))
    out.append(("agy", ".gemini", os.path.join(home, ".gemini", "config", "skills")))
    seen, uniq = {}, []
    for agent, account, path in out:
        if not os.path.isdir(path):
            continue
        real = os.path.realpath(path)
        if real in seen:  # ~/.claude-work/skills -> ~/.claude/skills: one folder, both accounts
            if account not in seen[real]["also"] and account != seen[real]["account"]:
                seen[real]["also"].append(account)
            continue
        entry = {"agent": agent, "account": account, "path": path, "also": []}
        seen[real] = entry
        uniq.append(entry)
    return uniq


def _repo_root(folder):
    d = folder
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


_TRACKED = {}


def _tracked(folder):
    """Files git tracks under folder (so runtime output such as logs is left out), or None outside a repo.

    One `git ls-files` per repository, however many skills it holds.
    """
    root = _repo_root(folder)
    if root is None:
        return None
    if root not in _TRACKED:
        try:
            p = subprocess.run(["git", "-C", root, "ls-files", "-z"],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20)
            _TRACKED[root] = [n for n in p.stdout.decode("utf-8", "replace").split("\0") if n] \
                if p.returncode == 0 else []
        except (OSError, subprocess.SubprocessError):
            _TRACKED[root] = []
    prefix = os.path.relpath(folder, root).replace(os.sep, "/") + "/"
    files = [os.path.join(root, n) for n in _TRACKED[root] if prefix == "./" or n.startswith(prefix)]
    return files or None


def tree_files(folder, tracked=True):
    """[{path, hash, size}] of every file under folder, sorted; inside a git repository only tracked ones.

    tracked=False walks the folder even inside a repository (a copy staged for a push).
    """
    real = os.path.realpath(folder)
    files = _tracked(real) if tracked else None
    if files is None:
        files = []
        for dirpath, dirnames, filenames in os.walk(real, followlinks=True):
            dirnames[:] = sorted(d for d in dirnames if d not in (".git", "__pycache__", "node_modules"))
            files += [os.path.join(dirpath, f) for f in filenames if f != ".DS_Store" and not f.endswith(".pyc")]
    out = []
    for path in sorted(files):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            continue
        out.append({"path": os.path.relpath(path, real).replace(os.sep, "/"),
                    "hash": hashlib.sha256(data).hexdigest(), "size": len(data)})
    return out


def tree_hash(folder, tracked=True, files=None):
    """sha256 over (relative path, content hash) of every file, first 12 hex digits."""
    h = hashlib.sha256()
    for f in tree_files(folder, tracked) if files is None else files:
        h.update(f["path"].encode("utf-8") + b"\0" + bytes.fromhex(f["hash"]))
    return h.hexdigest()[:12]


def frontmatter(folder):
    """name and description from SKILL.md's YAML front matter (simple keys and folded blocks)."""
    try:
        with open(os.path.join(folder, "SKILL.md"), encoding="utf-8", errors="replace") as f:
            text = f.read(20000)
    except OSError:
        return {}
    m = re.match(r"\A---\s*\n(.*?)\n---", text, re.S)
    if not m:
        return {}
    out, key = {}, None
    for line in m.group(1).splitlines():
        kv = re.match(r"^([A-Za-z][\w-]*):\s*(.*)$", line)
        if kv:
            key, value = kv.group(1), kv.group(2).strip()
            out[key] = "" if value in (">", "|", ">-", "|-") else value.strip("\"'")
        elif key and line.startswith((" ", "\t")):
            out[key] = (out[key] + " " + line.strip()).strip()
    return {k: out[k] for k in ("name", "description") if k in out}


def inventory(home=None, hashes=True):
    """{"dirs": [...], "skills": [...]}: every skill folder in agent_dirs(), with what it is."""
    dirs, skills, hashed = agent_dirs(home), [], {}
    for d in dirs:
        managed, count = [], 0
        for name in sorted(os.listdir(d["path"])):
            path = os.path.join(d["path"], name)
            if name.startswith(".") or name in MANAGED.get(d["agent"], ()):
                managed.append(name)
                continue
            if os.path.islink(path):
                kind = "link" if os.path.exists(path) else "dangling"
            elif os.path.isdir(path):
                kind = "dir"
            else:
                continue
            count += 1
            item = {"name": name, "agent": d["agent"], "account": d["account"], "dir": d["path"], "kind": kind}
            if kind != "dir":
                item["target"] = os.readlink(path)
            if kind != "dangling":
                real = os.path.realpath(path)
                if hashes and real not in hashed:
                    hashed[real] = tree_hash(real)
                item["hash"] = hashed.get(real)
                item.update({"desc" if k == "description" else k: v for k, v in frontmatter(real).items()
                             if k == "description"})
            skills.append(item)
        d["skills"], d["managed"] = count, managed
    return {"dirs": dirs, "skills": skills}


def issues(devices):
    """Dangling links and names whose copies differ, over one device's inventory or several.

    devices: {device: inventory}. Returns [{"skill", "issue", "where"}], issue
    being "dangling" or "forked" (n different contents).
    """
    out, by_name = [], {}
    for device, inv in sorted(devices.items()):
        for s in inv["skills"]:
            where = "%s:%s/%s" % (device, s["account"], s["name"])
            if s["kind"] == "dangling":
                out.append({"skill": s["name"], "issue": "dangling", "where": [where], "target": s.get("target")})
            elif s.get("hash"):
                by_name.setdefault(s["name"], {}).setdefault(s["hash"], []).append(where)
    for name, versions in sorted(by_name.items()):
        if len(versions) > 1:
            out.append({"skill": name, "issue": "forked", "versions": len(versions),
                        "where": sorted(w for ws in versions.values() for w in ws),
                        "hashes": {h: sorted(ws) for h, ws in versions.items()}})
    return out


def search(devices, query, limit=20):
    """Installed skills whose name or description contains every query word, best first."""
    words = [w.lower() for w in query.split() if w.strip()]
    found = {}
    for device, inv in devices.items():
        for s in inv["skills"]:
            name, desc = s["name"].lower(), (s.get("desc") or "").lower()
            if not words or not all(w in name or w in desc for w in words):
                continue
            hit = found.setdefault(s["name"], {"skill": s["name"], "desc": s.get("desc") or "", "where": [],
                                               "score": sum(3 * name.count(w) + desc.count(w) for w in words)})
            hit["where"].append("%s:%s" % (device, s["account"]))
            if not hit["desc"] and s.get("desc"):
                hit["desc"] = s["desc"]
    hits = sorted(found.values(), key=lambda h: (-h["score"], h["skill"]))[:limit]
    for h in hits:
        h["where"] = sorted(set(h["where"]))
    return hits


# ---------------------------------------------------------------------- stats

def parse_since(value, today):
    """'30d' or 'YYYY-MM-DD' -> the first day counted (a date)."""
    value = (value or "30d").strip()
    m = re.match(r"^(\d+)d$", value)
    if m:
        return today - datetime.timedelta(days=max(1, int(m.group(1))) - 1)
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("--since takes Nd (e.g. 30d) or YYYY-MM-DD, not %r" % value)


def _day(ts):
    try:
        return datetime.date(int(ts[:4]), int(ts[5:7]), int(ts[8:10]))
    except (TypeError, ValueError):
        return None


def usage(db, first_day, known=()):
    """Per (agent, skill) counts from skill_events: the window starts at first_day; totals cover all time.

    known: names that are skills on this device (installed folders). A Claude Code
    slash command counts as a use only when its name is known or a skill load
    proves it is one; /login and friends are commands, not skills.
    """
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name = 'skill_events'").fetchone():
        return []  # a v3 index nobody migrated yet
    rows = db.execute("SELECT source, skill, trigger, path, ts FROM skill_events").fetchall()
    skills = set(known) | {r[1] for r in rows if r[2] == LOAD}
    agg = {}
    for source, skill, trigger, path, ts in rows:
        if trigger == LOAD or (trigger == SLASH and skill not in skills):
            continue
        trigger = "user" if trigger == SLASH else trigger
        a = agg.setdefault((source, skill), {"agent": source, "skill": skill, "calls": 0, "sessions": set(),
                                             "user": 0, "tool": 0, "read": 0, "total": 0,
                                             "total_sessions": set(), "last": ""})
        a["total"] += 1
        a["total_sessions"].add(path)
        if (ts or "") > a["last"]:
            a["last"] = ts
        day = _day(ts)
        if day is not None and day >= first_day:
            a["calls"] += 1
            a[trigger] += 1
            a["sessions"].add(path)
    out = []
    for a in agg.values():
        a["sessions"], a["total_sessions"], a["last"] = len(a["sessions"]), len(a["total_sessions"]), a["last"][:10]
        out.append(a)
    return sorted(out, key=lambda r: (-r["calls"], -r["total"], r["agent"], r["skill"]))


COUNTS = ("calls", "sessions", "user", "tool", "read", "total", "total_sessions")


def group(rows, by):
    """Sum rows (each with device, agent, skill) over the keys not in `by`; skill is always kept.

    Session counts are summed too: one session belongs to one device and one agent,
    so the sum is exact unless rows of the same skill from several agents are merged.
    """
    keys = [k for k in ("device", "agent") if k in by] + ["skill"]
    out = {}
    for r in rows:
        k = tuple(r[x] for x in keys)
        g = out.setdefault(k, dict({x: r[x] for x in keys}, last="", **{c: 0 for c in COUNTS}))
        for c in COUNTS:
            g[c] += r[c]
        g["last"] = max(g["last"], r["last"])
    return sorted(out.values(), key=lambda r: (-r["calls"], -r["total"], tuple(r[x] for x in keys)))


def unused(devices):
    """Installed somewhere, used nowhere in the window. devices: {device: {"installed", "rows"}}."""
    installed = set().union(*(set(d["installed"]) for d in devices.values())) if devices else set()
    used = {r["skill"] for d in devices.values() for r in d["rows"] if r["calls"]}
    return sorted(installed - used)


def gaps(devices, min_sessions=3):
    """Skills an agent used in >= min_sessions sessions on one device that another device lacks.

    A device "has" a skill when it is in one of its skill folders or its sessions
    used it at any time (some skills arrive through a workspace or a plugin).
    Only skills installed in some device's skill folders are proposed.
    """
    known = set().union(*(set(d["installed"]) for d in devices.values())) if devices else set()
    has = {dev: set(d["installed"]) | {r["skill"] for r in d["rows"]} for dev, d in devices.items()}
    out = {}
    for dev, d in devices.items():
        for r in d["rows"]:
            if r["sessions"] < min_sessions or r["skill"] not in known:
                continue
            for other in devices:
                if other == dev or r["skill"] in has[other]:
                    continue
                g = out.get((r["skill"], other))
                if not g or r["sessions"] > g["sessions"]:
                    out[(r["skill"], other)] = {"skill": r["skill"], "to": other, "from": dev,
                                                "agent": r["agent"], "sessions": r["sessions"]}
    return sorted(out.values(), key=lambda g: (-g["sessions"], g["skill"], g["to"]))


def report(devices, by, since, with_unused=False, with_gaps=False, warnings=()):
    """The stats answer for several devices: rows grouped by `by`, plus the optional lists."""
    for name, d in devices.items():
        for r in d["rows"]:
            r["device"] = name
    out = {"since": since, "first_day": devices[min(devices)]["first_day"] if devices else None,
           "by": list(by), "devices": sorted(devices),
           "rows": group([r for d in devices.values() for r in d["rows"]], by), "warnings": list(warnings)}
    if with_unused:
        out["unused"] = unused(devices)
    if with_gaps:
        out["gaps"] = gaps(devices)
    return out


# ------------------------------------------------------------------- devices

LOCAL = "local"


def local(sub, db_path, since=None, no_sync=False, today=None):
    """What this device answers for `skill <sub>`: the same JSON a neighbor returns."""
    if sub in ("ls", "search"):
        return dict(inventory(hashes=sub == "ls"), warnings=[])
    from .index import SCHEMA_VERSION, Index, IndexTooNew

    warnings = []
    idx = Index(db_path)
    try:
        if not no_sync:
            try:
                idx.sync_if_stale()
            except IndexTooNew as exc:
                warnings.append("%s: %s" % (LOCAL, exc))
        if idx.stored_version != SCHEMA_VERSION:
            warnings.append("%s: index schema v%s has no skill uses yet; run `mnemo upgrade`"
                            % (LOCAL, idx.stored_version))
        first = parse_since(since, today)
        installed = sorted({s["name"] for s in inventory(hashes=False)["skills"]})
        rows = usage(idx.db, first, known=installed)
    finally:
        idx.close()
    return {"first_day": first.isoformat(), "today": today.isoformat(), "installed": installed,
            "rows": rows, "warnings": warnings}


def collect(sub, db_path, host=LOCAL, since=None, no_sync=False, today=None, query=""):
    """({device: answer}, warnings) for `skill <sub>` on this device and/or its direct neighbors.

    host: "local", "all", or comma-separated names. today: YYYY-MM-DD, fixed in tests.
    """
    day = datetime.datetime.strptime(today, "%Y-%m-%d").date() if today else datetime.datetime.utcnow().date()
    parse_since(since, day)  # a bad --since fails before any device is asked
    hosts = "all" if host == "all" else [h.strip() for h in (host or LOCAL).split(",") if h.strip()]
    devices, warnings = {}, []
    if hosts == "all" or LOCAL in hosts:
        devices[LOCAL] = local(sub, db_path, since, no_sync, day)
    if hosts == "all" or [h for h in hosts if h != LOCAL]:
        argv = [sub] + ([query] if sub == "search" else []) + (["--since", since] if since else []) \
            + (["--no-sync"] if no_sync else []) + (["--today", today] if today else [])
        found, warnings = fan_out(argv, hosts)
        devices.update(found)
    for d in devices.values():
        warnings = list(d.pop("warnings", [])) + warnings
    return devices, warnings


def fan_out(argv, hosts):
    """Run `mnemo skill <argv> --json --host local` on each wanted neighbor in parallel.

    hosts: None or "all" for every direct neighbor, else a list of names ("local"
    is answered by the caller). Returns ({name: parsed JSON}, warnings). A device
    on an older mnemo has no `skill` command; it is skipped with a warning.
    """
    from concurrent.futures import ThreadPoolExecutor

    from . import remote as remote_mod

    wanted = None if hosts in (None, "all") else [h for h in hosts if h != "local"]
    remotes = [r for r in remote_mod.load_remotes() if wanted is None or r["name"] in wanted]
    remotes = [r for r in remotes if remote_mod._link_up(r)]  # a laptop that linked in may be asleep
    missing = sorted(set(wanted or ()) - {r["name"] for r in remotes})
    warnings = ["%s: no such device or not linked right now" % m for m in missing]

    def one(r):
        try:
            out = remote_mod.remote_exec(r, ["skill"] + list(argv) + ["--json", "--host", "local"], timeout=120)
            return r["name"], json.loads(out), None
        except remote_mod.RemoteError as exc:
            msg = str(exc)
            if "invalid choice: 'skill'" in msg:
                msg = "%s: runs an older mnemo without `skill` (mnemo remote upgrade %s)" % (r["name"], r["name"])
            return r["name"], None, msg
        except ValueError:
            return r["name"], None, "%s: answered with something other than JSON" % r["name"]

    results = {}
    if remotes:
        with ThreadPoolExecutor(max_workers=min(8, len(remotes))) as pool:
            for name, data, err in pool.map(one, remotes):
                if err:
                    warnings.append(err)
                else:
                    results[name] = data
    return results, warnings
