import concurrent.futures
import json
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import remote as remote_mod
from .index import DEFAULT_DB_PATH, Index
from .remote import LOCAL, RemoteError
from .search import DEFAULT_KINDS, get_session, raw_session
from .sources import SOURCES

DEFAULT_PORT = 7787


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
    remotes = remote_mod.load_remotes()
    wanted = set(hosts) if hosts else None
    selected = [r for r in remotes if wanted is None or r["name"] in wanted]
    include_local = wanted is None or LOCAL in wanted
    per_host, warnings, payload = [], [], []

    jobs = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(selected) + 1) as pool:
        if include_local:
            jobs[pool.submit(_time, lambda: remote_mod._local_search(
                DEFAULT_DB_PATH, query, None, list(DEFAULT_KINDS), None, None, limit,
                True, warnings))] = LOCAL
        for r in selected:
            def run_remote(r=r):
                return remote_mod._remote_search(
                    r, query, None, list(DEFAULT_KINDS), None, None, limit, True)
            jobs[pool.submit(_time, run_remote)] = r["name"]
        for fut in concurrent.futures.as_completed(jobs):
            name = jobs[fut]
            try:
                rows, ms = fut.result()
                per_host.append({"host": name, "ok": True, "ms": ms, "hits": len(rows)})
                for h in rows:
                    h["host"] = name
                payload.append((name, rows))
            except RemoteError as exc:
                per_host.append({"host": name, "ok": False, "error": str(exc)})
                warnings.append(str(exc))

    merged = remote_mod._rrf(payload, limit)
    return {"per_host": per_host, "merged": merged, "warnings": warnings}


class Handler(BaseHTTPRequestHandler):
    token = ""
    server_version = "mnemo-dashboard"

    def log_message(self, fmt, *args):
        return

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
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

    def do_GET(self):
        if not self._authorized():
            return
        parsed = urlparse(self.path)
        if parsed.path == "/":
            body = PAGE.replace("__TOKEN__", self.token).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/status":
            self._json(local_status())
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
                        remote_mod.get_remote(host), path_value,
                        head=head, tail=tail, raw=raw, timeout=180,
                    )
                if value is None:
                    self._json({"ok": False, "error": "path not in index; sync first"}, 404)
                else:
                    self._json({"ok": True, "session": value})
            else:
                self._json({"error": "not found"}, 404)
        except (RemoteError, ValueError, KeyError) as exc:
            self._json({"ok": False, "error": str(exc)}, 400)


def serve(port=DEFAULT_PORT, open_browser=True):
    Handler.token = secrets.token_urlsafe(16)
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


PAGE = r"""<!doctype html>
<html lang="zh" data-theme="light"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="token" content="__TOKEN__">
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"%3E%3Crect width="64" height="64" rx="15" fill="#0f1115"/%3E%3Cpath d="M17 46V19l15 16 15-16v27" fill="none" stroke="#fff" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/%3E%3Ccircle cx="17" cy="46" r="5.6" fill="#fff"/%3E%3Ccircle cx="17" cy="19" r="5.6" fill="#fff"/%3E%3Ccircle cx="47" cy="19" r="5.6" fill="#fff"/%3E%3Ccircle cx="47" cy="46" r="5.6" fill="#fff"/%3E%3Ccircle cx="32" cy="35" r="6.6" fill="#4176e6" stroke="#0f1115" stroke-width="2"/%3E%3C/svg%3E">
<title>mnemo 管理面板</title>
<style>
:root {
  --bg: #ffffff;
  --surface: #ffffff;
  --surface-2: #ffffff;
  --surface-soft: #f5f6f7;
  --border: rgba(0,0,0,.08);
  --border-strong: rgba(0,0,0,.12);
  --text: #0f1115; --text-2: #61666b; --text-3: #81858c;
  --accent: #4176e6; --accent-soft: #e4edfd; --accent-border: rgba(65,118,230,.35);
  --ok: #22c55e; --ok-soft: rgba(34,197,94,.12);
  --warn: #f59e0b; --warn-soft: rgba(245,158,11,.12);
  --err: #ec1313; --err-soft: rgba(236,19,19,.08);
  --sidebar-bg: #f9fafb;
  --hover: rgba(38,49,72,.06);
  --cta: #0f1115; --cta-hover: #43454a; --cta-text: #ffffff;
  --shadow: 0 2px 4px rgba(0,0,0,.05);
  --shadow-pop: 0 0 1px rgba(0,0,0,.2), 0 12px 32px rgba(0,0,0,.08);
  --r-lg: 14px; --r-md: 10px; --r-sm: 8px;
}
[data-theme="dark"] {
  --bg: #151517;
  --surface: #232325;
  --surface-2: #2c2c2e;
  --surface-soft: #2c2c2e;
  --border: rgba(255,255,255,.08);
  --border-strong: rgba(255,255,255,.14);
  --text: #fafafb; --text-2: #cfd3d6; --text-3: #81858c;
  --accent: #5686fe; --accent-soft: rgba(86,134,254,.18); --accent-border: rgba(86,134,254,.4);
  --ok: #22c55e; --ok-soft: rgba(34,197,94,.16);
  --warn: #f59e0b; --warn-soft: rgba(245,158,11,.16);
  --err: #f25a5a; --err-soft: rgba(242,90,90,.14);
  --sidebar-bg: #1b1b1c;
  --hover: rgba(255,255,255,.08);
  --cta: #fafafb; --cta-hover: #e5ebf2; --cta-text: #0f1115;
  --shadow: 0 2px 4px rgba(0,0,0,.3);
  --shadow-pop: 0 0 1px rgba(0,0,0,.4), 0 12px 32px rgba(0,0,0,.5);
}
* { box-sizing: border-box; }
body { margin:0; font:14px/1.55 -apple-system,BlinkMacSystemFont,"PingFang SC","Segoe UI",sans-serif;
       color:var(--text); background:var(--bg); min-height:100vh;
       -webkit-font-smoothing:antialiased; -moz-osx-font-smoothing:grayscale; }
code,.mono { font-family:"SF Mono",ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px; }
::-webkit-scrollbar { width:8px; height:8px; }
::-webkit-scrollbar-track { background:transparent; }
::-webkit-scrollbar-thumb { border-radius:4px; background:rgba(0,0,0,.18); }
::-webkit-scrollbar-thumb:hover { background:rgba(0,0,0,.3); }
[data-theme="dark"] ::-webkit-scrollbar-thumb { background:rgba(255,255,255,.22); }
[data-theme="dark"] ::-webkit-scrollbar-thumb:hover { background:rgba(255,255,255,.32); }

/* layout */
.sidebar { position:fixed; inset:0 auto 0 0; width:210px; padding:18px 14px; z-index:20;
  background:var(--sidebar-bg);
  border-right:1px solid var(--border); display:flex; flex-direction:column; gap:6px; }
.brand { display:flex; align-items:center; gap:10px; padding:4px 10px 18px; font-weight:700; font-size:15px; }
.brand .logo { width:28px; height:28px; flex:none; display:grid; }
.nav { display:flex; flex-direction:column; gap:3px; }
.nav a { display:flex; align-items:center; gap:11px; padding:9px 12px; border-radius:var(--r-md);
  color:var(--text-2); text-decoration:none; cursor:pointer; font-weight:500; transition:background .15s; }
.nav a:hover { background:var(--hover); }
.nav a.active { background:var(--surface-soft); color:var(--text); }
.nav a.active svg { color:var(--accent); }
.nav svg { width:18px; height:18px; flex:none; }
.sidebar-foot { margin-top:auto; padding:10px 12px; color:var(--text-3); font-size:11.5px; }

.main { margin-left:210px; padding:18px 28px 40px; }
.header { display:flex; align-items:center; gap:12px; margin-bottom:18px; }
.header h1 { font-size:19px; margin:0; font-weight:600; }
.header .spacer { flex:1; }
.iconbtn { display:inline-flex; align-items:center; gap:6px; }
.iconbtn svg { width:15px; height:15px; }

.card { background:var(--surface);
  border:1px solid var(--border); border-radius:var(--r-lg); padding:20px 22px; margin-bottom:18px; }
.card h2 { font-size:14px; font-weight:600; margin:0 0 14px; display:flex; align-items:center; gap:8px; }
.card h2 .spacer { flex:1; }
.grid { display:grid; gap:14px; }
.g4 { grid-template-columns:repeat(auto-fill,minmax(220px,1fr)); }
.g3 { grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); }

/* status banner */
.banner { display:flex; align-items:center; gap:18px; flex-wrap:wrap; }
.dot { width:9px; height:9px; border-radius:50%; background:var(--ok); box-shadow:0 0 0 4px var(--ok-soft); flex:none; }
.dot.bad { background:var(--err); box-shadow:0 0 0 4px var(--err-soft); }
.banner .k { color:var(--text-3); font-size:12px; }
.banner .v { font-weight:600; }
.pill { display:inline-flex; align-items:center; gap:5px; padding:2px 10px; border-radius:12px; font-size:12px; font-weight:500; }
.pill.ok { background:var(--ok-soft); color:var(--ok); } .pill.bad { background:var(--err-soft); color:var(--err); }
.pill.warn { background:var(--warn-soft); color:var(--warn); } .pill.idle { background:var(--surface-soft); color:var(--text-3); }

/* stat cards */
.stat { background:var(--surface-2); border:1px solid var(--border); border-radius:var(--r-md); padding:15px 17px; }
.stat .top { display:flex; align-items:center; gap:10px; color:var(--text-3); font-size:12.5px; }
.stat .ico { width:30px; height:30px; border-radius:8px; display:grid; place-items:center; background:var(--accent-soft); color:var(--accent); }
.stat .ico svg { width:16px; height:16px; }
.stat .num { font-size:26px; font-weight:700; margin:8px 0 2px; letter-spacing:-.02em; }
.stat .sub { color:var(--text-3); font-size:11.5px; }

/* device card */
.dev { background:var(--surface-2); border:1px solid var(--border); border-radius:var(--r-md); padding:16px 18px; display:flex; flex-direction:column; gap:10px; }
.dev .head { display:flex; align-items:center; gap:9px; }
.dev .name { font-weight:600; font-size:14.5px; }
.health { margin-left:auto; width:30px; height:30px; border-radius:50%; display:grid; place-items:center; }
.health.ok { background:var(--ok-soft); color:var(--ok); } .health.bad { background:var(--err-soft); color:var(--err); }
.health.unknown { background:var(--surface-soft); color:var(--text-3); }
.health svg { width:15px; height:15px; }
.dev .meta { color:var(--text-2); font-size:12px; display:grid; gap:3px; }
.dev .meta b { color:var(--text); font-weight:500; }
.dev .acts { display:flex; gap:7px; flex-wrap:wrap; margin-top:2px; }
/* device status cards (dashboard) */
.card h2 .h2sub { font-weight:400; font-size:12.5px; color:var(--text-3); margin-left:4px; }
.dstat { position:relative; background:var(--surface-2); border:1px solid var(--border); border-radius:16px;
  padding:18px 20px 16px; display:flex; flex-direction:column; gap:14px; min-height:176px;
  transition:border-color .15s, box-shadow .15s; }
.dstat:hover { border-color:var(--border-strong); box-shadow:var(--shadow); }
.dstat .top { display:flex; align-items:center; gap:14px; min-width:0; }
.dstat .av { position:relative; flex:none; width:52px; height:52px; border-radius:50%; display:grid; place-items:center;
  background:hsl(var(--h) 70% 92%); color:hsl(var(--h) 45% 38%); }
.dstat .av svg { width:24px; height:24px; }
.dstat .av .dot { position:absolute; right:0; bottom:1px; width:13px; height:13px; border-radius:50%;
  background:var(--text-3); box-shadow:0 0 0 3px var(--surface-2); }
.dstat .av .dot.ok { background:var(--ok); } .dstat .av .dot.bad { background:var(--err); }
.dstat .av .dot.checking { background:var(--warn); animation:pulse 1.2s ease-in-out infinite; }
@keyframes pulse { 50% { opacity:.35; } }
.dstat .who { min-width:0; display:flex; flex-direction:column; align-items:flex-start; gap:6px; }
.dstat .nm { font-size:16px; font-weight:700; letter-spacing:-.01em; max-width:100%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.dstat .tag { max-width:100%; padding:2px 10px; border-radius:999px; border:1px solid var(--border-strong);
  background:var(--surface-soft); color:var(--text-2); font-size:12px; font-weight:500;
  font-family:ui-monospace,SFMono-Regular,Menlo,monospace; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.dstat .desc { color:var(--text-2); font-size:13.5px; line-height:1.55; }
.dstat .desc .err { display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; word-break:break-all; }
.dstat .desc .srcs { display:block; margin-top:3px; color:var(--text-3); font-size:12px; }
.dstat .foot { margin-top:auto; display:flex; align-items:center; gap:8px; }
.dstat .badge { padding:3px 11px; border-radius:8px; font-size:12.5px; font-weight:600; }
.dstat .badge.ok { background:var(--ok-soft); color:var(--ok); } .dstat .badge.bad { background:var(--err-soft); color:var(--err); }
.dstat .badge.checking { background:var(--warn-soft); color:var(--warn); } .dstat .badge.unknown { background:var(--surface-soft); color:var(--text-3); }
.dstat .ago { margin-left:auto; color:var(--text-3); font-size:12.5px; font-variant-numeric:tabular-nums; }
.dstat .act { width:28px; height:28px; padding:0; display:grid; place-items:center; border-radius:8px; opacity:0; transition:opacity .15s; }
.dstat:hover .act, .dstat .act:focus-visible, .dstat .act:disabled { opacity:1; }
.dstat .act svg { width:14px; height:14px; }
.dstat .act:disabled svg { animation:rot .8s linear infinite; }
@media (hover:none) { .dstat .act { opacity:1; } }
.spin { display:inline-block; width:12px; height:12px; border:2px solid currentColor; border-top-color:transparent; border-radius:50%; animation:rot .7s linear infinite; vertical-align:-2px; }
@keyframes rot { to { transform:rotate(360deg); } }

button { font:inherit; font-size:12.5px; font-weight:500; border-radius:14px; cursor:pointer;
  border:1px solid transparent; background:transparent; color:var(--text-2); padding:6px 14px; transition:background .15s,color .15s; }
button:hover { background:var(--hover); color:var(--text); }
button.primary { background:var(--cta); border-color:transparent; color:var(--cta-text); }
button.primary:hover { background:var(--cta-hover); color:var(--cta-text); }
button.danger { color:var(--err); } button.danger:hover { border-color:transparent; background:var(--err-soft); color:var(--err); }
button:disabled { opacity:.4; cursor:wait; }
input,select { font:inherit; font-size:13px; background:var(--surface-2); border:1px solid var(--border);
  color:var(--text); border-radius:var(--r-sm); padding:7px 11px; outline:none; transition:border-color .15s; }
input:focus { border-color:var(--accent); box-shadow:0 0 0 3px rgba(65,118,230,.12); }
[data-theme="dark"] input:focus { box-shadow:0 0 0 3px rgba(86,134,254,.18); }
label.chk { display:inline-flex; align-items:center; gap:6px; color:var(--text-2); font-size:12.5px; cursor:pointer; user-select:none; }

table { width:100%; border-collapse:collapse; font-size:12.5px; }
th { text-align:left; color:var(--text-3); font-weight:500; padding:7px 9px; border-bottom:1px solid var(--border); white-space:nowrap; }
td { padding:8px 9px; border-bottom:1px solid var(--border); vertical-align:top; }
tr:last-child td { border-bottom:none; }
tbody tr:hover { background:var(--surface-soft); }
.hosttag { display:inline-block; padding:1.5px 9px; border-radius:12px; font-size:11.5px; font-weight:600;
  background:var(--accent-soft); color:var(--accent); white-space:nowrap; }
.hosttag.local { background:var(--surface-soft); color:var(--text-2); }
.hosttag.bad { background:var(--err-soft); color:var(--err); }
.snip { color:var(--text-2); max-width:460px; }
.path { color:var(--text-3); }
.muted { color:var(--text-3); }
.view { display:none; animation:fade .2s ease; }
.view.active { display:block; }
.pill.pending { background:var(--surface-soft); color:var(--text-2); }
.pill .spin { width:10px; height:10px; border-width:1.6px; }
.skel-note { color:var(--text-3); font-size:12.5px; margin:0 0 10px; display:flex; align-items:center; gap:8px; }
.skel { height:88px; border-radius:var(--r-md); border:1px solid var(--border);
  background:linear-gradient(90deg, var(--surface-soft) 25%, var(--surface-2) 50%, var(--surface-soft) 75%);
  background-size:200% 100%; animation:shimmer 1.3s linear infinite; }
.skel + .skel { margin-top:9px; }
@keyframes shimmer { from { background-position:150% 0; } to { background-position:-50% 0; } }
@media (prefers-reduced-motion: reduce) { .skel, .spin { animation:none; } }
@keyframes fade { from { opacity:0; transform:translateY(4px); } to { opacity:1; transform:none; } }

/* search hit list */
.hitlist { display:flex; flex-direction:column; gap:9px; }
.hit { display:grid; grid-template-columns:28px 36px 1fr 18px; gap:12px; align-items:start;
  padding:13px 16px; background:var(--surface); border:1px solid var(--border); border-radius:12px;
  cursor:pointer; transition:background .15s, border-color .15s; }
.hit:hover { background:var(--surface-soft); }
.hit-rank { color:var(--text-3); font-size:12px; padding-top:8px; text-align:center; font-variant-numeric:tabular-nums; }
.hit-ico { width:36px; height:36px; border-radius:10px; display:grid; place-items:center; color:#fff; flex:none;
  background:var(--surface-soft); box-shadow:inset 0 0 0 1px var(--border); }
.hit-ico svg { width:19px; height:19px; }
.hit-ico.claude { background:#d97757; box-shadow:none; }
.hit-ico.claude svg { width:21px; height:21px; }
.hit-ico.codex { background:#0f1115; }
[data-theme="dark"] .hit-ico.codex { background:#000; box-shadow:inset 0 0 0 1px var(--border-strong); }
.hit-ico.pi { background:#fff; box-shadow:inset 0 0 0 1px var(--border-strong); }
[data-theme="dark"] .hit-ico.pi { background:var(--surface-soft); }
.hit-ico.pi svg { width:18px; height:18px; }
.hit-main { min-width:0; }
.hit-meta { display:flex; align-items:center; gap:8px; flex-wrap:wrap; font-size:12px; color:var(--text-2); margin-bottom:4px; }
.hit-snip { color:var(--text-2); font-size:13px; line-height:1.6; display:-webkit-box; -webkit-line-clamp:2;
  -webkit-box-orient:vertical; overflow:hidden; word-break:break-word; }
.hit-path { color:var(--text-3); font-size:11.5px; margin-top:6px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.hit-go { color:var(--text-3); padding-top:8px; transition:color .15s, transform .15s; }
.hit:hover .hit-go { color:var(--accent); transform:translateX(2px); }
.srcbadge { font-size:11px; font-weight:700; padding:1.5px 8px; border-radius:6px;
  background:var(--surface-soft); color:var(--text-2); border:1px solid var(--border); }
mark { background:rgba(250,204,21,.38); color:inherit; border-radius:3px; padding:0 1px; }
[data-theme="dark"] mark { background:rgba(250,204,21,.28); }

/* session transcript */
.sess-bar { position:sticky; top:0; z-index:15; margin:-18px -28px 20px; padding:12px 28px;
  background:var(--bg);
  border-bottom:1px solid var(--border); display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
.sess-bar .st { min-width:0; }
.sess-bar .st .t1 { font-weight:600; font-size:13.5px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:380px; }
.sess-bar .st .t2 { font-size:11.5px; color:var(--text-3); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:460px; }
.sess-bar .mini { min-width:48px; text-align:center; font-size:11.5px; padding:6px 9px; }
#sessMeta { font-size:12px; color:var(--text-2); display:flex; align-items:center; gap:8px; flex-wrap:wrap; min-width:0; }
#sessMeta .muted { overflow:hidden; text-overflow:ellipsis; }
.trans { max-width:748px; margin:0 auto; display:flex; flex-direction:column; gap:16px;
  padding:0 0 60px 84px; position:relative; }
.loadbar { display:flex; justify-content:center; }
.loadbar button { border:none; border-radius:14px; padding:4px 14px; font-size:12px;
  color:var(--text-2); background:var(--surface-soft); cursor:pointer; }
.loadbar button:hover { background:var(--hover); color:var(--accent); }

/* day divider */
.daysep { display:flex; align-items:center; gap:12px; color:var(--text-3); font-size:12px; }
.daysep::before, .daysep::after { content:""; flex:1; height:1px; background:var(--border); }

/* timeline rail: per-row segments overlap across the 16px flex gap */
.rail { position:absolute; left:-18px; top:-16px; bottom:-16px; width:2px;
  background:var(--border); pointer-events:none; }
.rail.start { top:9px; }
.rail.start::before { content:""; position:absolute; top:-5px; left:-4px; width:10px; height:10px;
  border-radius:50%; background:var(--accent); border:2px solid var(--bg); box-sizing:border-box; }
.rail.end { bottom:0; }
.tmark { position:absolute; left:-84px; top:0; width:58px; text-align:right;
  font-size:10.5px; line-height:18px; color:var(--text-3);
  font-variant-numeric:tabular-nums; white-space:nowrap; pointer-events:none; }
.tmark em { font-style:normal; color:var(--accent); margin-left:4px; }
.tgap { position:relative; height:20px; margin:-8px 0; display:flex; align-items:center;
  justify-content:flex-start; }
.tgap .rail { left:-18px; top:0; bottom:0; }
.tgap .glab { position:absolute; left:-17px; top:50%; transform:translate(-50%,-50%);
  font-size:10.5px; color:var(--text-3); background:var(--bg); padding:0 8px;
  font-variant-numeric:tabular-nums; white-space:nowrap; }
.disc .dur { color:var(--accent); font-variant-numeric:tabular-nums; }

/* flow rows (DeepSeekHarness-style transcript) */
.msg { min-width:0; position:relative; animation:fade .2s ease; }
.msg.user { display:flex; flex-direction:column; align-items:flex-end; gap:6px; }
.bubble { max-width:72%; background:#EDF3FE; color:#0F1115; border-radius:22px;
  padding:10px 16px; font-size:14px; line-height:22px; white-space:pre-wrap; word-break:break-word; }
[data-theme="dark"] .bubble { background:#2C2C2E; color:#E6E8EE; }
.msg.user.mhit .bubble { background:#D3E2FF; }
[data-theme="dark"] .msg.user.mhit .bubble { background:#33415e; }
.msg.user .meta { display:flex; gap:7px; align-items:center; justify-content:flex-end;
  font-size:11.5px; color:var(--text-3); padding-right:6px; opacity:0; transition:opacity .15s; }
.msg.user:hover .meta { opacity:1; }

/* assistant narration: borderless full-width markdown */
.msg.assistant { font-size:14px; line-height:24px; color:var(--text); }
.msg.assistant .mbody { white-space:pre-wrap; word-break:break-word; }
.msg.assistant.mhit { background:rgba(65,118,230,.08); border-radius:10px; padding:6px 12px 4px; }
[data-theme="dark"] .msg.assistant.mhit { background:rgba(65,118,230,.18); }
.ameta { display:flex; gap:8px; align-items:center; font-size:11.5px; color:var(--text-3);
  margin-top:3px; opacity:0; transition:opacity .15s; }
.msg.assistant:hover .ameta, .msg.mhit .ameta { opacity:1; }

/* disclosure rows: thinking / tool calls & results / summary marker */
.disc { width:100%; }
.disc > summary { display:flex; align-items:center; gap:6px; min-width:0; height:26px;
  list-style:none; cursor:pointer; color:var(--text-3); font-size:13px; line-height:20px;
  user-select:none; border-radius:6px; padding:0; }
.disc > summary::-webkit-details-marker { display:none; }
.disc > summary:hover { background:var(--surface-soft); }
.disc .chev { flex:none; width:16px; height:16px; color:var(--text-3);
  transform:rotate(-90deg); transition:transform .1s ease; }
.disc[open] > summary .chev { transform:rotate(0deg); }
.disc .dt { flex:none; font-weight:400; color:var(--text-2); }
.disc .ddot { flex:none; width:2px; height:2px; border-radius:1px; background:var(--text-3); }
.disc .dsum { flex:1 1 auto; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.disc .dln { flex:none; font-size:11.5px; }
.disc[open] > summary { border-bottom:1px solid var(--border); border-radius:6px 6px 0 0;
  margin-bottom:8px; height:33px; padding-bottom:8px; }
.disc .dbody { padding:2px 0 4px 22px; font-size:13px; line-height:20px; color:var(--text-3);
  white-space:pre-wrap; word-break:break-word; }
.disc.tool .dbody { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px;
  line-height:1.6; background:var(--surface-soft); border-radius:8px; margin:0 0 4px; padding:8px 12px; }
.disc.summarydisc > summary { height:24px; }
.disc.summarydisc[open] > summary { height:33px; }
.msg.other .mbody { white-space:pre-wrap; word-break:break-word; font-size:13px; color:var(--text-2); }

/* hit marker */
.hittag { flex:none; font-size:10.5px; font-weight:600; padding:0 6px; border-radius:6px;
  background:var(--accent-soft); color:var(--accent); line-height:18px; }

.mbody code { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12.5px;
  background:var(--surface-soft); border:1px solid var(--border); border-radius:4px; padding:0 4px; }
.codeblock { background:var(--surface-soft); border:1px solid var(--border); border-radius:8px;
  padding:10px 13px; margin:6px 0; overflow:auto; font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  font-size:12.5px; line-height:1.6; white-space:pre; }
.mbody.clamp, .dbody.clamp { max-height:300px; overflow:hidden; position:relative; }
.mbody.clamp::after, .dbody.clamp::after { content:""; position:absolute; inset:auto 0 0 0; height:76px;
  background:linear-gradient(transparent, var(--bg)); pointer-events:none; }
.bubble .mbody.clamp::after { background:linear-gradient(transparent, #EDF3FE); }
[data-theme="dark"] .bubble .mbody.clamp::after { background:linear-gradient(transparent, #2C2C2E); }
.disc.tool .dbody.clamp::after { background:linear-gradient(transparent, var(--surface-soft));
  border-radius:0 0 8px 8px; left:0; right:0; }
.mexpand { margin-top:7px; font-size:11.5px; padding:3px 10px; border-radius:14px; border:none;
  color:var(--text-2); background:var(--surface-soft); cursor:pointer; }
.mexpand:hover { color:var(--accent); background:var(--hover); }


/* toasts */
#toasts { position:fixed; top:18px; right:22px; z-index:60; display:flex; flex-direction:column; gap:9px; width:340px; }
.toast { display:flex; gap:10px; align-items:flex-start; background:#353638; color:#fff;
  border-radius:14px; box-shadow:var(--shadow-pop); padding:11px 16px;
  animation:slidein .22s ease; }
[data-theme="dark"] .toast { background:#43454a; }
.toast.ok { box-shadow:var(--shadow-pop), inset 3px 0 0 var(--ok); } .toast.err { box-shadow:var(--shadow-pop), inset 3px 0 0 var(--err); }
.toast .tmsg { font-size:13px; line-height:20px; word-break:break-word; }
.toast .tmsg small { color:rgba(255,255,255,.6); display:block; margin-top:2px; }
@keyframes slidein { from { opacity:0; transform:translateX(16px); } to { opacity:1; transform:none; } }

/* modal */
#modal { position:fixed; inset:0; z-index:50; display:none; place-items:center;
  background:rgba(0,0,0,.24); backdrop-filter:blur(2px); }
#modal.show { display:grid; }
.modal-box { background:var(--surface-2); border:1px solid var(--border); border-radius:24px;
  box-shadow:var(--shadow-pop); width:420px; max-width:calc(100vw - 40px); padding:22px 24px; animation:pop .18s ease; }
@keyframes pop { from { opacity:0; transform:scale(.96); } to { opacity:1; transform:none; } }
.modal-box h3 { margin:0 0 8px; font-size:15px; } .modal-box p { margin:0 0 18px; color:var(--text-2); font-size:13px; word-break:break-word; }
.modal-acts { display:flex; justify-content:flex-end; gap:9px; }

#logbox { background:var(--surface-2); border:1px solid var(--border); border-radius:var(--r-md);
  height:calc(100vh - 230px); min-height:300px; overflow:auto; padding:12px 16px;
  font-family:ui-monospace,Menlo,monospace; font-size:12px; }
.logline { padding:2.5px 0; color:var(--text-2); white-space:pre-wrap; }
.logline .lt { color:var(--text-3); margin-right:8px; }
.logline.ok { color:var(--ok); } .logline.err { color:var(--err); }
.empty { text-align:center; color:var(--text-3); padding:44px 0; font-size:13px; }
.empty svg { width:34px; height:34px; margin-bottom:8px; opacity:.5; }
@media (max-width: 860px) {
  .sidebar { width:54px; } .brand span, .nav a span, .sidebar-foot { display:none; }
  .brand { justify-content:center; padding-left:0; padding-right:0; } .nav a { justify-content:center; }
  .main { margin-left:54px; padding:14px; }
  .sess-bar { margin:-14px -14px 16px; padding:10px 14px; }
  .sess-bar .st .t1, .sess-bar .st .t2 { max-width:52vw; }
  .sess-bar button:disabled { cursor:default; }
  .bubble { max-width:88%; }
  .trans { padding-left:52px; }
  .rail { left:-12px; }
  .tmark { left:-52px; width:34px; }
  .tmark em, .tmark .ss { display:none; }
  .tgap .rail { left:-12px; }
  .tgap .glab { left:-11px; }
}
</style></head>
<body>
<aside class="sidebar">
  <div class="brand"><div class="logo">
    <svg width="28" height="28" viewBox="0 0 64 64" aria-hidden="true"><rect width="64" height="64" rx="15" fill="var(--cta)"/><path d="M17 46 32 35 47 46" fill="none" stroke="#4176e6" stroke-width="2.4" stroke-linecap="round" stroke-dasharray="0.1 5"/><path d="M17 46V19l15 16 15-16v27" fill="none" stroke="var(--cta-text)" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/><g fill="var(--cta-text)"><circle cx="17" cy="46" r="5"/><circle cx="17" cy="19" r="5"/><circle cx="47" cy="19" r="5"/><circle cx="47" cy="46" r="5"/></g><circle cx="32" cy="35" r="6" fill="#4176e6" stroke="var(--cta)" stroke-width="2"/></svg>
  </div><span>mnemo</span></div>
  <nav class="nav" id="nav">
    <a data-view="dashboard" class="active">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/></svg><span>仪表盘</span></a>
    <a data-view="devices">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/></svg><span>设备管理</span></a>
    <a data-view="search">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg><span>会话搜索</span></a>
    <a data-view="logs">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5h16M4 10h16M4 15h10M4 20h7"/></svg><span>日志查看</span></a>
  </nav>
  <div class="sidebar-foot">v0.1.0 · 127.0.0.1 本地服务</div>
</aside>

<div class="main">
  <div class="header">
    <h1 id="pageTitle">仪表盘</h1>
    <span class="spacer"></span>
    <button class="iconbtn" onclick="pingAll(true)">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.55a11 11 0 0 1 14 0"/><path d="M1.42 9a16 16 0 0 1 21.16 0"/><path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><path d="M12 20h.01"/></svg>
      测试连接</button>
    <button class="iconbtn" onclick="refresh(true)">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/></svg>
      刷新</button>
    <button class="iconbtn" onclick="toggleTheme()" title="切换主题">
      <svg id="themeIcon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>
    </button>
  </div>

  <!-- dashboard -->
  <section class="view active" id="view-dashboard">
    <div class="card">
      <div class="banner">
        <span class="dot" id="svcDot"></span>
        <div><div class="k">本地服务</div><div class="v" id="svcState">运行中</div></div>
        <div><div class="k">索引库</div><div class="v mono" id="svcDb" style="font-size:12px">—</div></div>
        <div><div class="k">上次同步</div><div class="v" id="svcSync">—</div></div>
        <span class="spacer" style="flex:1"></span>
        <button onclick="syncLocal()">增量同步</button>
      </div>
    </div>

    <div class="grid g4" id="statRow" style="margin-bottom:18px"></div>

    <div class="card">
      <h2>设备状态<span class="h2sub">每台设备的实时连接与索引</span><span class="spacer"></span>
        <button onclick="go('devices')">管理设备 →</button></h2>
      <div class="grid g3" id="healthGrid"></div>
    </div>
  </section>

  <!-- devices -->
  <section class="view" id="view-devices">
    <div class="card">
      <h2>添加远程设备</h2>
      <div style="display:flex; gap:10px; flex-wrap:wrap; align-items:center">
        <input type="text" id="addName" placeholder="名称，如 devbox-1" style="min-width:170px">
        <input type="text" id="addHost" placeholder="SSH host（默认同名称）" style="min-width:170px">
        <input type="text" id="addBin" placeholder="远端启动器路径（可选）" style="min-width:240px; flex:1">
        <button class="primary" id="addBtn" onclick="addRemote()">rsync 安装并建索引</button>
      </div>
      <p class="muted" style="margin:10px 0 0; font-size:12px">要求：免密 SSH、远端 Python 3.7+ 且 SQLite 支持 FTS5。设备上的会话正文不会被复制到本机。</p>
    </div>
    <div class="card">
      <h2>已注册设备<span class="spacer"></span>
        <button onclick="pingAll(true)">全部测试</button>
        <button onclick="syncAll()">全部同步</button>
        <button onclick="updateAll()">全部更新代码</button></h2>
      <div class="grid g3" id="devGrid"></div>
    </div>
  </section>

  <!-- search -->
  <section class="view" id="view-search">
    <div class="card">
      <h2>会话搜索</h2>
      <div style="display:flex; gap:10px; flex-wrap:wrap; align-items:center">
        <input type="text" id="q" placeholder="搜索历史会话，多个关键词用空格分隔，如：部署 报错" style="min-width:260px; flex:1">
        <input type="number" id="lim" value="20" min="1" max="50" style="width:74px" title="每设备取数">
        <label class="chk"><input type="checkbox" id="allHosts" checked onchange="renderHostPickers()"> 全部设备</label>
        <span id="hostPickers" style="display:flex;gap:12px"></span>
        <button class="primary" id="searchBtn" onclick="runSearch()">查询</button>
      </div>
      <div id="chips" style="margin-top:14px"></div>
    </div>
    <div class="card">
      <h2>搜索结果 <span class="spacer"></span><span id="hitCount" style="font-weight:400;font-size:12px"></span></h2>
      <div id="searchOut"><div class="empty">输入查询词并查询；结果支持点击，可直接打开该条会话的完整全文。</div></div>
    </div>
  </section>

  <!-- session transcript -->
  <section class="view" id="view-session">
    <div class="sess-bar">
      <button class="iconbtn" onclick="closeSession()" title="返回搜索结果">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg>
        返回</button>
      <div class="st">
        <div class="t1 mono" id="sessTitle">—</div>
        <div class="t2 mono" id="sessSub"></div>
      </div>
      <span style="flex:1"></span>
      <span id="sessMeta"></span>
      <button class="mini" id="sessPrev" onclick="gotoMatch(-1)" title="上一处匹配">↑</button>
      <button class="mini" id="sessNext" onclick="gotoMatch(1)" title="下一处匹配">↓</button>
      <button id="sessRaw" onclick="toggleRaw()">读取全文</button>
      <button onclick="copySessionPath()">复制路径</button>
    </div>
    <div id="sessBody"><div class="empty">从搜索结果点击一条记录打开会话。</div></div>
  </section>

  <!-- logs -->
  <section class="view" id="view-logs">
    <div class="card">
      <h2>操作日志<span class="spacer"></span><button onclick="clearLogs()">清空</button></h2>
      <div id="logbox"></div>
    </div>
  </section>
</div>

<div id="toasts"></div>
<div id="modal"><div class="modal-box">
  <h3 id="modalTitle"></h3><p id="modalBody"></p>
  <div class="modal-acts"><button onclick="closeModal()">取消</button><button class="danger" id="modalOk">确认</button></div>
</div></div>

<script>
const TOKEN = document.querySelector('meta[name=token]').content;
let STATUS = null;
const PING = {};   // name -> {ok, ms|error}
const RSTAT = {};  // name -> remote status payload

/* ---------- theme & nav ---------- */
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem('mnemo-theme', t); } catch (e) {}
}
function toggleTheme() { applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'); }
try { const t = localStorage.getItem('mnemo-theme'); if (t) applyTheme(t); } catch (e) {}
const TITLES = { dashboard:'仪表盘', devices:'设备管理', search:'会话搜索', logs:'日志查看', session:'会话全文' };
function go(view) {
  document.querySelectorAll('.nav a').forEach(a => a.classList.toggle('active', a.dataset.view === view));
  document.querySelectorAll('.view').forEach(v => v.classList.toggle('active', v.id === 'view-' + view));
  document.getElementById('pageTitle').textContent = TITLES[view] || '';
  history.replaceState(null, '', '#' + view);
}
document.querySelectorAll('.nav a').forEach(a => a.onclick = () => go(a.dataset.view));

/* ---------- primitives ---------- */
function esc(s) { return String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function fmtTs(ts) { return ts ? new Date(ts * 1000).toLocaleString() : '从未同步'; }
function totals(sources) { let f=0,m=0; for (const k in sources){ f+=sources[k].files; m+=sources[k].messages; }
  return { files:f, msgs:m, text:f+' sessions · '+m.toLocaleString()+' messages' }; }
function busy(btn, html) { if (!btn) return; btn.disabled = true; btn._html = btn.innerHTML; btn.innerHTML = html || '<span class="spin"></span> 执行中'; }
function idle(btn) { if (!btn) return; btn.disabled = false; btn.innerHTML = btn._html || btn.innerHTML; }
async function api(path, body) {
  const opt = body === undefined
    ? { headers:{'X-Dashboard-Token':TOKEN} }
    : { method:'POST', headers:{'X-Dashboard-Token':TOKEN,'Content-Type':'application/json'}, body:JSON.stringify(body) };
  const r = await fetch(path, opt); return r.json();
}

/* ---------- toasts & modal & logs ---------- */
function toast(msg, type, sub) {
  const el = document.createElement('div');
  el.className = 'toast ' + (type || '');
  el.innerHTML = '<div class="tmsg">' + esc(msg) + (sub ? '<small>' + esc(sub) + '</small>' : '') + '</div>';
  document.getElementById('toasts').appendChild(el);
  setTimeout(() => { el.style.transition = 'opacity .3s'; el.style.opacity = '0'; setTimeout(() => el.remove(), 300); }, 4200);
  addLog(msg + (sub ? '  ' + sub : ''), type);
}
function addLog(msg, cls) {
  const box = document.getElementById('logbox');
  const line = document.createElement('div');
  line.className = 'logline ' + (cls === 'err' ? 'err' : cls === 'ok' ? 'ok' : '');
  line.innerHTML = '<span class="lt">' + new Date().toLocaleTimeString() + '</span>' + esc(msg);
  box.appendChild(line); box.scrollTop = box.scrollHeight;
}
function clearLogs() { document.getElementById('logbox').innerHTML = ''; }
function confirmDlg(title, body, onOk, danger) {
  document.getElementById('modalTitle').textContent = title;
  document.getElementById('modalBody').textContent = body;
  const ok = document.getElementById('modalOk');
  ok.className = danger === false ? 'primary' : 'danger';
  ok.textContent = '确认';
  ok.onclick = () => { closeModal(); onOk(); };
  document.getElementById('modal').classList.add('show');
}
function closeModal() { document.getElementById('modal').classList.remove('show'); }
document.getElementById('modal').addEventListener('click', e => { if (e.target.id === 'modal') closeModal(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

/* ---------- status ---------- */
async function refresh(silent) {
  STATUS = await api('/api/status');
  if (STATUS.error) { if (!silent) toast(STATUS.error, 'err'); return; }
  document.getElementById('svcDb').textContent = STATUS.db;
  document.getElementById('svcSync').textContent = fmtTs(STATUS.last_sync);
  const t = totals(STATUS.sources);
  const online = STATUS.remotes.filter(r => PING[r.name] && PING[r.name].ok).length;
  document.getElementById('statRow').innerHTML = [
    [iconDb(), '索引会话', t.files, '本机 ' + Object.keys(STATUS.sources).length + ' 个 agent'],
    [iconMsg(), '索引消息', t.msgs.toLocaleString(), '跨三源统一索引'],
    [iconDev(), '远程设备', STATUS.remotes.length, online + ' 台在线 / 共 ' + STATUS.remotes.length + ' 台'],
    [iconClock(), '上次同步', STATUS.last_sync ? new Date(STATUS.last_sync*1000).toLocaleTimeString() : '—',
       STATUS.last_sync ? new Date(STATUS.last_sync*1000).toLocaleDateString() : '请先同步'],
  ].map(([ic,label,num,sub]) =>
    '<div class="stat"><div class="top"><span class="ico">' + ic + '</span>' + label + '</div>' +
    '<div class="num">' + num + '</div><div class="sub">' + esc(sub) + '</div></div>').join('');
  renderHealth(); renderDevices(); renderHostPickers();
}
function healthIcon(state) {
  if (state === 'ok') return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>';
  if (state === 'bad') return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>';
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 8h.01M12 12v4"/></svg>';
}
function srcLine(sources) {
  return Object.entries(sources).map(([k,v]) =>
    '<span class="pill idle" style="margin:1px 4px 1px 0">' + esc(k) + ' ' + v.files + '/' + v.messages.toLocaleString() + '</span>').join('');
}
function fmtAgo(ts) {
  if (!ts) return '从未同步';
  const d = Math.max(0, Date.now() / 1000 - ts);
  if (d < 60) return '刚刚同步';
  if (d < 3600) return Math.floor(d / 60) + 'm前同步';
  if (d < 86400) return Math.floor(d / 3600) + 'h前同步';
  return Math.floor(d / 86400) + 'd前同步';
}
function hueOf(name) { let h = 0; for (const c of name) h = (h * 31 + c.charCodeAt(0)) % 360; return h; }
function iconLaptop() { return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="5" width="16" height="11" rx="1.5"/><path d="M2 19h20"/></svg>'; }
function iconServer() { return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/></svg>'; }
function iconSync() { return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 0 1-15.5 6.2L3 16M3 12a9 9 0 0 1 15.5-6.2L21 8"/><path d="M21 3v5h-5M3 21v-5h5"/></svg>'; }
function srcSummary(sources) {
  return Object.entries(sources || {}).map(([k, v]) => esc(k) + ' ' + v.files).join(' · ');
}
const STATE_LABEL = { ok:'就绪', bad:'离线', checking:'检测中', unknown:'未检测' };
function statusCard(o) {
  // o: { name, tag, icon, state, desc, srcs, ts, action }
  return '<div class="dstat" style="--h:' + hueOf(o.name) + '">' +
    '<div class="top"><div class="av">' + o.icon + '<span class="dot ' + o.state + '"></span></div>' +
    '<div class="who"><span class="nm" title="' + esc(o.name) + '">' + esc(o.name) + '</span>' +
    '<span class="tag" title="' + esc(o.tag) + '">' + esc(o.tag) + '</span></div></div>' +
    '<div class="desc">' + o.desc + (o.srcs ? '<span class="srcs">' + o.srcs + '</span>' : '') + '</div>' +
    '<div class="foot"><span class="badge ' + o.state + '">' + STATE_LABEL[o.state] + '</span>' +
    '<span class="ago">' + (o.ts === undefined ? '' : fmtAgo(o.ts)) + '</span>' +
    '<button class="act" title="增量同步" onclick="' + o.action + '">' + iconSync() + '</button></div></div>';
}
function renderHealth() {
  const t = totals(STATUS.sources);
  const cards = [statusCard({
    name: 'local', tag: '本机', icon: iconLaptop(), state: 'ok',
    desc: '运行中，已索引 ' + t.files + ' 个会话、' + t.msgs.toLocaleString() + ' 条消息',
    srcs: srcSummary(STATUS.sources), ts: STATUS.last_sync, action: 'syncLocal()',
  })];
  cards.push(...STATUS.remotes.map(r => {
    const p = PING[r.name], s = RSTAT[r.name];
    const state = CHECKING.has(r.name) ? 'checking' : !p ? 'unknown' : p.ok ? 'ok' : 'bad';
    let desc;
    if (state === 'checking') desc = '正在连接…';
    else if (state === 'bad') {
      const e = String(p.error || '未知错误').split('\n')[0];
      desc = '<span class="err" title="' + esc(e) + '">无法连接：' + esc(e) + '</span>';
    }
    else if (state === 'unknown') desc = '尚未检测连接';
    else desc = '在线，延迟 ' + p.ms + ' ms' + (s ? '，已索引 ' + totals(s.sources).files + ' 个会话' : '');
    return statusCard({
      name: r.name, tag: r.host, icon: iconServer(), state, desc,
      srcs: s && state === 'ok' ? srcSummary(s.sources) : '',
      ts: s ? s.last_sync : undefined, action: "syncOne('" + esc(r.name) + "')",
    });
  }));
  const grid = document.getElementById('healthGrid');
  grid.innerHTML = cards.join('');
  if (!STATUS.remotes.length) grid.innerHTML +=
    '<div class="empty" style="grid-column:1/-1">还没有远程设备，到「设备管理」添加。</div>';
}
// Probe every remote once on load so the cards show live state instead of "未检测".
const CHECKING = new Set();
async function probeRemotes() {
  if (!STATUS || !STATUS.remotes) return;
  await Promise.all(STATUS.remotes.map(async r => {
    CHECKING.add(r.name); renderHealth();
    const x = await api('/api/remote-status?name=' + encodeURIComponent(r.name));
    CHECKING.delete(r.name);
    if (x.ok) { RSTAT[r.name] = x.status; PING[r.name] = { name:r.name, ok:true, ms:x.status.ms }; }
    else PING[r.name] = { name:r.name, ok:false, error:x.error };
    renderHealth(); renderDevices();
  }));
  refresh(true);   // stat row's online count depends on the probe results
}

/* ---------- devices page ---------- */
function renderDevices() {
  if (!STATUS) return;
  const box = document.getElementById('devGrid');
  if (!STATUS.remotes.length) { box.innerHTML = '<div class="empty" style="grid-column:1/-1">尚未注册设备</div>'; return; }
  box.innerHTML = STATUS.remotes.map(r => {
    const p = PING[r.name]; const s = RSTAT[r.name];
    const state = !p ? 'unknown' : p.ok ? 'ok' : 'bad';
    return '<div class="dev"><div class="head"><span class="name">' + esc(r.name) + '</span>' +
      '<span class="health ' + state + '">' + healthIcon(state) + '</span></div>' +
      '<div class="meta"><div>host: <b>' + esc(r.host) + '</b>' +
      (p ? (p.ok ? ' · ' + p.ms + ' ms' : ' · <b style="color:var(--err)">不可达</b>') : '') + '</div>' +
      '<div>bin: <b>' + esc(r.bin) + '</b></div>' +
      (s ? '<div>' + srcLine(s.sources) + '</div><div>last sync: <b>' + fmtTs(s.last_sync) + '</b> (' + s.ms + ' ms)</div>' : '') +
      '</div><div class="acts">' +
      '<button onclick="pingOne(\'' + esc(r.name) + '\')">测试</button>' +
      '<button onclick="remoteStatus(\'' + esc(r.name) + '\')">索引状态</button>' +
      '<button onclick="syncOne(\'' + esc(r.name) + '\')">同步</button>' +
      '<button onclick="updateOne(\'' + esc(r.name) + '\')">更新代码</button>' +
      '<button class="danger" onclick="removeOne(\'' + esc(r.name) + '\')">移除</button>' +
      '</div></div>';
  }).join('');
}
async function addRemote() {
  const name = document.getElementById('addName').value.trim();
  const host = document.getElementById('addHost').value.trim() || name;
  const bin = document.getElementById('addBin').value.trim();
  if (!name) { toast('需要填写设备名称', 'err'); return; }
  const btn = document.getElementById('addBtn'); busy(btn, '<span class="spin"></span> 安装中');
  toast('正在连接 ' + host + ' 并安装，通常需要几十秒…');
  const r = await api('/api/remotes/add', { name, host, bin });
  idle(btn);
  if (r.ok) {
    toast('已添加设备 ' + name, 'ok', (r.logs || []).join(' / '));
    document.getElementById('addName').value = ''; document.getElementById('addHost').value = ''; document.getElementById('addBin').value = '';
    await refresh(true); pingOne(name);
  } else toast('添加失败：' + r.error, 'err');
}
function removeOne(name) {
  confirmDlg('移除设备 ' + name, '只删除本机的连接配置，不会删除设备上的任何文件。', async () => {
    const r = await api('/api/remotes/remove', { name });
    if (r.ok) { toast('已移除 ' + name, 'ok'); delete PING[name]; delete RSTAT[name]; refresh(true); }
    else toast(r.error, 'err');
  });
}
function updateOne(name) {
  confirmDlg('更新 ' + name + ' 的代码', '将重新 rsync 代码并在该设备上增量建索引。', () => doUpdate({ name }, name), false);
}
function updateAll() {
  if (!STATUS.remotes.length) { toast('没有已注册设备', 'err'); return; }
  confirmDlg('更新全部设备', '将向 ' + STATUS.remotes.length + ' 台设备重新 rsync 代码并增量建索引。', () => doUpdate({}, '全部设备'), false);
}
async function doUpdate(body, label) {
  toast('开始更新 ' + label + '…');
  const r = await api('/api/remotes/update', body);
  if (r.ok) toast('更新完成：' + label, 'ok', (r.logs || []).join(' / '));
  else toast('更新失败：' + r.error, 'err');
}

/* ---------- actions ---------- */
async function pingAll(silent) {
  const r = await api('/api/ping', {});
  for (const x of r.results) {
    PING[x.name] = x;
    if (x.ok) { if (!silent) toast(x.name + ' 连接正常', 'ok', x.ms + ' ms'); }
    else if (!silent) toast(x.name + ' 不可达', 'err', x.error);
  }
  addLog('连接测试：' + r.results.map(x => x.name + (x.ok ? ' ✓' : ' ✗')).join('，'),
         r.results.every(x => x.ok) ? 'ok' : 'err');
  renderHealth(); renderDevices();
}
async function pingOne(name) {
  const r = (await api('/api/ping', { name })).results[0];
  PING[name] = r;
  if (r.ok) toast(name + ' 连接正常', 'ok', r.ms + ' ms'); else toast(name + ' 不可达', 'err', r.error);
  renderHealth(); renderDevices();
}
async function remoteStatus(name) {
  const r = await api('/api/remote-status?name=' + encodeURIComponent(name));
  if (r.ok) { RSTAT[name] = r.status; toast(name + ' 索引状态', 'ok', totals(r.status.sources).text); }
  else { toast(name + ' 状态获取失败', 'err', r.error); PING[name] = { ok:false, error:r.error }; }
  renderHealth(); renderDevices();
}
async function syncLocal() {
  toast('本机增量同步中…');
  const t0 = Date.now();
  const r = await api('/api/sync', {});
  if (r.ok) { toast('本机同步完成', 'ok', '+' + r.stats.files_new + ' 新 / ' + r.stats.messages + ' 消息 / ' + r.ms + ' ms'); refresh(true); }
  else toast(r.error, 'err');
}
async function syncOne(name) {
  toast(name + ' 增量同步中…');
  const r = await api('/api/sync', { name });
  if (r.ok) { toast(name + ' 同步完成', 'ok', r.output + ' / ' + r.ms + ' ms'); remoteStatus(name); }
  else toast(name + ' 同步失败', 'err', r.error);
}
function syncAll() {
  if (!STATUS.remotes.length) { syncLocal(); return; }
  syncLocal(); STATUS.remotes.forEach(r => syncOne(r.name));
}

/* ---------- search diagnose ---------- */
function renderHostPickers() {
  if (!STATUS) return;
  const all = document.getElementById('allHosts').checked;
  const names = ['local'].concat(STATUS.remotes.map(r => r.name));
  document.getElementById('hostPickers').innerHTML = all ? '' :
    names.map(n => '<label class="chk"><input type="checkbox" class="hpick" value="' + esc(n) + '" checked> ' + esc(n) + '</label>').join('');
}
function pickedHosts() {
  if (document.getElementById('allHosts').checked) return null;
  return [...document.querySelectorAll('.hpick:checked')].map(e => e.value);
}
const ROLE_LABEL = { user:'用户', assistant:'助手', tool:'工具', system:'系统' };
const KIND_LABEL = { text:'对话', summary:'摘要', tool_call:'工具调用', tool_result:'工具结果', reasoning:'思考' };
let LAST_TERMS = [];
let LAST_HITS = [];

function searchTargets() {
  const picked = pickedHosts();
  return picked !== null ? picked : ['local'].concat(((STATUS && STATUS.remotes) || []).map(r => r.name));
}
function fmtSecs(ms) { return (ms / 1000).toFixed(1) + 's'; }
async function runSearch() {
  const query = document.getElementById('q').value.trim();
  if (!query) { document.getElementById('q').focus(); return; }
  const btn = document.getElementById('searchBtn');
  if (btn.disabled) return;   // one search at a time; Enter repeats are ignored
  const limit = parseInt(document.getElementById('lim').value, 10) || 20;
  const targets = searchTargets();
  const out = document.getElementById('searchOut'), count = document.getElementById('hitCount');

  // pending state: per-device chips, skeleton results, live elapsed time
  busy(btn, '<span class="spin"></span> 搜索中');
  out.setAttribute('aria-busy', 'true');
  document.getElementById('chips').innerHTML = targets.map(h =>
    '<span class="pill pending" style="margin:2px 6px 2px 0"><span class="spin"></span>' + esc(h) + ' · 搜索中</span>').join('');
  out.innerHTML = '<div class="skel-note"><span class="spin"></span>正在同步索引并搜索 ' +
    (targets.length > 1 ? targets.length + ' 台设备' : esc(targets[0] || 'local')) + '…</div>' +
    '<div class="skel"></div><div class="skel"></div><div class="skel"></div>';
  const t0 = Date.now();
  count.textContent = '已用 0.0s';
  const tick = setInterval(() => { count.textContent = '已用 ' + fmtSecs(Date.now() - t0); }, 100);

  let r;
  try { r = await api('/api/search', { query, limit, hosts: pickedHosts() }); }
  catch (e) { r = { error: String(e && e.message || e) }; }
  finally { clearInterval(tick); idle(btn); out.removeAttribute('aria-busy'); }
  const took = fmtSecs(Date.now() - t0);

  if (r.error) {
    toast('搜索失败', 'err', r.error);
    document.getElementById('chips').innerHTML = '';
    count.textContent = '';
    out.innerHTML = '<div class="empty">搜索失败：' + esc(r.error) + '</div>';
    return;
  }
  document.getElementById('chips').innerHTML = r.per_host.map(h =>
    '<span class="pill ' + (h.ok ? 'ok' : 'bad') + '" style="margin:2px 6px 2px 0">' +
    esc(h.host) + ' · ' + (h.ok ? h.ms + ' ms · ' + h.hits + ' 命中' : '不可达') + '</span>').join('') +
    (r.warnings.length ? '<div class="muted" style="margin-top:6px; font-size:12px">' + r.warnings.map(esc).join('<br>') + '</div>' : '');
  LAST_TERMS = query.split(/\s+/).filter(Boolean);
  LAST_HITS = r.merged;
  count.textContent = r.merged.length ? r.merged.length + ' 条 · 用时 ' + took + ' · 点击查看会话全文' : '用时 ' + took;
  if (!r.merged.length) { out.innerHTML = '<div class="empty">没有匹配结果。</div>'; return; }
  document.getElementById('searchOut').innerHTML =
    '<div class="hitlist">' + r.merged.map((h, i) => {
      const snip = esc((h.snippet || '').replace(/\s+/g, ' '))
        .replace(/\[\[([\s\S]*?)\]\]/g, '<mark>$1</mark>');
      return '<div class="hit" data-i="' + i + '" role="button" tabindex="0">' +
        '<div class="hit-rank">' + (i + 1) + '</div>' +
        '<div class="hit-ico ' + esc(h.source) + '">' + srcIcon(h.source) + '</div>' +
        '<div class="hit-main"><div class="hit-meta">' +
          '<span class="hosttag ' + (h.host === 'local' ? 'local' : '') + '">' + esc(h.host) + '</span>' +
          '<span class="srcbadge">' + esc(h.source) + '</span>' +
          '<span>' + esc(ROLE_LABEL[h.role] || h.role || '') + ' · ' + esc(KIND_LABEL[h.kind] || h.kind || '') + '</span>' +
          '<span class="muted">' + esc((h.ts || '').slice(0, 16).replace('T', ' ')) + '</span>' +
        '</div>' +
        '<div class="hit-snip">' + snip + '</div>' +
        '<div class="hit-path mono">' + esc(h.path.split('/').pop()) + ' : ' + h.lineno + '</div></div>' +
        '<div class="hit-go">' + iconChevron() + '</div></div>';
    }).join('') + '</div>';
  addLog('搜索「' + query + '」：' + r.per_host.map(h => h.host + ' ' + (h.ok ? h.ms + 'ms' : '✗')).join('，'));
}
document.getElementById('q').addEventListener('keydown', e => { if (e.key === 'Enter') runSearch(); });
document.getElementById('searchOut').addEventListener('click', e => {
  const el = e.target.closest('.hit');
  if (el && LAST_HITS[+el.dataset.i]) {
    const h = LAST_HITS[+el.dataset.i];
    openSession(h.path, h.host, h.lineno, LAST_TERMS);
  }
});
document.getElementById('searchOut').addEventListener('keydown', e => {
  if (e.key !== 'Enter' && e.key !== ' ') return;
  const el = e.target.closest('.hit');
  if (el && LAST_HITS[+el.dataset.i]) {
    e.preventDefault();
    const h = LAST_HITS[+el.dataset.i];
    openSession(h.path, h.host, h.lineno, LAST_TERMS);
  }
});

/* ---------- session transcript ---------- */
let SESS = null;
const EXPANDED = new Set();
const DISC_OPEN = new Set();

function discToggle(sum) {
  setTimeout(() => {
    const d = sum.closest('details.disc');
    const i = +d.closest('.msg').dataset.i;
    if (d.open) DISC_OPEN.add(i); else DISC_OPEN.delete(i);
  }, 0);
}

function showSession() {
  document.querySelectorAll('.nav a').forEach(a => a.classList.remove('active'));
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.getElementById('view-session').classList.add('active');
  document.getElementById('pageTitle').textContent = TITLES.session;
}
function closeSession() {
  SESS = null;
  history.pushState(null, '', '#search');
  go('search');
}
window.addEventListener('popstate', () => {
  if (location.hash === '#search') { SESS = null; go('search'); }
  else if (location.hash.startsWith('#session')) openSessionFromHash(true);
});
function openSessionFromHash(replace) {
  const p = new URLSearchParams(location.hash.slice(9));
  const path = p.get('path');
  if (!path) { go('search'); return; }
  const terms = p.get('q') ? p.get('q').split(/\s+/).filter(Boolean) : [];
  openSession(path, p.get('host') || 'local', parseInt(p.get('line'), 10) || 0, terms, replace);
}
async function openSession(path, host, lineno, terms, replace) {
  host = host || 'local';
  terms = terms || [];
  showSession();
  EXPANDED.clear();
  DISC_OPEN.clear();
  SESS = null;
  document.getElementById('sessTitle').textContent = path.split('/').pop();
  document.getElementById('sessSub').textContent = host + ' · ' + path;
  document.getElementById('sessMeta').textContent = '加载中…';
  document.getElementById('sessBody').innerHTML =
    '<div class="empty"><span class="spin" style="display:inline-block;width:18px;height:18px;border-width:2.5px"></span><br>正在读取会话…</div>';
  const q = new URLSearchParams({ path, host, line: String(lineno || '') });
  if (terms.length) q.set('q', terms.join(' '));
  if (replace) history.replaceState(null, '', '#session?' + q.toString());
  else history.pushState(null, '', '#session?' + q.toString());
  const r = await api('/api/session', { path, host, raw: false });
  if (!r.ok) {
    document.getElementById('sessBody').innerHTML = '<div class="empty">加载失败：' + esc(r.error) + '</div>';
    toast('会话加载失败：' + r.error, 'err');
    return;
  }
  initSession(r.session, host, lineno, terms, false);
}
function initSession(data, host, anchor, terms, raw) {
  EXPANDED.clear();
  DISC_OPEN.clear();
  SESS = { data, host, anchor, terms, raw, lo: 0, hi: data.count, matched: [], cur: -1 };
  const idx = Math.max(0, data.messages.findIndex(m => m.lineno === anchor));
  if (data.count > 240) {
    SESS.lo = Math.max(0, idx - 90);
    SESS.hi = Math.min(data.count, idx + 91);
  }
  const tt = terms.map(t => t.toLowerCase());
  if (tt.length) data.messages.forEach((m, i) => {
    const txt = (m.text || '').toLowerCase();
    if (tt.every(t => txt.includes(t))) SESS.matched.push(i);
  });
  SESS.cur = SESS.matched.find(i => i >= idx);
  if (SESS.cur === undefined) SESS.cur = SESS.matched[0] === undefined ? -1 : SESS.matched[0];
  renderSession('anchor');
}
function escRe(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }
function renderBody(text, terms) {
  let h = esc(text == null ? '' : text);
  (terms || []).forEach(t => {
    if (!t) return;
    h = h.replace(new RegExp('(' + escRe(esc(t)) + ')', 'gi'), '<mark>$1</mark>');
  });
  h = h.replace(/```[^\n]*\n?([\s\S]*?)(?:```|$)/g,
    (m, code) => '<pre class="codeblock">' + code.replace(/\n$/, '') + '</pre>');
  h = h.replace(/(^|[\s(\[])`([^`\n]{1,200})`(?=$|[\s).,:;!?\]])/g, '$1<code>$2</code>');
  return h;
}
function fmtMsgTs(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  return isNaN(d) ? String(ts).slice(11, 19) : d.toLocaleTimeString();
}
function chevIcon() {
  return '<svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>';
}
function oneLine(text, n) {
  const line = String(text || '').split('\n').map(s => s.trim()).find(Boolean) || '';
  return line.length > n ? line.slice(0, n) + '…' : line;
}
function metaHtml(m, hit) {
  return '<span class="meta">' + fmtMsgTs(m.ts) + ' · L' + m.lineno +
    (hit ? '<span class="hittag">命中</span>' : '') + '</span>';
}
function fmtClock(ts) {
  const d = new Date(ts);
  if (isNaN(d)) return esc(String(ts || '').slice(11, 19));
  const p = n => String(n).padStart(2, '0');
  return p(d.getHours()) + ':' + p(d.getMinutes()) + '<span class="ss">:' + p(d.getSeconds()) + '</span>';
}
function fmtDur(sec) {
  if (sec == null || !isFinite(sec) || sec < 0) return '';
  if (sec < 1) return Math.round(sec * 1000) + 'ms';
  if (sec < 60) return (sec < 10 ? sec.toFixed(1) : String(Math.round(sec))) + 's';
  const m = Math.floor(sec / 60), ss = Math.floor(sec % 60);
  if (m < 60) return m + '分' + (ss ? ss + '秒' : '');
  const h = Math.floor(m / 60);
  return h + '小时' + (m % 60 ? (m % 60) + '分' : '');
}
function fmtGap(sec) {
  if (sec < 3600) return Math.round(sec / 60) + '分钟';
  if (sec < 86400) {
    const h = Math.floor(sec / 3600), mm = Math.floor(sec / 60) % 60;
    return h + '小时' + (mm ? mm + '分' : '');
  }
  return Math.floor(sec / 86400) + '天';
}
function railHtml(ctx, m) {
  return '<span class="rail' + (ctx.turnStart ? ' start' : '') + (ctx.turnEnd ? ' end' : '') + '"></span>' +
    (ctx.turnStart
      ? '<span class="tmark" title="' + esc(m.ts || '') + '">' + fmtClock(m.ts) +
        (ctx.turnNo ? '<em>#' + ctx.turnNo + '</em>' : '') + '</span>'
      : '');
}
function renderMsg(m, i, hit, terms, ctx) {
  ctx = ctx || {};
  const rail = railHtml(ctx, m);
  const text = m.text || '';
  const long = text.length > 1200 || text.split('\n').length > 24;
  const clamp = long && !EXPANDED.has(i);
  const expandBtn = clamp
    ? '<button class="mexpand" onclick="expandMsg(' + i + ')">展开全部 ↓</button>' : '';
  if (m.kind === 'text' && m.role === 'user') {
    return '<div class="msg user' + (hit ? ' mhit' : '') + '" data-i="' + i + '">' + rail +
      '<div class="bubble"><div class="mbody' + (clamp ? ' clamp' : '') + '">' +
      renderBody(text, terms) + '</div></div>' + expandBtn + metaHtml(m, hit) + '</div>';
  }
  if ((m.kind === 'text' || m.kind == null) && (m.role === 'assistant' || m.role == null)) {
    return '<div class="msg assistant' + (hit ? ' mhit' : '') + '" data-i="' + i + '">' + rail +
      '<div class="mbody' + (clamp ? ' clamp' : '') + '">' + renderBody(text, terms) + '</div>' +
      expandBtn +
      '<div class="ameta">' + fmtMsgTs(m.ts) + ' · L' + m.lineno +
      (hit ? '<span class="hittag">命中</span>' : '') + '</div></div>';
  }
  let discClass = 'disc', title = KIND_LABEL[m.kind] || m.kind || '内容';
  if (m.kind === 'reasoning') discClass += ' think';
  else if (m.kind === 'tool_call' || m.kind === 'tool_result') discClass += ' tool';
  else if (m.kind === 'summary') discClass += ' summarydisc';
  else return '<div class="msg other' + (hit ? ' mhit' : '') + '" data-i="' + i + '">' + rail +
      '<div class="ameta">' + esc(ROLE_LABEL[m.role] || m.role || '') + ' · ' +
      esc(KIND_LABEL[m.kind] || m.kind || '') + ' · ' + fmtMsgTs(m.ts) + ' · L' + m.lineno +
      (hit ? '<span class="hittag">命中</span>' : '') + '</div>' +
      '<div class="mbody">' + renderBody(text, terms) + '</div></div>';
  const durTag = ctx.dur != null && ctx.dur >= 0.05 ? '<span class="dln dur">' + fmtDur(ctx.dur) + '</span>' : '';
  return '<div class="msg' + (hit ? ' mhit' : '') + '" data-i="' + i + '">' + rail +
    '<details class="' + discClass + '"' + (hit || DISC_OPEN.has(i) ? ' open' : '') + '>' +
    '<summary onclick="discToggle(this)">' + chevIcon() + '<span class="dt">' + esc(title) + '</span>' +
    '<span class="ddot"></span><span class="dsum">' + esc(oneLine(text, 140)) + '</span>' +
    durTag +
    (hit ? '<span class="hittag">命中 L' + m.lineno + '</span>' : '<span class="dln muted">L' + m.lineno + '</span>') +
    '</summary>' +
    '<div class="dbody' + (clamp ? ' clamp' : '') + '">' + renderBody(text, terms) + '</div>' +
    expandBtn + '</details></div>';
}
function renderSession(mode) {
  if (!SESS) return;
  const s = SESS.data;
  const prevH = document.body.scrollHeight;
  const rawBtn = document.getElementById('sessRaw');
  rawBtn.textContent = SESS.raw ? '返回索引版' : '读取全文';
  rawBtn.classList.toggle('primary', !SESS.raw);
  const elapsed = s.started_at && s.ended_at
    ? (Date.parse(s.ended_at) - Date.parse(s.started_at)) / 1000 : null;
  document.getElementById('sessMeta').innerHTML =
    '<span class="hosttag ' + (SESS.host === 'local' ? 'local' : '') + '">' + esc(SESS.host) + '</span>' +
    '<span class="srcbadge">' + esc(s.source || '') + '</span>' +
    '<span>' + s.count + ' 条消息 · ' +
    esc((s.started_at || '').slice(0, 16).replace('T', ' ')) + ' → ' + esc((s.ended_at || '').slice(11, 19)) +
    (isFinite(elapsed) && elapsed >= 60 ? ' · 共 ' + fmtDur(elapsed) : '') + '</span>' +
    (SESS.raw ? '<span class="pill warn">原始全文</span>' : '<span class="pill idle">索引版 · 单条≤20k</span>') +
    (s.cwd ? '<span class="muted mono">' + esc(s.cwd) + '</span>' : '');
  const isUserTurn0 = mm => mm.kind === 'text' && mm.role === 'user';
  const turnOf = new Array(s.count);
  let tn = 0;
  s.messages.forEach((mm, ii) => {
    if (ii === 0 || isUserTurn0(mm)) tn++;
    turnOf[ii] = tn;
  });
  const rows = [];
  if (SESS.lo > 0)
    rows.push('<div class="loadbar"><button onclick="expandOlder()">↑ 加载更早 ' + Math.min(120, SESS.lo) + ' 条</button></div>');
  s.messages.slice(SESS.lo, SESS.hi).forEach((m, off) => {
    const i = SESS.lo + off;
    const day = (m.ts || '').slice(0, 10);
    const prev = i > 0 ? s.messages[i - 1] : null;
    const next = i < s.count - 1 ? s.messages[i + 1] : null;
    const prevDay = prev ? (prev.ts || '').slice(0, 10) : '';
    const dayChg = !!(day && day !== prevDay);
    if (dayChg) { rows.push('<div class="daysep">' + esc(day) + '</div>'); }
    if (prev && !dayChg && i > SESS.lo) {
      const gap = (Date.parse(m.ts) - Date.parse(prev.ts)) / 1000;
      if (isFinite(gap) && gap >= 90)
        rows.push('<div class="tgap"><span class="rail"></span><span class="glab">空闲 ' + fmtGap(gap) + '</span></div>');
    }
    let dur = null;
    if (m.kind === 'tool_call' && next && next.kind === 'tool_result') {
      const d = (Date.parse(next.ts) - Date.parse(m.ts)) / 1000;
      if (isFinite(d) && d >= 0) dur = d;
    }
    const ctx = {
      turnStart: i === 0 || isUserTurn0(m) || dayChg,
      turnEnd: !next || isUserTurn0(next) ||
        (m.ts || '').slice(0, 10) !== (next.ts || '').slice(0, 10),
      turnNo: isUserTurn0(m) ? turnOf[i] : null,
      dur
    };
    rows.push(renderMsg(m, i, m.lineno === SESS.anchor, SESS.terms, ctx));
  });
  if (SESS.hi < s.count)
    rows.push('<div class="loadbar"><button onclick="expandNewer()">加载更晚 ' + Math.min(120, s.count - SESS.hi) + ' 条 ↓</button></div>');
  document.getElementById('sessBody').innerHTML = '<div class="trans">' + rows.join('') + '</div>';
  const n = SESS.matched.length;
  const pos = n ? SESS.matched.indexOf(SESS.cur) + 1 : 0;
  const pb = document.getElementById('sessPrev'), nb = document.getElementById('sessNext');
  pb.disabled = nb.disabled = n === 0;
  pb.textContent = n ? '↑ ' + pos + '/' + n : '↑';
  nb.textContent = '↓';
  requestAnimationFrame(() => {
    if (mode === 'anchor' || mode === 'match') {
      const sel = mode === 'match' ? '.msg[data-i="' + SESS.cur + '"]' : '.msg.mhit';
      const el = document.querySelector(sel) || document.querySelector('.trans');
      if (mode === 'match') { const d = el && el.querySelector('details.disc'); if (d) d.open = true; }
      if (el) el.scrollIntoView({ block: mode === 'anchor' ? 'start' : 'center' });
      if (mode === 'anchor') window.scrollBy(0, -76);
    } else if (mode === 'older') {
      window.scrollBy(0, document.body.scrollHeight - prevH);
    }
  });
}
function expandOlder() { SESS.lo = Math.max(0, SESS.lo - 120); renderSession('older'); }
function expandNewer() { SESS.hi = Math.min(SESS.data.count, SESS.hi + 120); renderSession('stay'); }
function expandMsg(i) { EXPANDED.add(i); renderSession('stay'); }
function gotoMatch(dir) {
  if (!SESS || !SESS.matched.length) return;
  let pos = SESS.matched.indexOf(SESS.cur);
  if (pos < 0) pos = 0; else pos = (pos + dir + SESS.matched.length) % SESS.matched.length;
  SESS.cur = SESS.matched[pos];
  DISC_OPEN.add(SESS.cur);
  if (SESS.cur < SESS.lo) SESS.lo = Math.max(0, SESS.cur - 90);
  if (SESS.cur >= SESS.hi) SESS.hi = Math.min(SESS.data.count, SESS.cur + 91);
  renderSession('match');
}
async function toggleRaw() {
  if (!SESS) return;
  const next = !SESS.raw;
  if (next) toast('从原始 JSONL 读取未截断全文，大会话可能较慢…');
  const r = await api('/api/session', { path: SESS.data.path, host: SESS.host, raw: next });
  if (!r.ok) { toast('读取失败：' + r.error, 'err'); return; }
  initSession(r.session, SESS.host, SESS.anchor, SESS.terms, next);
}
function copySessionPath() {
  if (!SESS) return;
  navigator.clipboard.writeText(SESS.data.path).then(
    () => toast('已复制会话路径', 'ok'),
    () => toast('复制失败', 'err'));
}

/* ---------- icons ---------- */
function iconDb() { return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/></svg>'; }
function iconMsg() { return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>'; }
function iconDev() { return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="7" rx="2"/><rect x="2" y="13" width="20" height="7" rx="2"/><path d="M6 7.5h.01M6 16.5h.01"/></svg>'; }
function iconClock() { return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>'; }
// Official marks: Claude and OpenAI glyphs from simple-icons (CC0), pi logo from pi.dev.
function srcIcon(s) {
  if (s === 'claude')
    return '<svg viewBox="0 0 24 24" fill="currentColor" role="img" aria-label="Claude Code"><path d="m4.7144 15.9555 4.7174-2.6471.079-.2307-.079-.1275h-.2307l-.7893-.0486-2.6956-.0729-2.3375-.0971-2.2646-.1214-.5707-.1215-.5343-.7042.0546-.3522.4797-.3218.686.0608 1.5179.1032 2.2767.1578 1.6514.0972 2.4468.255h.3886l.0546-.1579-.1336-.0971-.1032-.0972L6.973 9.8356l-2.55-1.6879-1.3356-.9714-.7225-.4918-.3643-.4614-.1578-1.0078.6557-.7225.8803.0607.2246.0607.8925.686 1.9064 1.4754 2.4893 1.8336.3643.3035.1457-.1032.0182-.0728-.164-.2733-1.3539-2.4467-1.445-2.4893-.6435-1.032-.17-.6194c-.0607-.255-.1032-.4674-.1032-.7285L6.287.1335 6.6997 0l.9957.1336.419.3642.6192 1.4147 1.0018 2.2282 1.5543 3.0296.4553.8985.2429.8318.091.255h.1579v-.1457l.1275-1.706.2368-2.0947.2307-2.6957.0789-.7589.3764-.9107.7468-.4918.5828.2793.4797.686-.0668.4433-.2853 1.8517-.5586 2.9021-.3643 1.9429h.2125l.2429-.2429.9835-1.3053 1.6514-2.0643.7286-.8196.85-.9046.5464-.4311h1.0321l.759 1.1293-.34 1.1657-1.0625 1.3478-.8804 1.1414-1.2628 1.7-.7893 1.36.0729.1093.1882-.0183 2.8535-.607 1.5421-.2794 1.8396-.3157.8318.3886.091.3946-.3278.8075-1.967.4857-2.3072.4614-3.4364.8136-.0425.0304.0486.0607 1.5482.1457.6618.0364h1.621l3.0175.2247.7892.522.4736.6376-.079.4857-1.2142.6193-1.6393-.3886-3.825-.9107-1.3113-.3279h-.1822v.1093l1.0929 1.0686 2.0035 1.8092 2.5075 2.3314.1275.5768-.3218.4554-.34-.0486-2.2039-1.6575-.85-.7468-1.9246-1.621h-.1275v.17l.4432.6496 2.3436 3.5214.1214 1.0807-.17.3521-.6071.2125-.6679-.1214-1.3721-1.9246L14.38 17.959l-1.1414-1.9428-.1397.079-.674 7.2552-.3156.3703-.7286.2793-.6071-.4614-.3218-.7468.3218-1.4753.3886-1.9246.3157-1.53.2853-1.9004.17-.6314-.0121-.0425-.1397.0182-1.4328 1.9672-2.1796 2.9446-1.7243 1.8456-.4128.164-.7164-.3704.0667-.6618.4008-.5889 2.386-3.0357 1.4389-1.882.929-1.0868-.0062-.1579h-.0546l-6.3385 4.1164-1.1293.1457-.4857-.4554.0608-.7467.2307-.2429 1.9064-1.3114Z"/></svg>';
  if (s === 'codex')
    return '<svg viewBox="0 0 24 24" fill="currentColor" role="img" aria-label="Codex"><path d="M22.2819 9.8211a5.9847 5.9847 0 0 0-.5157-4.9108 6.0462 6.0462 0 0 0-6.5098-2.9A6.0651 6.0651 0 0 0 4.9807 4.1818a5.9847 5.9847 0 0 0-3.9977 2.9 6.0462 6.0462 0 0 0 .7427 7.0966 5.98 5.98 0 0 0 .511 4.9107 6.051 6.051 0 0 0 6.5146 2.9001A5.9847 5.9847 0 0 0 13.2599 24a6.0557 6.0557 0 0 0 5.7718-4.2058 5.9894 5.9894 0 0 0 3.9977-2.9001 6.0557 6.0557 0 0 0-.7475-7.0729zm-9.022 12.6081a4.4755 4.4755 0 0 1-2.8764-1.0408l.1419-.0804 4.7783-2.7582a.7948.7948 0 0 0 .3927-.6813v-6.7369l2.02 1.1686a.071.071 0 0 1 .038.052v5.5826a4.504 4.504 0 0 1-4.4945 4.4944zm-9.6607-4.1254a4.4708 4.4708 0 0 1-.5346-3.0137l.142.0852 4.783 2.7582a.7712.7712 0 0 0 .7806 0l5.8428-3.3685v2.3324a.0804.0804 0 0 1-.0332.0615L9.74 19.9502a4.4992 4.4992 0 0 1-6.1408-1.6464zM2.3408 7.8956a4.485 4.485 0 0 1 2.3655-1.9728V11.6a.7664.7664 0 0 0 .3879.6765l5.8144 3.3543-2.0201 1.1685a.0757.0757 0 0 1-.071 0l-4.8303-2.7865A4.504 4.504 0 0 1 2.3408 7.872zm16.5963 3.8558L13.1038 8.364 15.1192 7.2a.0757.0757 0 0 1 .071 0l4.8303 2.7913a4.4944 4.4944 0 0 1-.6765 8.1042v-5.6772a.79.79 0 0 0-.407-.667zm2.0107-3.0231l-.142-.0852-4.7735-2.7818a.7759.7759 0 0 0-.7854 0L9.409 9.2297V6.8974a.0662.0662 0 0 1 .0284-.0615l4.8303-2.7866a4.4992 4.4992 0 0 1 6.6802 4.66zM8.3065 12.863l-2.02-1.1638a.0804.0804 0 0 1-.038-.0567V6.0742a4.4992 4.4992 0 0 1 7.3757-3.4537l-.142.0805L8.704 5.459a.7948.7948 0 0 0-.3927.6813zm1.0976-2.3654l2.602-1.4998 2.6069 1.4998v2.9994l-2.5974 1.4997-2.6067-1.4997Z"/></svg>';
  if (s === 'pi')
    return '<svg role="img" aria-label="Pi" viewBox="165 165 470 470"><path fill="#F09082" d="M165.29 165.29H517.36V400H400V282.65H165.29Z"/><path fill="#4D9ABF" d="M165.29 282.65H282.65V400H400V517.36H282.65V634.72H165.29Z"/><path fill="#F1BE58" d="M517.36 400H634.72V634.72H517.36Z"/></svg>';
  return '<span style="font-weight:700">' + esc(String(s || '?').charAt(0).toUpperCase()) + '</span>';
}
function iconChevron() {
  return '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 6 6 6-6 6"/></svg>';
}

refresh(true).then(probeRemotes);
if (location.hash === '#devices' || location.hash === '#search' || location.hash === '#logs') go(location.hash.slice(1));
else if (location.hash.startsWith('#session')) openSessionFromHash();
</script>
</body></html>
"""
