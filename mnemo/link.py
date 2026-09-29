"""Inbound links: let a device you can reach search you back, without it reaching you.

A laptop can usually SSH into its devboxes, but they cannot connect back to
it. `mnemo link <remote> --allow-inbound`, run on the laptop, keeps one SSH
session open to the remote running `mnemo link --serve`. On the remote, that
process listens on a Unix socket only its user can open and registers this
device as a neighbor with the "link" transport. A request from the remote goes
over the socket, back up the SSH session, and is answered here by `mnemo`
itself, but only for the read-only commands a neighbor needs (ALLOWED below):
the remote never gets a shell here, and this device's relay policy still
decides whether it may reach anything beyond.

Wire format: one JSON object per line, in both directions.
  laptop -> remote   {"hello": {"name", "id"}}, then {"id", "rc", "out", "err"}
  remote -> laptop   {"ready": {"name", "socket"}}, then {"id", "argv", "timeout"}
"""
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time

from . import remote as remote_mod
from .remote import CONNECT_TIMEOUT, RemoteError

# `link --serve` exits with this when it refuses the link (name clash, bad name):
# retrying cannot help, so the linking side stops instead.
REFUSED = 3

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def self_argv():
    """argv that runs this same mnemo: the checkout's launcher, or (pip/uv installs) the
    package imported from where this one lives. Not `-m mnemo`: that would pick up a
    `mnemo/` directory in whatever the working directory happens to be."""
    launcher = os.path.join(REPO, "bin", "mnemo")
    if os.path.isfile(launcher):
        return [sys.executable, launcher]
    boot = "import sys; sys.path.insert(0, %r); from mnemo.cli import main; sys.exit(main())" % REPO
    return [sys.executable, "-c", boot]


class LinkRefused(RemoteError):
    pass


# ------------------------------------------------------------------ policy

def allowed(argv):
    """Whether a neighbor may run argv here over an inbound link. Read-only by design."""
    argv = list(argv)
    if not argv or "--db" in argv:
        return False
    cmd, rest = argv[0], argv[1:]
    if argv in (["--version"], ["index"], ["status", "--json"], ["node", "--json"]):
        return True
    if cmd == "node":  # topology probe; never --name or --forward
        return "--probe" in rest and "--relay" in rest and not {"--name", "--forward"} & set(rest)
    # --relay makes this device apply its own forward policy, as for any neighbor.
    return cmd in ("search", "context", "session") and "--relay" in rest


# ------------------------------------------------------------ remote side

def socket_path(name):
    path = os.path.join(remote_mod.CONFIG_DIR, "links", name + ".sock")
    if len(path) < 100:  # sun_path is ~104 bytes on macOS
        return path
    short = os.path.join(tempfile.gettempdir(), "mnemo-%d" % os.getuid())
    return os.path.join(short, hashlib.sha1(path.encode("utf-8")).hexdigest()[:16] + ".sock")


def is_up(remote):
    """A link neighbor is only there while its session is open (a laptop asleep is not an error)."""
    return os.path.exists(socket_path(remote["name"]))


def _register(name, node_id):
    """Add (or refresh) the linking device as a neighbor of this one."""
    with remote_mod._remotes_lock:
        remotes = remote_mod.load_remotes()
        for r in remotes:
            if r["name"] == name:
                if r.get("transport") != "link":
                    raise RemoteError("a neighbor named %r already exists here; rename the linking device "
                                      "(mnemo node --name) or remove that neighbor" % name)
                r["node_id"] = node_id
                break
        else:
            remotes.append({"name": name, "host": "link:" + name, "transport": "link", "node_id": node_id})
        remote_mod.save_remotes(remotes)


def serve(stdin=None, stdout=None):
    """`mnemo link --serve`: bridge local requests to the linking device over stdio."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    hello = json.loads(stdin.readline() or "{}").get("hello") or {}
    name = remote_mod.check_node_name(hello.get("name") or "")
    _register(name, hello.get("id"))

    path = socket_path(name)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    os.chmod(os.path.dirname(path), 0o700)
    if os.path.exists(path):
        os.unlink(path)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    os.chmod(path, 0o600)
    server.listen(16)

    out_lock = threading.Lock()
    pending = {}
    counter = [0]

    def send(obj):
        with out_lock:
            stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
            stdout.flush()

    def handle(conn):
        with conn:
            try:
                req = json.loads(_recv_line(conn))
                with out_lock:
                    counter[0] += 1
                    rid = counter[0]
                done = threading.Event()
                pending[rid] = [done, None]
                send({"id": rid, "argv": req["argv"], "timeout": req.get("timeout", 60)})
                if not done.wait(req.get("timeout", 60) + 10):
                    reply = {"rc": 1, "out": "", "err": "%s: timed out over the link" % name}
                else:
                    reply = pending[rid][1]
                pending.pop(rid, None)
                conn.sendall((json.dumps(reply, ensure_ascii=False) + "\n").encode("utf-8"))
            except (OSError, ValueError, KeyError):
                pass

    def accept():
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            threading.Thread(target=handle, args=(conn,), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()
    send({"ready": {"name": name, "socket": path}})
    try:
        for line in stdin:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            slot = pending.get(msg.get("id"))
            if slot:
                slot[1] = msg
                slot[0].set()
    finally:
        server.close()
        try:
            os.unlink(path)
        except OSError:
            pass
        down = {"rc": 1, "out": "", "err": "%s: the link closed" % name}
        for slot in list(pending.values()):
            slot[1] = down
            slot[0].set()
    return 0


def _recv_line(conn):
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = conn.recv(65536)
        if not chunk:
            break
        buf += chunk
    return buf.decode("utf-8")


def link_exec(remote, argv, timeout):
    """remote_exec for the "link" transport: ask the linked device through its socket."""
    path = socket_path(remote["name"])
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout + 15)
    try:
        s.connect(path)
        s.sendall((json.dumps({"argv": list(argv), "timeout": timeout}) + "\n").encode("utf-8"))
        reply = json.loads(_recv_line(s) or "null")
    except (OSError, ValueError):
        raise RemoteError("%s: not linked right now (run `mnemo link <this device> --allow-inbound` there)"
                          % remote["name"])
    finally:
        s.close()
    if not reply:
        raise RemoteError("%s: the link closed" % remote["name"])
    if reply.get("rc") != 0:
        msg = (reply.get("err") or reply.get("out") or "exit code %s" % reply.get("rc")).strip()
        raise RemoteError("%s: %s" % (remote["name"], msg.splitlines()[-1][:200] if msg else "failed"))
    return reply.get("out", "")


# ------------------------------------------------------------- local side

def state_path(name):
    return os.path.join(remote_mod.CONFIG_DIR, "links", "out-%s.json" % name)


def write_state(name, state, error=None):
    """What the link to `name` is doing, for `mnemo link --list` and the dashboard."""
    path = state_path(name)
    try:
        remote_mod._atomic_json(path, {"remote": name, "pid": os.getpid(), "state": state,
                                       "since": int(time.time()), "error": error})
    except OSError:
        pass


def read_state(name):
    """The last state the link process wrote, or "stopped" if that process is gone."""
    try:
        with open(state_path(name), encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        return {"remote": name, "state": "stopped", "pid": None, "since": None, "error": None}
    if st.get("state") not in ("stopped", "refused") and not _alive(st.get("pid")):
        st.update(state="stopped", pid=None)
    return st


def _alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _answer(argv, timeout):
    if not allowed(argv):
        return {"rc": 2, "out": "", "err": "refused over an inbound link: mnemo %s" % " ".join(argv[:2])}
    try:
        p = subprocess.run(self_argv() + list(argv), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"rc": 1, "out": "", "err": "timed out after %ds" % timeout}
    return {"rc": p.returncode, "out": p.stdout, "err": p.stderr}


def _serve_command(remote):
    argv = ["link", "--serve"]
    if remote.get("transport") == "local":  # tests: another HOME on this machine
        own = os.path.join(remote["home"], "mnemo", "bin", "mnemo")
        cmd = ([sys.executable, own] if os.path.isfile(own) else self_argv()) + argv
        return cmd, dict(os.environ, HOME=remote["home"])
    ssh = [
        "ssh",
        "-o", "ConnectTimeout=%d" % CONNECT_TIMEOUT,
        "-o", "BatchMode=yes",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
        "-o", "ExitOnForwardFailure=yes",
        remote["host"],
        remote_mod._remote_command(remote, argv),
    ]
    return ssh, None


def connect(remote, log=lambda m: None, stop=None):
    """One link session: runs until the connection drops (or `stop` is set). Returns True if it was ready."""
    node = remote_mod.load_node()
    cmd, env = _serve_command(remote)
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, bufsize=1, env=env)
    lock = threading.Lock()

    def send(obj):
        with lock:
            proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
            proc.stdin.flush()

    def work(msg):
        reply = _answer(msg.get("argv") or [], int(msg.get("timeout") or 60))
        reply["id"] = msg["id"]
        try:
            send(reply)
        except (OSError, ValueError):
            pass

    ready = False
    if stop is not None:
        threading.Thread(target=lambda: (stop.wait(), proc.stdin.close()), daemon=True).start()
    try:
        send({"hello": {"name": node["name"], "id": node["id"]}})
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "ready" in msg:
                ready = True
                write_state(remote["name"], "connected")
                log("linked: %s can now search this device as %r" % (remote["name"], msg["ready"]["name"]))
            elif "argv" in msg:
                threading.Thread(target=work, args=(msg,), daemon=True).start()
    except (OSError, ValueError):
        pass
    finally:
        # Closing its stdin lets the other end clean up (unlink its socket) and exit;
        # terminate only if it does not.
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.terminate()
            proc.wait()
    if not ready:
        err = (proc.stderr.read() or "").strip().splitlines()
        msg = "%s: %s" % (remote["name"], err[-1] if err else "link could not start")
        raise (LinkRefused if proc.returncode == REFUSED else RemoteError)(msg)
    return ready


def run(remote, log=print, stop=None):
    """Keep the link up: reconnect with backoff until interrupted."""
    delay = 2
    name = remote["name"]
    try:
        while stop is None or not stop.is_set():
            write_state(name, "connecting")
            try:
                connect(remote, log=log, stop=stop)
                delay = 2
                log("link to %s dropped; reconnecting" % name)
            except LinkRefused as exc:
                write_state(name, "refused", str(exc))
                raise
            except RemoteError as exc:
                write_state(name, "retrying", str(exc))
                log("link to %s failed: %s; retrying in %ds" % (name, exc, delay))
                if stop is not None and stop.wait(delay):
                    break
                if stop is None:
                    time.sleep(delay)
                delay = min(delay * 2, 60)
    finally:
        if read_state(name).get("state") != "refused":
            write_state(name, "stopped")
