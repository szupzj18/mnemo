import concurrent.futures
import json
import mimetypes
import os
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import remote as remote_mod
from .index import DEFAULT_DB_PATH, Index, IndexTooNew
from .remote import LOCAL, RemoteError
from .search import DEFAULT_KINDS, get_session, raw_session
from .sources import SOURCES

DEFAULT_PORT = 7787

# Static export of the Next.js frontend in web/ (see web/scripts/sync-dist.mjs).
WEB_DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_dist")
TOKEN_PLACEHOLDER = b"__MNEMO_TOKEN__"
MISSING_UI = (
    b"<!doctype html><meta charset=utf-8><title>mnemo</title>"
    b"<p>mnemo web UI is not built. Run <code>pnpm install && pnpm build</code> in <code>web/</code>.</p>"
)


def local_status():
    idx = Index(DEFAULT_DB_PATH)
    try:
        counts = idx.counts()
        last = idx.last_sync()
    finally:
        idx.close()
    return {
        "db": DEFAULT_DB_PATH,
        "last_sync": last,
        "sources": {
            name: counts.get(name, {"files": 0, "messages": 0}) for name in sorted(SOURCES)
        },
        "remotes": remote_mod.load_remotes(),
    }


def node_info():
    node = remote_mod.load_node()
    return {"id": node["id"], "name": node["name"], "forward": node["forward"]}


def local_sync():
    idx = Index(DEFAULT_DB_PATH)
    try:
        return idx.sync()
    finally:
        idx.close()


def local_session(path, raw=False):
    idx = Index(DEFAULT_DB_PATH)
    try:
        return raw_session(idx, path) if raw else get_session(idx, path)
    finally:
        idx.close()


def _time(fn):
    t = time.time()
    value = fn()
    return value, int((time.time() - t) * 1000)


def ping_all(remotes):
    out = []
    for r in remotes:
        try:
            _, ms = _time(lambda r=r: remote_mod.remote_exec(r, ["--version"], timeout=12))
            out.append({"name": r["name"], "ok": True, "ms": ms})
        except RemoteError as exc:
            out.append({"name": r["name"], "ok": False, "error": str(exc)})
    return out


def diagnose_search(query, hosts, limit):
    """Federated search with per-neighbor timing for the dashboard.

    Same routing as fan_out_search (relays, dedupe, routes as host labels);
    each direct neighbor's time and hit count include whatever it relayed.
    """
    node = remote_mod.load_node()
    remotes = remote_mod.load_remotes()
    wanted = set(hosts) if hosts else None
    selected = [r for r in remotes if wanted is None or r["name"] in wanted]
    include_local = wanted is None or LOCAL in wanted
    covered = {node["id"]} | {r["node_id"] for r in selected if r.get("node_id")}
    kinds = list(DEFAULT_KINDS)
    per_host, warnings, payload = [], [], []

    jobs = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(selected) + 1) as pool:
        if include_local:
            jobs[pool.submit(_time, lambda: (remote_mod._local_search(
                DEFAULT_DB_PATH, query, None, kinds, None, None, limit,
                True, warnings), []))] = LOCAL
        for r in selected:
            def run_remote(r=r):
                return remote_mod._remote_search(
                    r, query, None, kinds, None, None, limit, True, False,
                    covered - {r.get("node_id")}, remote_mod.DEFAULT_TTL - 1)
            jobs[pool.submit(_time, run_remote)] = r["name"]
        for fut in concurrent.futures.as_completed(jobs):
            name = jobs[fut]
            try:
                (rows, remote_warnings), ms = fut.result()
            except RemoteError as exc:
                per_host.append({"host": name, "ok": False, "error": str(exc)})
                warnings.append(str(exc))
                continue
            if name == LOCAL:
                for h in rows:
                    h["host"] = LOCAL
                    h["node"] = node["id"]
            warnings.extend(remote_warnings)
            per_host.append({"host": name, "ok": True, "ms": ms, "hits": len(rows)})
            payload.append((name, rows))

    merged = remote_mod._rrf(remote_mod._dedupe(payload), limit)
    return {"per_host": per_host, "merged": merged, "warnings": warnings}


class Handler(BaseHTTPRequestHandler):
    token = ""
    server_version = "mnemo-dashboard"
    head_only = False

    def log_message(self, fmt, *args):
        return

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not self.head_only:
            self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except ValueError:
            return {}

    def _authorized(self):
        host = self.headers.get("Host", "")
        if host.split(":")[0] not in ("127.0.0.1", "localhost", "[::1]"):
            self._json({"error": "bad host"}, 403)
            return False
        if self.path.startswith("/api/") and self.headers.get("X-Dashboard-Token") != self.token:
            self._json({"error": "unauthorized"}, 403)
            return False
        return True

    def _send(self, code, body, ctype, cache="no-store"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if not self.head_only:
            self.wfile.write(body)

    def _static(self, url_path):
        """Serve the exported UI; HTML gets the per-launch API token injected."""
        root = os.path.realpath(WEB_DIST)
        if not os.path.isfile(os.path.join(root, "index.html")):
            self._send(503, MISSING_UI, "text/html; charset=utf-8")
            return
        rel = url_path.lstrip("/")
        target = os.path.realpath(os.path.join(root, rel))
        if target != root and not target.startswith(root + os.sep):
            self._json({"error": "not found"}, 404)
            return
        if os.path.isdir(target):
            target = os.path.join(target, "index.html")
        elif not os.path.isfile(target) and os.path.isfile(target + ".html"):
            target += ".html"
        code = 200
        if not os.path.isfile(target):
            code, target = 404, os.path.join(root, "404.html")
            if not os.path.isfile(target):
                self._json({"error": "not found"}, 404)
                return
        with open(target, "rb") as f:
            body = f.read()
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json", "image/svg+xml"):
            ctype += "; charset=utf-8"
        if target.endswith(".html"):
            body = body.replace(TOKEN_PLACEHOLDER, self.token.encode("ascii"))
            cache = "no-store"
        elif "/_next/static/" in target.replace(os.sep, "/"):
            cache = "public, max-age=31536000, immutable"
        else:
            cache = "no-cache"
        self._send(code, body, ctype, cache)

    def do_HEAD(self):
        # Next.js prefetches routes with HEAD; answer like GET without a body.
        # The API is POST/GET-only, so HEAD never reaches it.
        self.head_only = True
        if not self._authorized():
            return
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            self._send(405, b"", "text/plain; charset=utf-8")
        else:
            self._static(path)

    def do_GET(self):
        if not self._authorized():
            return
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            self._static(parsed.path)
            return
        if parsed.path == "/api/status":
            self._json(local_status())
            return
        if parsed.path == "/api/node":
            self._json(node_info())
            return
        if parsed.path == "/api/remote-status":
            qs = parse_qs(parsed.query)
            try:
                r = remote_mod.get_remote(qs.get("name", [""])[0])
                value, ms = _time(lambda: remote_mod.remote_status(r))
                value["name"] = r["name"]
                value["ms"] = ms
                self._json({"ok": True, "status": value})
            except RemoteError as exc:
                self._json({"ok": False, "error": str(exc)})
            return
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        if not self._authorized():
            return
        data = self._body()
        path = urlparse(self.path).path
        try:
            if path == "/api/ping":
                remotes = remote_mod.load_remotes()
                if data.get("name"):
                    remotes = [remote_mod.get_remote(data["name"])]
                self._json({"results": ping_all(remotes)})
            elif path == "/api/sync":
                if data.get("name"):
                    r = remote_mod.get_remote(data["name"])
                    value, ms = _time(lambda: remote_mod.remote_exec(r, ["index"], timeout=600))
                    self._json({"ok": True, "name": r["name"], "ms": ms, "output": value.strip()})
                else:
                    value, ms = _time(local_sync)
                    self._json({"ok": True, "ms": ms, "stats": value})
            elif path == "/api/remotes/add":
                name = (data.get("name") or "").strip()
                host = (data.get("host") or "").strip()
                if not name or not host:
                    raise RemoteError("name and host are required")
                if any(r["name"] == name for r in remote_mod.load_remotes()):
                    raise RemoteError("remote %r already registered" % name)
                remote = {"name": name, "host": host,
                          "bin": data.get("bin") or "~/mnemo/bin/mnemo"}
                logs = []
                remote_mod.install(remote, logger=logs.append)
                remote_mod.add_remote(name, host, remote["bin"])
                self._json({"ok": True, "logs": logs})
            elif path == "/api/remotes/remove":
                remote_mod.remove_remote((data.get("name") or "").strip())
                self._json({"ok": True})
            elif path == "/api/remotes/update":
                remotes = remote_mod.load_remotes()
                if data.get("name"):
                    remotes = [remote_mod.get_remote(data["name"])]
                logs = []
                for r in remotes:
                    remote_mod.install(r, logger=lambda m, n=r["name"]: logs.append(n + ": " + m))
                self._json({"ok": True, "logs": logs})
            elif path == "/api/node":
                node = remote_mod.load_node()
                if "name" in data:
                    node["name"] = remote_mod.check_node_name(data["name"])
                if "forward" in data:
                    node["forward"] = bool(data["forward"])
                remote_mod.save_node(node)
                self._json(dict(node_info(), ok=True))
            elif path == "/api/node/remote":
                name = (data.get("name") or "").strip()
                forward = data.get("forward")
                node_name = data.get("node_name")
                if node_name is not None:
                    node_name = remote_mod.check_node_name(node_name)
                if forward is None and node_name is None:
                    raise RemoteError("nothing to change")
                info = remote_mod.set_remote_node(
                    name, forward=None if forward is None else bool(forward), node_name=node_name)
                self._json({"ok": True, "node": info})
            elif path == "/api/topology":
                t, ms = _time(remote_mod.probe_topology)
                self._json({"ok": True, "ms": ms, "topology": t, "ttl": remote_mod.DEFAULT_TTL})
            elif path == "/api/search":
                query = (data.get("query") or "").strip()
                if not query:
                    raise RemoteError("query is required")
                limit = min(int(data.get("limit", 20)), 50)
                self._json(diagnose_search(query, data.get("hosts"), limit))
            elif path == "/api/session":
                path_value = (data.get("path") or "").strip()
                if not path_value:
                    raise RemoteError("path is required")
                host = data.get("host") or LOCAL
                raw = bool(data.get("raw"))
                head, tail = data.get("head"), data.get("tail")
                if host == LOCAL:
                    value = local_session(path_value, raw)
                    if value and (head or tail):
                        if head:
                            value["messages"] = value["messages"][: int(head)]
                        else:
                            value["messages"] = value["messages"][-int(tail):]
                else:
                    value = remote_mod.remote_session(
                        host, path_value,
                        head=head, tail=tail, raw=raw, timeout=180,
                    )
                if value is None:
                    self._json({"ok": False, "error": "path not in index; sync first"}, 404)
                else:
                    self._json({"ok": True, "session": value})
            else:
                self._json({"error": "not found"}, 404)
        except (RemoteError, ValueError, KeyError, IndexTooNew) as exc:
            self._json({"ok": False, "error": str(exc)}, 400)


def serve(port=DEFAULT_PORT, open_browser=True):
    # MNEMO_DASHBOARD_TOKEN pins the token so `next dev` (web/) can call the API.
    Handler.token = os.environ.get("MNEMO_DASHBOARD_TOKEN") or secrets.token_urlsafe(16)
    httpd = None
    for candidate in range(port, port + 10):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", candidate), Handler)
            port = candidate
            break
        except OSError:
            continue
    if httpd is None:
        raise SystemExit("no free port in %d-%d" % (port, port + 9))
    url = "http://127.0.0.1:%d/" % port
    if open_browser:
        webbrowser.open(url)
    print("mnemo dashboard: %s (Ctrl-C to stop)" % url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
