"""Long-running processes follow code updates: MCP tool calls and the dashboard."""
import contextlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest import mock

from helpers import REPO, DemoHome

from mnemo import fingerprint, live, remote


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class CopiedInstall:
    """A copy of the package we can edit while a process runs from it (like an upgrade)."""

    def __init__(self, home):
        self.site = tempfile.mkdtemp(prefix="mnemo-live-")
        shutil.copytree(fingerprint.PACKAGE, os.path.join(self.site, "mnemo"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.env = dict(os.environ, HOME=home, MNEMO_RELOAD_INTERVAL="0.2")

    def argv(self, *args):
        """How a console script starts it: import the package from site, whatever the cwd."""
        boot = "import sys; sys.path.insert(0, %r); from mnemo.cli import main; sys.exit(main())" % self.site
        return [sys.executable, "-c", boot] + list(args)

    def edit(self, rel, append):
        with open(os.path.join(self.site, "mnemo", rel), "a") as f:
            f.write(append)

    def cleanup(self):
        shutil.rmtree(self.site, ignore_errors=True)


class McpTest(unittest.TestCase):
    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.demo = DemoHome()
        self.install = CopiedInstall(self.demo.home)
        # Started from the repo root on purpose: its mnemo/ must not shadow the install.
        self.proc = subprocess.Popen(self.install.argv("mcp"), env=self.install.env, cwd=REPO,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, bufsize=1)
        self.rid = 0

    def tearDown(self):
        self.proc.stdin.close()
        self.proc.wait(10)
        self.proc.stdout.close()
        self.install.cleanup()
        self.demo.cleanup()

    def request(self, method, params=None):
        self.rid += 1
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.rid, "method": method,
                                          "params": params or {}}) + "\n")
        self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline())["result"]

    def call(self, tool, **args):
        return self.request("tools/call", {"name": tool, "arguments": args})

    def test_tool_calls_run_the_code_on_disk_now(self):
        self.request("initialize", {"protocolVersion": "2024-11-05"})
        names = [t["name"] for t in self.request("tools/list")["tools"]]
        self.assertIn("search_sessions", names)

        hits = json.loads(self.call("search_sessions", query="backoff", host="local")["content"][0]["text"])
        self.assertTrue(hits)
        self.assertTrue(self.call("get_session", path="/nope").get("isError"))

        # "Upgrade" the code under the running server: the next call uses it.
        self.install.edit("mcp_server.py", "\n\ndef handle_call(name, args, index):\n"
                                           "    return _text_result({'reloaded': name})\n")
        result = self.call("list_recent_sessions")
        self.assertEqual(json.loads(result["content"][0]["text"]), {"reloaded": "list_recent_sessions"})

    def test_the_server_is_marked_as_following_updates(self):
        self.request("initialize")
        saved = remote.CONFIG_DIR
        remote.CONFIG_DIR = os.path.join(self.demo.home, ".mnemo")
        try:
            self.assertIn(self.proc.pid, live.marked_pids())
        finally:
            remote.CONFIG_DIR = saved


class DashboardReloadTest(unittest.TestCase):
    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.demo = DemoHome()
        self.install = CopiedInstall(self.demo.home)
        self.port = free_port()
        env = dict(self.install.env, MNEMO_DASHBOARD_TOKEN="tok")
        self.proc = subprocess.Popen(self.install.argv("dashboard", "--no-open", "--port", str(self.port)),
                                     env=env, cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def tearDown(self):
        self.proc.terminate()
        self.proc.wait(10)
        self.install.cleanup()
        self.demo.cleanup()

    def version(self):
        req = urllib.request.Request("http://127.0.0.1:%d/api/status" % self.port, headers={"X-Dashboard-Token": "tok"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=5) as r:
            return json.load(r)["version"]

    def wait_for(self, want, seconds=20):
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                if self.version() == want:
                    return
            except OSError:
                pass  # restarting
            time.sleep(0.2)
        self.fail("dashboard never served version %s" % want)

    def test_restarts_on_new_code_with_the_same_port_and_token(self):
        from mnemo import __version__
        self.wait_for(__version__)
        self.install.edit("__init__.py", '__version__ = "9.9.9"\n')
        self.wait_for("9.9.9")
        self.assertIsNone(self.proc.poll(), "same process (exec), still running")


class WatchTest(unittest.TestCase):
    def test_waits_for_the_new_code_to_settle(self):
        # Running "a". Seen on disk: b, back to a (no reload), c, d (still changing), d again: reload once.
        seq = iter(["b", "a", "c", "d", "d", "d"])
        fired = threading.Event()
        calls = []
        with mock.patch.object(fingerprint, "code_fingerprint", lambda: "a"), \
                mock.patch.object(fingerprint, "compute", lambda: next(seq, "d")):
            stop = live.watch(lambda: (calls.append(1), fired.set()), every=0.01)
            self.assertTrue(fired.wait(5))
            time.sleep(0.05)
            stop.set()
        self.assertEqual(calls, [1])
        self.assertEqual(next(seq, None), "d", "fired on the second d, not before")


class MarkerTest(unittest.TestCase):
    def test_markers_of_dead_processes_are_dropped(self):
        with contextlib.redirect_stdout(io.StringIO()):
            demo = DemoHome()
        demo.activate()
        try:
            live.mark("mcp")
            run = os.path.join(remote.CONFIG_DIR, "run")
            open(os.path.join(run, "dashboard-999999"), "w").close()
            self.assertEqual(live.marked_pids(), {os.getpid()})
            self.assertEqual(sorted(os.listdir(run)), ["mcp-%d" % os.getpid()])
        finally:
            demo.deactivate()
            demo.cleanup()


if __name__ == "__main__":
    unittest.main()
