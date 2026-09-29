import concurrent.futures
import json
import os
import shlex
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

from .fingerprint import code_fingerprint
from .index import Index, IndexTooNew
from .search import search as local_search

CONFIG_DIR = os.path.expanduser("~/.mnemo")
CONFIG_PATH = os.path.join(CONFIG_DIR, "remotes.json")
LOCAL = "local"
RRF_K = 60
CONNECT_TIMEOUT = 8
# How many relays a search may cross; each hop adds one SSH round trip.
DEFAULT_TTL = 3
ROUTE_SEP = "/"


class RemoteError(Exception):
    pass


# Machines provisioned with a stock MIT krb5.conf (missing the corporate realm)
# keep a user-level override at ~/.krb5.conf; point ssh/GSSAPI at it when the
# launcher itself was started without KRB5_CONFIG in its environment.
_USER_KRB5_CONF = os.path.join(os.path.expanduser("~"), ".krb5.conf")
if os.path.exists(_USER_KRB5_CONF) and not os.environ.get("KRB5_CONFIG"):
    os.environ["KRB5_CONFIG"] = _USER_KRB5_CONF


# --------------------------------------------------------------------- config

def load_remotes():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    out = []
    for r in data.get("remotes", []):
        if r.get("name") and r.get("host"):
            r.setdefault("bin", "~/mnemo/bin/mnemo")
            out.append(r)
    return out


# Neighbors are searched and probed in parallel threads, each of which may learn
# something; serialize the read-modify-write so no thread's update is lost.
_remotes_lock = threading.Lock()


def update_remote(name, **fields):
    """Persist learned facts about a neighbor (its node id, protocol level)."""
    with _remotes_lock:
        remotes = load_remotes()
        changed = False
        for r in remotes:
            if r["name"] == name:
                for k, v in fields.items():
                    if r.get(k) != v:
                        r[k] = v
                        changed = True
        if changed:
            save_remotes(remotes)


def _atomic_json(path, data):
    """Write via a temp file unique to this process and thread, then rename into place."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.%d.tmp" % (path, os.getpid(), threading.get_ident())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ------------------------------------------------------------------ this node

def _node_path():
    return os.path.join(CONFIG_DIR, "node.json")


def load_node():
    """This device's identity and relay policy, created on first use.

    id       stable across renames; dedupes a device reached over several routes
    name     how this device introduces itself
    forward  relay searches and reads for neighbors (off by default: a neighbor
             may otherwise reach devices it has no direct trust with)
    """
    try:
        with open(_node_path(), encoding="utf-8") as f:
            node = json.load(f)
    except (OSError, ValueError):
        node = {}
    if not node.get("id"):
        node = {
            "id": uuid.uuid4().hex,
            "name": node.get("name") or socket.gethostname().split(".")[0] or "mnemo",
            "forward": bool(node.get("forward", False)),
        }
        save_node(node)
    node.setdefault("forward", False)
    return node


def check_node_name(name):
    name = (name or "").strip()
    if not name or len(name) > 64 or any(c.isspace() and c != " " for c in name):
        raise RemoteError("node name must be 1-64 characters on one line")
    return name


def save_node(node):
    _atomic_json(_node_path(), node)


def split_route(host):
    """'devbox-109/devbox-126' -> ('devbox-109', 'devbox-126'); 'local' -> ('local', None)."""
    host = host or LOCAL
    first, _, rest = host.partition(ROUTE_SEP)
    return first, (rest or None)


def save_remotes(remotes):
    _atomic_json(CONFIG_PATH, {"remotes": remotes})


def add_remote(name, host, bin_path="~/mnemo/bin/mnemo"):
    remotes = load_remotes()
    if any(r["name"] == name for r in remotes):
        raise RemoteError("remote %r already registered" % name)
    remotes.append({"name": name, "host": host, "bin": bin_path})
    save_remotes(remotes)


def remove_remote(name):
    remotes = load_remotes()
    kept = [r for r in remotes if r["name"] != name]
    if len(kept) == len(remotes):
        raise RemoteError("no remote named %r" % name)
    save_remotes(kept)


def get_remote(name):
    for r in load_remotes():
        if r["name"] == name:
            return r
    raise RemoteError("no remote named %r" % name)


# ------------------------------------------------------------------------ ssh

def _socket(remote):
    return os.path.join(CONFIG_DIR, "ssh-%s" % remote["name"])


def _ssh_base(remote):
    return [
        "ssh",
        "-o", "ControlMaster=auto",
        "-o", "ControlPath=" + _socket(remote),
        "-o", "ControlPersist=10m",
        "-o", "ConnectTimeout=%d" % CONNECT_TIMEOUT,
        "-o", "BatchMode=yes",
        remote["host"],
    ]


def _remote_command(remote, argv):
    bin_path = remote["bin"]
    if bin_path.startswith("~/"):
        bin_render = "~/" + shlex.quote(bin_path[2:])
    else:
        bin_render = shlex.quote(bin_path)
    parts = ["python3", bin_render] + [shlex.quote(a) for a in argv]
    return " ".join(parts)


def remote_exec(remote, argv, timeout=60):
    env = None
    if remote.get("transport") == "local":
        # Another mnemo home on this machine: used to test multi-node topologies.
        # It runs its own copy of the code once `install` gave it one.
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        own = os.path.join(remote["home"], "mnemo", "bin", "mnemo")
        cmd = [sys.executable, own if os.path.isfile(own) else os.path.join(repo, "bin", "mnemo")] + list(argv)
        env = dict(os.environ, HOME=remote["home"])
    else:
        cmd = _ssh_base(remote) + [_remote_command(remote, argv)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        raise RemoteError("%s: timed out after %ds" % (remote["name"], timeout))
    except OSError as exc:
        raise RemoteError("%s: %s" % (remote["name"], exc))
    if p.returncode != 0:
        msg = (p.stderr or p.stdout or "exit code %d" % p.returncode).strip()
        raise RemoteError("%s: %s" % (remote["name"], msg.splitlines()[-1][:200]))
    return p.stdout


def ping(remote):
    remote_exec(remote, ["--version"], timeout=CONNECT_TIMEOUT + 4)


# Remotes only run the Python package; never ship the web toolchain or caches.
RSYNC_EXCLUDES = (
    ".git", "__pycache__", "*.pyc", "index.db", ".claude",
    "node_modules", ".next", "out", "test-results", "playwright-report", ".pnpm-store",
)


def _copy_tree(src, dest):
    """rsync --delete for the local transport, without needing rsync."""
    tmp = dest + ".new"
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(src, tmp, symlinks=True, ignore=shutil.ignore_patterns(*RSYNC_EXCLUDES))
    shutil.rmtree(dest, ignore_errors=True)
    os.replace(tmp, dest)


def install(remote, logger=lambda m: None, timeout=600, build_index=True):
    """rsync this checkout to the remote; optionally build its index afterwards."""
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if remote.get("transport") == "local":
        _copy_tree(repo, os.path.join(remote["home"], "mnemo"))
        logger("code copied to %s" % remote["name"])
        if build_index:
            logger(remote_exec(remote, ["index"], timeout=timeout).strip())
        return
    excludes = []
    for pattern in RSYNC_EXCLUDES:
        excludes += ["--exclude", pattern]
    rsync = [
        "rsync", "-az", "--delete",
    ] + excludes + [
        "-e", "ssh",
        repo + "/",
        "%s:~/mnemo/" % remote["host"],
    ]
    try:
        p = subprocess.run(rsync, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RemoteError("rsync to %s failed: %s" % (remote["host"], exc))
    if p.returncode != 0:
        raise RemoteError("rsync to %s failed: %s" % (remote["host"], p.stderr.strip()[:200]))
    if not build_index:
        logger("code synced to %s" % remote["name"])
        ping(remote)
        return
    logger("code synced; building index on %s" % remote["name"])
    out = remote_exec(remote, ["index"], timeout=timeout)
    logger(out.strip())
    ping(remote)


# ----------------------------------------------------------------- search io

def search_argv(query, sources, kinds, cwd, since, limit, include_injected=False,
                relay=False, visited=(), ttl=0):
    """argv for a forwarded search.

    Legacy form pins the remote to its own index with --host local. The relay
    form (protocol 2) lets it forward to its own neighbors: --visited carries
    every node already covered (loops and duplicate fan-out stop there) and
    --ttl the hops left; the reply is an envelope with the node id and warnings.
    """
    argv = ["search", query, "--json", "--limit", str(limit)]
    if relay:
        argv += ["--relay", "--ttl", str(ttl)]
        if visited:
            argv += ["--visited", ",".join(sorted(visited))]
    else:
        argv += ["--host", LOCAL]
    if sources:
        argv += ["--source", ",".join(sources)]
    if kinds is not None:
        if kinds:
            argv += ["--kind", ",".join(kinds)]
        else:
            argv.append("--all-kinds")
    if cwd:
        argv += ["--cwd", cwd]
    if since:
        argv += ["--since", since]
    if include_injected:
        argv.append("--include-injected")
    return argv


def _relabel(remote, hits):
    """Hits from a neighbor get routes through it: local -> name, x -> name/x."""
    for h in hits:
        h["host"] = remote["name"] if h.get("host", LOCAL) == LOCAL else remote["name"] + ROUTE_SEP + h["host"]
    return hits


def _remote_search(remote, query, sources, kinds, cwd, since, limit, sync, include_injected,
                   visited=(), ttl=0):
    """Search one neighbor (and, if it relays, what lies behind it). Returns (hits, warnings)."""
    if sync:
        remote_exec(remote, ["index"], timeout=120)
    args = (query, sources, kinds, cwd, since, limit, include_injected)
    if remote.get("proto", 2) >= 2:
        try:
            out = remote_exec(remote, search_argv(*args, relay=True, visited=visited, ttl=ttl),
                              timeout=30 + 30 * ttl)
        except RemoteError as exc:
            if "unrecognized arguments" not in str(exc):
                raise
            update_remote(remote["name"], proto=1)  # an older mnemo; ask it the old way from now on
        else:
            env = json.loads(out)
            node = env.get("node") or {}
            if node.get("id"):
                update_remote(remote["name"], node_id=node["id"], proto=2)
            warnings = ["via %s: %s" % (remote["name"], w) for w in env.get("warnings", [])]
            return _relabel(remote, env.get("hits", [])), warnings
    out = remote_exec(remote, search_argv(*args), timeout=30)
    return _relabel(remote, json.loads(out)), []


def _dedupe(per_host):
    """Drop hits seen over a longer route: one device reached via A and via A/B counts once."""
    best = {}
    for _, hits in per_host:
        for h in hits:
            key = (h.get("node") or h["host"], h["path"], h["lineno"])
            hops = h["host"].count(ROUTE_SEP)
            if key not in best or hops < best[key]:
                best[key] = hops
    out = []
    for host, hits in per_host:
        kept, seen = [], set()
        for h in hits:
            key = (h.get("node") or h["host"], h["path"], h["lineno"])
            if best.get(key) == h["host"].count(ROUTE_SEP) and key not in seen:
                seen.add(key)
                kept.append(h)
                best[key] = -1  # claimed; later copies with the same hop count are dropped
        out.append((host, kept))
    return out


def _rrf(per_host, limit):
    scores = {}
    payload = {}
    for host, hits in per_host:
        for i, h in enumerate(hits):
            key = (host, h["path"], h["lineno"])
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + i + 1)
            payload[key] = h
    keys = sorted(scores, key=lambda k: (-scores[k], k[0], k[1], k[2]))
    return [payload[k] for k in keys[:limit]]


def _local_search(db_path, query, sources, kinds, cwd, since, limit,
                  sync=False, warnings=None, include_injected=False):
    idx = Index(db_path)
    try:
        if idx.too_new and warnings is not None:
            warnings.append("local index is newer than this mnemo (restart this process to "
                            "pick up the upgrade); searching it read-only")
        elif sync:
            # A failed refresh must not cost the user their results: search
            # the index as it stands and say it may be stale.
            try:
                idx.sync_if_stale()
            except (sqlite3.Error, OSError, IndexTooNew) as exc:
                if warnings is not None:
                    warnings.append("local index not refreshed (%s); results may miss recent sessions" % exc)
        return local_search(
            idx, query, sources=sources, kinds=kinds,
            cwd=cwd, since=since, limit=limit,
            include_injected=include_injected,
        )
    finally:
        idx.close()


def fan_out_search(
    index,
    query,
    sources=None,
    kinds=None,
    cwd=None,
    since=None,
    limit=20,
    hosts=None,
    sync_remotes=True,
    sync_local=True,
    include_injected=False,
    visited=(),
    ttl=DEFAULT_TTL,
):
    """Search the local index plus neighbors, and through relaying neighbors, beyond.

    Every device's index is synced incrementally before it is searched.
    hosts: optional subset of routes ("local", "devbox-109", "devbox-109/devbox-126");
      None means everything reachable.
    visited: node ids already covered upstream (set when this call relays).
    Returns (hits, warnings); each hit's host is its route from this device.
    """
    node = load_node()
    remotes = load_remotes()
    wanted = set(hosts) if hosts else None
    names = {r["name"] for r in remotes}
    if wanted is not None:
        unknown = {w for w in wanted if split_route(w)[0] not in names | {LOCAL}}
        if unknown:
            raise RemoteError("unknown host(s): %s" % ", ".join(sorted(unknown)))

    upstream = set(visited)
    selected = [
        r for r in remotes
        if (wanted is None or r["name"] in {split_route(w)[0] for w in wanted})
        and r.get("node_id") not in upstream
    ]
    include_local = wanted is None or LOCAL in wanted
    # Neighbors asked directly in this call need not be reached again through each
    # other; ones left out by a host filter stay reachable through a relay.
    covered = upstream | {node["id"]} | {r["node_id"] for r in selected if r.get("node_id")}
    warnings = []

    jobs = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(selected) + 1) as pool:
        if include_local:
            jobs[pool.submit(
                _local_search, index.db_path, query, sources, kinds, cwd, since, limit,
                sync_local, warnings, include_injected,
            )] = LOCAL
        for r in selected:
            jobs[pool.submit(
                _remote_search, r, query, sources, kinds, cwd, since, limit,
                sync_remotes, include_injected, covered - {r.get("node_id")}, max(ttl - 1, 0),
            )] = r["name"]
        per_host = []
        for fut in concurrent.futures.as_completed(jobs):
            name = jobs[fut]
            try:
                result = fut.result()
            except RemoteError as exc:
                warnings.append(str(exc))
                continue
            if name == LOCAL:
                for h in result:
                    h["host"] = LOCAL
                    h["node"] = node["id"]
                per_host.append((name, result))
            else:
                rows, remote_warnings = result
                warnings.extend(remote_warnings)
                per_host.append((name, rows))

    per_host = _dedupe(per_host)
    if wanted is not None:
        # "devbox-109" selects that device; "devbox-109/x" selects x behind it.
        per_host = [(h, [x for x in rows if x["host"] in wanted]) for h, rows in per_host]
    hits = _rrf(per_host, limit)
    return hits, warnings


def _routed_exec(route, argv, timeout):
    """Run a read on the device at `route`; each relay forwards the rest of the route.

    --relay tells the receiver the request came from a neighbor, so it applies its
    forward policy before passing it on. Older mnemo builds (protocol 1) do not
    know the flag; they can only answer for themselves.
    """
    first, rest = split_route(route)
    remote = get_remote(first)
    hop = list(argv) + (["--host", rest] if rest else [])
    if remote.get("proto", 2) < 2:
        if rest:
            raise RemoteError("%s runs an older mnemo that cannot relay; update it" % first)
        return remote_exec(remote, hop, timeout=timeout)
    try:
        return remote_exec(remote, hop + ["--relay"], timeout=timeout)
    except RemoteError as exc:
        if rest or "unrecognized arguments" not in str(exc):
            raise
        update_remote(first, proto=1)
        return remote_exec(remote, hop, timeout=timeout)


def remote_session(route, path, head=None, tail=None, raw=False, timeout=60):
    """Read a session on the device at `route` (a neighbor name or neighbor/…/device)."""
    argv = ["session", path, "--json"]
    if head is not None:
        argv += ["--head", str(head)]
    if tail is not None:
        argv += ["--tail", str(tail)]
    if raw:
        argv.append("--raw")
    return json.loads(_routed_exec(route, argv, timeout))


def remote_context(route, path, line, before, after, raw=False, timeout=30):
    """Messages around a hit on the device at `route`."""
    argv = ["context", path, str(line), "--json",
            "--before", str(before), "--after", str(after)]
    if raw:
        argv.append("--raw")
    return json.loads(_routed_exec(route, argv, timeout))


def probe_topology(visited=(), ttl=DEFAULT_TTL, relayed=False):
    """This device and, through relaying neighbors, the network reachable from it.

    Walks the same paths a search would: a neighbor asked by a neighbor
    (relayed) reports what lies behind it only if it forwards, the hop budget
    matches search, and devices already covered upstream are listed as edges
    but not probed again. Returns
      {id, name, forward, neighbors: [{name, node_id, ok, ms, error, seen, legacy,
                                       node: <same shape> | None}]}
    SSH targets are only reported to this device's own dashboard (not relayed).
    """
    node = load_node()
    out = {"id": node["id"], "name": node["name"], "forward": node["forward"],
           "code": code_fingerprint(), "neighbors": []}
    if node["id"] in set(visited) or (relayed and not node["forward"]):
        return out
    remotes = load_remotes()
    covered = set(visited) | {node["id"]} | {r["node_id"] for r in remotes if r.get("node_id")}

    def probe(r):
        entry = {"name": r["name"], "node_id": r.get("node_id"), "node": None}
        if not relayed:
            entry["host"] = r["host"]
        if r.get("node_id") in set(visited):
            entry["seen"] = True
            return entry
        if ttl <= 0:
            return entry
        argv = ["node", "--json", "--probe", "--relay", "--ttl", str(ttl - 1)]
        rest = covered - {r.get("node_id")}
        if rest:
            argv += ["--visited", ",".join(sorted(rest))]
        t = time.time()
        try:
            legacy = r.get("proto", 2) < 2
            if not legacy:
                try:
                    child = json.loads(remote_exec(r, argv, timeout=15 + 15 * ttl))
                except RemoteError as exc:
                    # Builds without the probe still answer searches; just can't map past them.
                    if "unrecognized arguments" not in str(exc) and "invalid choice" not in str(exc):
                        raise
                    legacy = True
                else:
                    entry["node"] = child
                    entry["node_id"] = child.get("id")
                    if child.get("id") and child["id"] != r.get("node_id"):
                        update_remote(r["name"], node_id=child["id"])
            if legacy:
                entry["legacy"] = True
                remote_exec(r, ["--version"], timeout=CONNECT_TIMEOUT + 4)
        except RemoteError as exc:
            entry.update(ok=False, error=str(exc), ms=int((time.time() - t) * 1000))
            return entry
        entry.update(ok=True, ms=int((time.time() - t) * 1000))
        return entry

    if remotes:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(remotes)) as pool:
            out["neighbors"] = list(pool.map(probe, remotes))
    return out


def set_remote_node(name, forward=None, node_name=None, timeout=30):
    """Change a direct neighbor's relay policy or name over SSH; returns its node info."""
    argv = ["node", "--json"]
    if forward is not None:
        argv += ["--forward", "on" if forward else "off"]
    if node_name:
        argv += ["--name", node_name]
    remote = get_remote(name)
    try:
        info = json.loads(remote_exec(remote, argv, timeout=timeout))
    except RemoteError as exc:
        if "invalid choice" in str(exc):
            raise RemoteError("%s runs an older mnemo without relaying; update its code first" % name)
        raise
    info.pop("neighbors", None)
    if info.get("id"):
        update_remote(name, node_id=info["id"])
    return info


def neighbor_code(remote):
    """The code fingerprint a neighbor runs; None for builds that predate fingerprints."""
    try:
        info = json.loads(remote_exec(remote, ["node", "--json"], timeout=CONNECT_TIMEOUT + 12))
    except RemoteError as exc:
        if "invalid choice" in str(exc):
            return None
        raise
    return info.get("code")


def upgrade_devices(routes=None, visited=(), ttl=DEFAULT_TTL, relayed=False, logger=lambda m: None):
    """Bring devices to this device's code: only those running something else.

    routes: None for every device reachable (through relays, like a search), or
      routes such as "devbox-109" / "devbox-109/devbox-126" to reach just those
      (relays on the way are brought up to date first: old code cannot relay this).
    A device asked by a neighbor (relayed) passes the request on only if it forwards.
    Each updated device gets the code by rsync, then `mnemo upgrade --if-needed`
    (an incremental sync, or a backed-up rebuild when the index schema changed).
    Returns [{route, status: updated|current|failed, error?}] and warnings.
    """
    node = load_node()
    mine = code_fingerprint()
    results, warnings = [], []
    if node["id"] in set(visited):
        return results, warnings
    if relayed and not node["forward"]:
        if routes:  # asked for devices behind it by route; a plain sweep just stops here
            warnings.append("%s does not relay; enable with `mnemo node --forward on` there" % node["name"])
        return results, warnings
    remotes = load_remotes()
    covered = set(visited) | {node["id"]} | {r["node_id"] for r in remotes if r.get("node_id")}
    wanted = None if routes is None else [split_route(w) for w in routes]

    def one(r):
        out, warn = [], []
        here = wanted is None or any(first == r["name"] and not rest for first, rest in wanted)
        deeper = None if wanted is None else [rest for first, rest in wanted if first == r["name"] and rest]
        if not here and not deeper:
            return out, warn
        if r.get("node_id") in set(visited):
            return out, warn
        try:
            if neighbor_code(r) != mine:
                logger("%s: updating code" % r["name"])
                install(r, logger=lambda m: None, build_index=False)
                remote_exec(r, ["upgrade", "--if-needed"], timeout=1800)
                status = "updated"
            else:
                status = "current"
        except RemoteError as exc:
            if here:
                out.append({"route": r["name"], "status": "failed", "error": str(exc)})
            else:
                warn.append(str(exc))
            return out, warn
        if here or status == "updated":  # a relay updated on the way is reported too
            out.append({"route": r["name"], "status": status})
        if ttl > 0 and (deeper is None or deeper):
            argv = ["remote", "upgrade", "--relay", "--json", "--ttl", str(ttl - 1)]
            rest = covered - {r.get("node_id")}
            if rest:
                argv += ["--visited", ",".join(sorted(rest))]
            argv += deeper or []
            try:
                env = json.loads(remote_exec(r, argv, timeout=3600))
            except (RemoteError, ValueError) as exc:
                warn.append("via %s: %s" % (r["name"], exc))
                return out, warn
            for item in env.get("results", []):
                item["route"] = r["name"] + ROUTE_SEP + item["route"]
                out.append(item)
            warn.extend("via %s: %s" % (r["name"], w) for w in env.get("warnings", []))
        return out, warn

    if remotes:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(remotes)) as pool:
            for out, warn in pool.map(one, remotes):
                results.extend(out)
                warnings.extend(warn)
    if wanted is not None:
        names = {r["name"] for r in remotes}
        warnings.extend("no neighbor named %r" % first for first, _ in wanted if first not in names)
    return results, warnings


def remote_status(remote, timeout=20):
    out = remote_exec(remote, ["status", "--json"], timeout=timeout)
    return json.loads(out)
