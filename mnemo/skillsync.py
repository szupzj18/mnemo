"""Skill sync: link skills from git repositories into agent skill folders, as a manifest says, with a lock.

The manifest (~/.mnemo/skills.toml) names the source repositories, which skills
go into which of the two shared folders (ROOTS), and which devices may receive a
source with a scope. Each device keeps a lock (~/.mnemo/skills.lock.json): every
link sync made or took over, its source commit and content hash. Earlier locks
are kept in skills.lock.history/ for rollback.

Sync only ever creates or removes symlinks it recorded, one per skill. Anything
else in a skill folder (Claude Code's synced/, real folders, links to elsewhere)
is left alone; a wanted name already taken by something else is a conflict. A
link that already points to the same folder of the source is taken over as is.

A source reaches a device by git (clone, then pull --ff-only). When that fails
on a remote device, or the device holds a copy without git history, the
controller pushes `git archive HEAD` of its own checkout with rsync.

The device side (serve) runs in-process for this device and, for a remote one,
as a program sent over SSH with this file and skills.py inside, so it needs only
python3 there, whatever mnemo version the device runs. Standard library only,
and Python 3.7.
"""
import datetime
import fnmatch
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile

from . import skills

LOCAL = "local"
ROOTS = {"claude": "~/.claude/skills", "agents": "~/.agents/skills"}
MANIFEST, LOCK, HISTORY = "skills.toml", "skills.lock.json", "skills.lock.history"
# Written into a pushed copy, so the device knows which source and commit it holds.
MARKER = ".mnemo-source.json"
CHECKOUTS = "~/.mnemo/skill-sources"
LOCK_VERSION = 1

# Source states: what inspect() found or did.
BROKEN = ("missing", "foreign")


class SyncError(Exception):
    pass


# ----------------------------------------------------------------------- toml

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _scan(text, stop):
    """Index of the first char in `stop` outside a string, or len(text)."""
    quote, i = None, 0
    while i < len(text):
        c = text[i]
        if quote:
            if c == "\\" and quote == '"':
                i += 1
            elif c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c in stop:
            return i
        i += 1
    return len(text)


def _depth(text):
    quote, depth, i = None, 0, 0
    while i < len(text):
        c = text[i]
        if quote:
            if c == "\\" and quote == '"':
                i += 1
            elif c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
        i += 1
    return depth


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"'}


def _value(text, i, lineno):
    while i < len(text) and text[i] in " \t\r\n":
        i += 1
    if i >= len(text):
        raise SyncError("line %d: missing value" % lineno)
    c = text[i]
    if c == '"':
        out, i = [], i + 1
        while i < len(text) and text[i] != '"':
            if text[i] == "\\" and i + 1 < len(text):
                if text[i + 1] not in _ESCAPES:
                    raise SyncError("line %d: unsupported escape \\%s" % (lineno, text[i + 1]))
                out.append(_ESCAPES[text[i + 1]])
                i += 2
                continue
            out.append(text[i])
            i += 1
        if i >= len(text):
            raise SyncError("line %d: unterminated string" % lineno)
        return "".join(out), i + 1
    if c == "'":
        end = text.find("'", i + 1)
        if end < 0:
            raise SyncError("line %d: unterminated string" % lineno)
        return text[i + 1:end], end + 1
    if c == "[":
        items, i = [], i + 1
        while True:
            while i < len(text) and text[i] in " \t\r\n,":
                i += 1
            if i < len(text) and text[i] == "]":
                return items, i + 1
            item, i = _value(text, i, lineno)
            items.append(item)
            while i < len(text) and text[i] in " \t\r\n":
                i += 1
            if i < len(text) and text[i] not in ",]":
                raise SyncError("line %d: expected , or ] in array" % lineno)
    m = re.compile(r"true|false|[+-]?\d+").match(text, i)
    if not m:
        raise SyncError("line %d: unsupported value %r (strings, arrays, true/false and integers only)"
                        % (lineno, text[i:i + 20]))
    word = m.group(0)
    return (word == "true") if word in ("true", "false") else int(word), m.end()


def _keys(text, lineno):
    keys, rest = [], text.strip()
    while rest:
        end = _scan(rest, ".")
        key = rest[:end].strip()
        if len(key) >= 2 and key[0] == key[-1] and key[0] in "\"'":
            key = key[1:-1]
        elif not _BARE_KEY.match(key):
            raise SyncError("line %d: bad key %r" % (lineno, key))
        keys.append(key)
        rest = rest[end + 1:]
    if not keys:
        raise SyncError("line %d: empty key" % lineno)
    return keys


def _table(base, keys, lineno):
    for k in keys:
        nxt = base.setdefault(k, {})
        if isinstance(nxt, list):
            nxt = nxt[-1]
        if not isinstance(nxt, dict):
            raise SyncError("line %d: %s is not a table" % (lineno, k))
        base = nxt
    return base


def parse_toml(text):
    """The TOML the manifest needs: tables, arrays of tables, strings, string arrays, booleans, integers.

    tomllib is Python 3.11+; devices run 3.7.
    """
    root, cur = {}, None
    cur = root
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        lineno = i + 1
        line = lines[i][:_scan(lines[i], "#")].strip()
        i += 1
        if not line:
            continue
        if line.startswith("[["):
            if not line.endswith("]]"):
                raise SyncError("line %d: bad table header" % lineno)
            keys = _keys(line[2:-2], lineno)
            parent = _table(root, keys[:-1], lineno)
            arr = parent.setdefault(keys[-1], [])
            if not isinstance(arr, list):
                raise SyncError("line %d: %s is already a table" % (lineno, keys[-1]))
            cur = {}
            arr.append(cur)
            continue
        if line.startswith("["):
            if not line.endswith("]"):
                raise SyncError("line %d: bad table header" % lineno)
            cur = _table(root, _keys(line[1:-1], lineno), lineno)
            continue
        eq = _scan(line, "=")
        if eq == len(line):
            raise SyncError("line %d: expected key = value" % lineno)
        value = line[eq + 1:]
        while _depth(value) > 0 and i < len(lines):  # an array over several lines
            value += "\n" + lines[i][:_scan(lines[i], "#")]
            i += 1
        parsed, end = _value(value, 0, lineno)
        if value[end:].strip():
            raise SyncError("line %d: unexpected %r after the value" % (lineno, value[end:].strip()[:20]))
        keys = _keys(line[:eq], lineno)
        tbl = _table(cur, keys[:-1], lineno)
        if keys[-1] in tbl:
            raise SyncError("line %d: %s is set twice" % (lineno, keys[-1]))
        tbl[keys[-1]] = parsed
    return root


# ------------------------------------------------------------------- manifest

EXAMPLE = """\
# ~/.mnemo/skills.toml
[sources.chris]
git = "https://github.com/you/your-skills.git"
path = "skills"                      # folder in the repo holding one folder per skill
checkout = "~/dev/your-skills"       # where each device keeps it (default ~/.mnemo/skill-sources/<name>)

[sources.internal]
git = "git@git.example.com:team/skills.git"
scope = "internal"                   # only devices listed in [devices] internal

[devices]
internal = ["local", "devbox-109"]

[[install]]
from = "chris"
skills = ["*"]
roots = ["claude", "agents"]         # ~/.claude/skills and ~/.agents/skills
"""


def _strings(value, what):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise SyncError("%s takes a list of strings" % what)
    return value


def _only(table, allowed, what):
    extra = sorted(set(table) - set(allowed))
    if extra:
        raise SyncError("%s: unknown key %s" % (what, ", ".join(extra)))


def manifest_from(data):
    """The manifest as sync uses it, checked: {"sources", "devices", "install"}."""
    _only(data, ("sources", "devices", "install"), "manifest")
    sources = {}
    for name, s in sorted((data.get("sources") or {}).items()):
        what = "sources.%s" % name
        if not re.match(r"^[\w.-]+$", name) or not isinstance(s, dict):
            raise SyncError("%s: a source is a table named with letters, digits, - _ ." % what)
        _only(s, ("git", "path", "checkout", "scope"), what)
        path = s.get("path", "skills")
        if not isinstance(path, str) or os.path.isabs(path) or ".." in path.split("/"):
            raise SyncError("%s: path is a folder inside the repository" % what)
        if not s.get("git") and not s.get("checkout"):
            raise SyncError("%s: needs git or checkout" % what)
        sources[name] = {"git": s.get("git") or None, "path": path.strip("/") or ".",
                         "checkout": s.get("checkout") or "%s/%s" % (CHECKOUTS, name), "scope": s.get("scope")}
    devices = {}
    for scope, names in sorted((data.get("devices") or {}).items()):
        devices[scope] = _strings(names, "devices.%s" % scope)
    for name, s in sources.items():
        if s["scope"] and s["scope"] not in devices:
            raise SyncError("sources.%s: scope %r needs a device list, [devices] %s = [...]"
                            % (name, s["scope"], s["scope"]))
    install = []
    entries = data.get("install") or []
    if not isinstance(entries, list):
        raise SyncError("install is written [[install]]")
    for n, e in enumerate(entries, 1):
        what = "install #%d" % n
        _only(e, ("from", "skills", "exclude", "roots", "devices"), what)
        if e.get("from") not in sources:
            raise SyncError("%s: from = one of the sources (%s)" % (what, ", ".join(sources) or "none defined"))
        roots = _strings(e.get("roots", sorted(ROOTS)), what + " roots")
        bad = [r for r in roots if r not in ROOTS]
        if bad:
            raise SyncError("%s: roots are %s, not %s" % (what, " and ".join(sorted(ROOTS)), ", ".join(bad)))
        install.append({"from": e["from"], "skills": _strings(e.get("skills", ["*"]), what + " skills"),
                        "exclude": _strings(e.get("exclude", []), what + " exclude") if e.get("exclude") else [],
                        "roots": roots, "devices": _strings(e.get("devices", ["*"]), what + " devices")})
    return {"sources": sources, "devices": devices, "install": install}


def load_manifest(path):
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        raise SyncError("no manifest at %s; start from this one:\n\n%s" % (path, EXAMPLE))
    try:
        return manifest_from(parse_toml(text))
    except SyncError as exc:
        raise SyncError("%s: %s" % (path, exc))


def for_device(manifest, device):
    """What one device gets: sources out of its scope are not sent to it at all."""
    sources = {n: s for n, s in manifest["sources"].items()
               if not s["scope"] or device in manifest["devices"][s["scope"]]}
    install = [e for e in manifest["install"] if e["from"] in sources
               and any(d == "*" or d == device for d in e["devices"])]
    used = {e["from"] for e in install}
    return {"sources": {n: s for n, s in sources.items() if n in used}, "install": install}


# ---------------------------------------------------------------- device side

def _expand(path):
    return os.path.abspath(os.path.expanduser(path))


def _git(args, timeout=120):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o ConnectTimeout=10")
    try:
        p = subprocess.run(["git"] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return False, "timed out after %ds" % timeout
    except OSError as exc:
        return False, str(exc)
    if p.returncode:
        err = [ln for ln in (p.stderr or p.stdout).strip().splitlines() if ln.strip()]
        return False, (err[-1] if err else "exit code %d" % p.returncode)[:200]
    return True, p.stdout.strip()


def norm_url(url):
    """github.com/u/r for https://github.com/u/r.git, git@github.com:u/r and ssh://git@github.com/u/r."""
    u = (url or "").strip().rstrip("/")
    u = re.sub(r"\.git$", "", u)
    u = re.sub(r"^[a-z+]+://", "", u)
    u = re.sub(r"^[^@/]+@", "", u)
    u = re.sub(r"^([^/:]+):(?!\d+/)", r"\1/", u)
    host, _, rest = u.partition("/")
    return host.lower() + "/" + rest


def _names(folder):
    try:
        entries = sorted(os.listdir(folder))
    except OSError:
        return []
    return [n for n in entries if not n.startswith(".")
            and os.path.isfile(os.path.join(folder, n, "SKILL.md"))]


def _marker(checkout):
    try:
        with open(os.path.join(checkout, MARKER), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def inspect(name, src, hint, mode):
    """Where a source stands on this device, after cloning or pulling it when mode is "fetch".

    mode: "plan" (dry run: only asks the remote repository whether git would work),
    "fetch" (clone or pull) or "read" (no network: a later step of the same sync).
    hint: the controller's own copy of the source ({commit, hash, names}), sent to
    remote devices; a remote that cannot get the source by git gets it pushed.
    """
    checkout = _expand(src["checkout"])
    st = {"source": name, "checkout": checkout, "git": src["git"], "path": src["path"], "kind": None,
          "commit": None, "note": None}
    folder = os.path.join(checkout, src["path"])

    def done(state, **kw):
        st.update(kw, state=state)
        st["names"] = hint["names"] if state == "push" or (state == "clone" and hint) else _names(folder)
        return st

    if not os.path.lexists(checkout):
        if not src["git"]:
            return done("missing", note="no checkout and no git url")
        if mode == "plan":
            ok, out = _git(["ls-remote", "--exit-code", src["git"], "HEAD"], timeout=60)
        elif mode == "fetch":
            os.makedirs(os.path.dirname(checkout), exist_ok=True)
            ok, out = _git(["clone", "-q", src["git"], checkout], timeout=300)
        else:
            ok, out = False, "not cloned"
        if ok:
            if mode == "plan":
                st.update(kind="git", state="clone", names=hint["names"] if hint else _peek(src))
                return st
            return done("cloned", kind="git", commit=_git(["-C", checkout, "rev-parse", "HEAD"])[1])
        return done("push" if hint else "missing", note="git clone fails: %s" % out)
    if not os.path.isdir(checkout):
        return done("foreign", note="%s is not a folder" % checkout)
    if os.path.exists(os.path.join(checkout, ".git")):
        st["kind"] = "git"
        ok, origin = _git(["-C", checkout, "remote", "get-url", "origin"])
        if src["git"] and (not ok or norm_url(origin) != norm_url(src["git"])):
            return done("foreign", note="a checkout of %s, not %s" % (origin if ok else "no origin", src["git"]))
        state, note = "ready", None
        if src["git"] and mode == "plan":
            ok, out = _git(["-C", checkout, "ls-remote", "--exit-code", "origin", "HEAD"], timeout=60)
            state, note = ("pull", None) if ok else ("stale", "git pull would fail: %s" % out)
        elif src["git"] and mode == "fetch":
            ok, out = _git(["-C", checkout, "pull", "--ff-only", "-q"], timeout=300)
            state, note = ("pulled", None) if ok else ("stale", "git pull fails: %s" % out)
        return done(state, note=note, commit=_git(["-C", checkout, "rev-parse", "HEAD"])[1] or None)
    # A copy without git history, such as one pushed earlier.
    st["kind"] = "copy"
    marker = _marker(checkout)
    if marker.get("source") not in (None, name):
        return done("foreign", note="holds source %s" % marker.get("source"))
    if not marker and not os.path.isdir(folder):
        return done("foreign", note="not a git checkout and has no %s/ folder" % src["path"])
    content = skills.tree_hash(folder, tracked=False)
    if hint and content == hint["hash"]:
        return done("current", commit=hint["commit"])
    if hint:
        return done("push", commit=marker.get("commit"), note="no git history to pull; content differs")
    return done("ready", commit=marker.get("commit"))


def _peek(src):
    """Skill names in a source not cloned yet, from a shallow clone in a temporary folder (dry run)."""
    tmp = tempfile.mkdtemp(prefix="mnemo-skill-peek-")
    try:
        ok, _ = _git(["clone", "-q", "--depth", "1", src["git"], os.path.join(tmp, "repo")], timeout=300)
        return _names(os.path.join(tmp, "repo", src["path"])) if ok else []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _wanted(manifest, states):
    """[{root, name, source, target}] in manifest order, and notes; the first entry for a (root, name) wins."""
    out, seen, notes = [], {}, []
    for e in manifest["install"]:
        st = states[e["from"]]
        if st["state"] in BROKEN:
            continue
        names = st["names"]
        for pattern in e["skills"]:
            if not any(ch in pattern for ch in "*?[") and pattern not in names:
                notes.append("%s has no skill %s" % (e["from"], pattern))
        chosen = [n for n in names if any(fnmatch.fnmatchcase(n, p) for p in e["skills"])
                  and not any(fnmatch.fnmatchcase(n, p) for p in e["exclude"])]
        for root in e["roots"]:
            for n in chosen:
                if (root, n) in seen:
                    if seen[(root, n)] != e["from"]:
                        notes.append("%s/%s comes from %s; %s's is not linked" % (root, n, seen[(root, n)], e["from"]))
                    continue
                seen[(root, n)] = e["from"]
                out.append({"root": root, "name": n, "source": e["from"],
                            "target": os.path.join(st["checkout"], st["path"], n)})
    return out, notes


def _unsafe(name):
    """Names sync never touches: hidden entries (Codex's .system) and what agents manage themselves."""
    return name.startswith(".") or any(name in v for v in skills.MANAGED.values())


def _owned(entry):
    """The link at entry["link"] is still the one the lock recorded."""
    link = entry["link"]
    return os.path.islink(link) and os.readlink(link) == entry["target"]


def _what(path):
    if os.path.islink(path):
        return "link -> %s" % os.readlink(path)
    return "real folder" if os.path.isdir(path) else "file"


def plan(manifest, states, lock):
    """The actions that bring this device's skill folders to the manifest, and notes.

    link     create a link (nothing there yet)
    adopt    a link that already resolves to the source's folder: recorded, not touched
    keep     recorded and unchanged
    relink   a link sync made now points elsewhere: replace it
    unlink   a link sync made is no longer wanted: remove it
    forget   a recorded link someone changed or removed: drop it from the lock only
    conflict the name is taken by something sync did not make: left alone
    skip     a name sync never touches
    mkdir    create a missing skill folder
    """
    wanted, notes = _wanted(manifest, states)
    recorded = {e["link"]: e for e in lock.get("skills", [])}
    actions = []
    for root in sorted({w["root"] for w in wanted}):
        folder = _expand(ROOTS[root])
        if not os.path.isdir(folder):
            if os.path.lexists(folder):
                actions.append({"action": "conflict", "root": root, "link": folder, "detail": _what(folder)})
                wanted = [w for w in wanted if w["root"] != root]
            else:
                actions.append({"action": "mkdir", "root": root, "link": folder})
    keep = set()
    for w in wanted:
        link = os.path.join(_expand(ROOTS[w["root"]]), w["name"])
        a = dict(w, link=link)
        entry = recorded.get(link)
        if _unsafe(w["name"]):
            a.update(action="skip", detail="left to the agent")
        elif not os.path.lexists(link):
            a["action"] = "link"
        elif os.path.islink(link) and os.path.realpath(link) == os.path.realpath(w["target"]):
            a.update(action="keep" if entry and entry["target"] == os.readlink(link) and entry["source"] == w["source"]
                     else "adopt", detail=os.readlink(link))
        elif entry and _owned(entry):
            a.update(action="relink", detail=os.readlink(link))
        else:
            a.update(action="conflict", detail=_what(link))
        if a["action"] in ("keep", "adopt", "relink"):
            a["origin"] = entry["origin"] if entry and a["action"] != "adopt" else "adopted"
        if a["action"] == "link":
            a["origin"] = "linked"
        if a["action"] != "conflict":
            keep.add(link)
        actions.append(a)
    for link, e in sorted(recorded.items()):
        if link in keep:
            continue
        gone = dict({k: e[k] for k in ("root", "name", "source", "target")}, link=link)
        if _owned(e) and e.get("origin") == "linked":
            gone["action"] = "unlink"
        else:
            gone.update(action="forget", detail="changed by hand" if os.path.lexists(link) else "removed by hand")
            if _owned(e):
                gone["detail"] = "taken over earlier; left in place"
        actions.append(gone)
    return actions, notes


def _symlink(target, link):
    """Point link at target, replacing an existing link in one step."""
    tmp = "%s.mnemo-%d.tmp" % (link, os.getpid())
    os.symlink(target, tmp)
    os.replace(tmp, link)


def execute(actions):
    """Carry out a plan. Each step re-checks what is on disk first; a step that no longer fits becomes a conflict."""
    for a in actions:
        kind, link = a["action"], a["link"]
        try:
            if kind == "mkdir":
                os.makedirs(link, exist_ok=True)
            elif kind == "link":
                if os.path.lexists(link):
                    a.update(action="conflict", detail="appeared during sync: " + _what(link))
                    continue
                os.symlink(a["target"], link)
            elif kind in ("relink", "unlink"):
                if not (os.path.islink(link) and os.readlink(link) == a["detail" if kind == "relink" else "target"]):
                    a.update(action="conflict", detail="changed during sync: " + _what(link))
                    continue
                if kind == "relink":
                    _symlink(a["target"], link)
                else:
                    os.unlink(link)
        except OSError as exc:
            a.update(action="error", detail=str(exc))
    return actions


def _entry(a, commit):
    files = skills.tree_files(a["link"])
    return {"name": a["name"], "source": a["source"], "root": a["root"], "link": a["link"],
            "target": os.readlink(a["link"]), "resolved": os.path.realpath(a["link"]), "origin": a["origin"],
            "commit": commit, "content_hash": skills.tree_hash(a["link"], files=files), "files": files}


def build_lock(device, actions, states):
    entries = [_entry(a, states[a["source"]]["commit"]) for a in actions
               if a["action"] in ("link", "adopt", "keep", "relink") and os.path.islink(a["link"])]
    used = sorted({e["source"] for e in entries})
    return {"version": LOCK_VERSION, "device": device, "synced_at": _now(),
            "sources": {n: {k: states[n][k] for k in ("git", "checkout", "kind", "commit")} for n in used},
            "skills": sorted(entries, key=lambda e: e["link"])}


def _now():
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _paths(state_dir):
    d = _expand(state_dir)
    return os.path.join(d, LOCK), os.path.join(d, HISTORY)


def empty_lock(device):
    return {"version": LOCK_VERSION, "device": device, "synced_at": None, "sources": {}, "skills": []}


def read_lock(state_dir, device=LOCAL):
    path = _paths(state_dir)[0]
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return empty_lock(device)
    except ValueError:
        raise SyncError("%s is not valid JSON; fix or move it before syncing" % path)


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def _same(a, b):
    strip = lambda lock: {k: v for k, v in lock.items() if k != "synced_at"}
    return strip(a) == strip(b)


def write_lock(state_dir, old, new):
    """Save new as the lock; the old one goes to the history when anything differs. True when written."""
    lock_path, history = _paths(state_dir)
    if _same(old, new):
        return False
    stamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%S%f")
    _write_json(os.path.join(history, "%s.json" % stamp), old)
    _write_json(lock_path, new)
    return True


def _history(state_dir):
    folder = _paths(state_dir)[1]
    try:
        return [os.path.join(folder, n) for n in sorted(os.listdir(folder)) if n.endswith(".json")]
    except OSError:
        return []


def _sync(req, mode):
    manifest, hints = req["manifest"], req.get("hint") or {}
    lock = read_lock(req["state_dir"], req["device"])
    skills._TRACKED.clear()  # a pull may have changed what git tracks
    states = {n: inspect(n, s, hints.get(n), mode) for n, s in sorted(manifest["sources"].items())}
    actions, notes = plan(manifest, states, lock)
    out = {"device": req["device"], "sources": list(states.values()), "actions": actions, "notes": notes}
    if req["action"] == "apply":
        pushed = set(req.get("pushed") or ())
        for name in pushed:
            states[name]["commit"] = hints[name]["commit"]
        blocked = [s["source"] for s in states.values() if s["state"] == "push" and s["source"] not in pushed]
        if blocked:
            raise SyncError("%s still needs a push" % ", ".join(blocked))
        execute(actions)
        skills._TRACKED.clear()
        out["written"] = write_lock(req["state_dir"], lock, build_lock(req["device"], actions, states))
    return out


def _rollback(req):
    """Bring this device back to the previous lock: its links, and each source's commit where possible."""
    history = _history(req["state_dir"])
    if not history:
        raise SyncError("no earlier lock to roll back to")
    with open(history[-1], encoding="utf-8") as f:
        prev = json.load(f)
    cur = read_lock(req["state_dir"], req["device"])
    before, after = {e["link"]: e for e in cur["skills"]}, {e["link"]: e for e in prev["skills"]}
    actions = []
    for link, e in sorted(before.items()):
        if link in after:
            continue
        a = dict({k: e[k] for k in ("root", "name", "source", "target")}, link=link)
        if e["origin"] == "linked" and _owned(e):
            a["action"] = "unlink"
        else:
            a.update(action="forget", detail="was there before; left in place")
        actions.append(a)
    for link, p in sorted(after.items()):
        a = dict({k: p[k] for k in ("root", "name", "source", "target")}, link=link, origin=p["origin"])
        c = before.get(link)
        if not os.path.lexists(link):
            a["action"] = "link"
        elif os.path.islink(link) and os.readlink(link) == p["target"]:
            a["action"] = "keep"
        elif c and _owned(c):
            a.update(action="relink", detail=os.readlink(link))
        else:
            a.update(action="conflict", detail=_what(link))
        actions.append(a)
    content, pushed = [], set(req.get("pushed") or ())
    for name, ps in sorted(prev["sources"].items()):
        cs = cur["sources"].get(name)
        if not cs or not ps.get("commit") or ps["commit"] == cs.get("commit"):
            continue
        step = {"source": name, "checkout": ps["checkout"], "commit": ps["commit"], "from": cs.get("commit")}
        if ps["kind"] == "copy":
            step["action"] = "pushed" if name in pushed else "push"
        else:
            head = _git(["-C", ps["checkout"], "rev-parse", "HEAD"])[1]
            clean = _git(["-C", ps["checkout"], "status", "--porcelain"])
            back = _git(["-C", ps["checkout"], "merge-base", "--is-ancestor", ps["commit"], "HEAD"])[0]
            if head != cs.get("commit") or not clean[0] or clean[1] or not back:
                step.update(action="conflict", detail="the checkout moved on or has local changes since the sync")
            else:
                step["action"] = "reset"
        content.append(step)
    out = {"device": req["device"], "actions": actions, "content": content, "lock": os.path.basename(history[-1])}
    if req.get("dry_run"):
        return out
    if any(s["action"] == "push" for s in content):
        raise SyncError("%s needs a push of its earlier commit first" % ", ".join(
            s["source"] for s in content if s["action"] == "push"))
    for s in content:
        if s["action"] == "reset":
            ok, msg = _git(["-C", s["checkout"], "reset", "-q", "--keep", s["commit"]])
            if not ok:
                s.update(action="error", detail=msg)
    execute(actions)
    lock_path = _paths(req["state_dir"])[0]
    _write_json(lock_path, prev)
    os.remove(history[-1])
    skills._TRACKED.clear()
    out["mismatches"] = verify(prev)
    return out


def verify(lock):
    """Links in lock that differ from it on disk now: [(link, what differs)]."""
    out = []
    for e in lock["skills"]:
        if not os.path.islink(e["link"]):
            out.append((e["link"], "missing"))
        elif os.readlink(e["link"]) != e["target"]:
            out.append((e["link"], "points to %s" % os.readlink(e["link"])))
        elif skills.tree_hash(e["link"]) != e["content_hash"]:
            out.append((e["link"], "content %s, lock %s" % (skills.tree_hash(e["link"]), e["content_hash"])))
    return out


def serve(req):
    """The device side of one request; returns its JSON answer.

    action: plan (dry run), prepare (clone or pull), apply (link and write the
    lock, no network), sync (prepare + apply, for this device) or rollback.
    """
    try:
        if req["action"] == "plan":
            return _sync(req, "plan")
        if req["action"] == "prepare":
            skills._TRACKED.clear()
            hints = req.get("hint") or {}
            return {"device": req["device"], "sources": [inspect(n, s, hints.get(n), "fetch")
                                                         for n, s in sorted(req["manifest"]["sources"].items())]}
        if req["action"] == "apply":
            return _sync(req, "read")
        if req["action"] == "sync":
            return _sync(dict(req, action="apply"), "fetch")
        if req["action"] == "rollback":
            return _rollback(req)
        raise SyncError("unknown action %r" % req["action"])
    except SyncError as exc:
        return {"device": req.get("device"), "error": str(exc)}


# ------------------------------------------------------------- controller side

_BOOT = """import json, sys, types
pkg = types.ModuleType("mnemo")
pkg.__path__ = []
sys.modules["mnemo"] = pkg
for name, src in json.loads(%r):
    mod = types.ModuleType("mnemo." + name)
    mod.__package__ = "mnemo"
    sys.modules["mnemo." + name] = mod
    setattr(pkg, name, mod)
    exec(compile(src, "mnemo/%%s.py" %% name, "exec"), mod.__dict__)
out = sys.modules["mnemo.skillsync"].serve(json.loads(%r))
sys.stdout.write(json.dumps(out) + "\\n")
"""


def program(req):
    """A python3 program that answers req on a device: this module and skills.py travel inside it."""
    here = os.path.dirname(os.path.abspath(__file__))
    mods = []
    for name in ("skills", "skillsync"):
        with open(os.path.join(here, name + ".py"), encoding="utf-8") as f:
            mods.append((name, f.read()))
    return _BOOT % (json.dumps(mods), json.dumps(req))


def stage(src, commit="HEAD"):
    """(folder, hint): `git archive` of this device's checkout of src, ready to push, or (None, None)."""
    checkout = _expand(src["checkout"])
    if not os.path.exists(os.path.join(checkout, ".git")):
        return None, None
    ok, sha = _git(["-C", checkout, "rev-parse", "%s^{commit}" % commit])
    if not ok:
        return None, None
    p = subprocess.run(["git", "-C", checkout, "archive", "--format=tar", sha],
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if p.returncode:
        return None, None
    folder = tempfile.mkdtemp(prefix="mnemo-skill-push-")
    with tarfile.open(fileobj=io.BytesIO(p.stdout)) as tar:
        tar.extractall(folder)
    name = src.get("name")
    with open(os.path.join(folder, MARKER), "w", encoding="utf-8") as f:
        json.dump({"source": name, "git": src["git"], "commit": sha}, f, indent=2)
        f.write("\n")
    skills_dir = os.path.join(folder, src["path"])
    return folder, {"commit": sha, "hash": skills.tree_hash(skills_dir, tracked=False), "names": _names(skills_dir)}


def push(remote, folder, dest):
    """Make dest on the device an exact copy of folder (rsync --delete; a plain copy for a local test device)."""
    from . import remote as remote_mod

    if not os.path.isabs(dest) or not re.match(r"^[\w./+-]+$", dest):
        raise remote_mod.RemoteError("%s: will not push to %r" % (remote["name"], dest))
    if remote.get("transport") == "local":
        if os.path.isdir(dest):
            shutil.rmtree(dest)
        shutil.copytree(folder, dest, symlinks=True)
        return
    ssh = " ".join(shlex.quote(a) for a in remote_mod._ssh_base(remote)[:-1])
    cmd = ["rsync", "-a", "--delete", "-e", ssh, folder + "/", "%s:%s/" % (remote["host"], dest)]
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise remote_mod.RemoteError("rsync to %s failed: %s" % (remote["name"], exc))
    if p.returncode:
        raise remote_mod.RemoteError("rsync to %s failed: %s" % (remote["name"], p.stderr.strip()[-200:]))


def _ask(remote, req):
    from . import remote as remote_mod

    out = remote_mod.remote_python(remote, program(req), timeout=900)
    try:
        return json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise remote_mod.RemoteError("%s: answered with something other than JSON" % remote["name"])


def _stages(manifest):
    folders, hints = {}, {}
    for name, src in manifest["sources"].items():
        folder, hint = stage(dict(src, name=name))
        if folder:
            folders[name], hints[name] = folder, hint
    return folders, hints


def sync_device(device, manifest, dry_run, state_dir=None, remote=None):
    """One device's answer to `skill sync`: its sources, the actions (done unless dry_run) and notes."""
    from . import remote as remote_mod

    mine = for_device(manifest, device)
    if remote is None:
        req = {"action": "plan" if dry_run else "sync", "device": device, "manifest": mine,
               "state_dir": state_dir or remote_mod.CONFIG_DIR}
        return serve(req)
    folders, hints = _stages(mine)
    try:
        base = {"device": device, "manifest": mine, "hint": hints, "state_dir": "~/.mnemo"}
        if dry_run:
            return _ask(remote, dict(base, action="plan"))
        prep = _ask(remote, dict(base, action="prepare"))
        if prep.get("error"):
            return prep
        pushed = []
        for st in prep["sources"]:
            if st["state"] == "push":
                push(remote, folders[st["source"]], st["checkout"])
                pushed.append(st["source"])
        out = _ask(remote, dict(base, action="apply", pushed=pushed))
        out["pushed"] = pushed
        out["prepared"] = prep["sources"]
        return out
    finally:
        for folder in folders.values():
            shutil.rmtree(folder, ignore_errors=True)


def rollback_device(device, manifest, dry_run, state_dir=None, remote=None):
    """One device's answer to `skill rollback`. A remote copy of a source gets its earlier commit pushed first."""
    from . import remote as remote_mod

    if remote is None:
        return serve({"action": "rollback", "device": device, "dry_run": dry_run,
                      "state_dir": state_dir or remote_mod.CONFIG_DIR})
    base = {"action": "rollback", "device": device, "state_dir": "~/.mnemo"}
    out = _ask(remote, dict(base, dry_run=True))
    if dry_run or out.get("error"):
        return out
    pushed = []
    for s in out["content"]:
        src = manifest["sources"].get(s["source"])
        if s["action"] != "push" or not src:
            continue
        folder, _ = stage(dict(src, name=s["source"]), s["commit"])
        if folder is None:
            continue
        try:
            push(remote, folder, s["checkout"])
            pushed.append(s["source"])
        finally:
            shutil.rmtree(folder, ignore_errors=True)
    return _ask(remote, dict(base, dry_run=False, pushed=pushed))


def devices_for(spec):
    """[(name, remote or None)] for --device: local (default), all, or comma-separated names."""
    from . import remote as remote_mod

    remotes = {r["name"]: r for r in remote_mod.load_remotes()}
    names = [LOCAL] + sorted(remotes) if spec == "all" else \
        [n.strip() for n in (spec or LOCAL).split(",") if n.strip()]
    out = []
    for n in names:
        if n != LOCAL and n not in remotes:
            raise SyncError("no device named %r (mnemo remote list)" % n)
        out.append((n, remotes.get(n)))
    return out
