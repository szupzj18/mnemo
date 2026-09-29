"""Long-running mnemo processes that follow code updates on their own.

An MCP server belongs to an agent session and cannot be restarted from
outside, so it runs every tool call in a fresh process (mcp_server.call_fresh);
the dashboard restarts itself in place when the code on disk changes. Both
leave a marker in ~/.mnemo/run/ so `mnemo upgrade` does not ask you to restart
them.
"""
import atexit
import os
import threading

from . import fingerprint
from . import remote as remote_mod


def _run_dir():
    return os.path.join(remote_mod.CONFIG_DIR, "run")


def mark(kind):
    """Record that this process follows code updates by itself."""
    path = os.path.join(_run_dir(), "%s-%d" % (kind, os.getpid()))
    try:
        os.makedirs(_run_dir(), exist_ok=True)
        open(path, "w").close()
    except OSError:
        return
    atexit.register(lambda: os.path.exists(path) and os.remove(path))


def marked_pids():
    """Live processes that follow code updates; markers of dead ones are cleaned up."""
    from .link import _alive

    pids = set()
    try:
        names = os.listdir(_run_dir())
    except OSError:
        return pids
    for name in names:
        try:
            pid = int(name.rsplit("-", 1)[1])
        except (IndexError, ValueError):
            continue
        if _alive(pid):
            pids.add(pid)
        else:
            try:
                os.remove(os.path.join(_run_dir(), name))
            except OSError:
                pass
    return pids


def interval():
    try:
        return float(os.environ.get("MNEMO_RELOAD_INTERVAL", "5"))
    except ValueError:
        return 5.0


def watch(on_change, every=None, stop=None):
    """Call on_change() once the code on disk differs from what this process runs.

    The new fingerprint must hold for two checks in a row, so a reload never
    lands in the middle of a `git pull` or rsync that is still writing files.
    """
    every = interval() if every is None else every
    running = fingerprint.code_fingerprint()
    stop = stop or threading.Event()

    def loop():
        seen = None
        while not stop.wait(every):
            now = fingerprint.compute()
            if now == running:
                seen = None
            elif now == seen:
                on_change()
                return
            else:
                seen = now

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return stop
