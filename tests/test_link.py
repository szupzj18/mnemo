"""Inbound links over real mnemo processes: A links in to B, then B searches A.

A (the "laptop") can reach B but not the other way round. `mnemo link B
--allow-inbound` runs on A; B's searches and reads then reach A through it.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import time
import unittest

from helpers import DemoHome
from test_mesh import MNEMO, Device

from mnemo import link, remote


class AllowedTest(unittest.TestCase):
    def test_only_read_only_commands_pass(self):
        ok = [
            ["--version"], ["index"], ["status", "--json"], ["node", "--json"],
            ["node", "--json", "--probe", "--relay", "--ttl", "2"],
            ["search", "x", "--json", "--limit", "5", "--relay", "--ttl", "2"],
            ["context", "/p", "3", "--json", "--relay"],
            ["session", "/p", "--json", "--relay", "--host", "C"],
        ]
        refused = [
            [], ["node", "--forward", "on"], ["node", "--json", "--name", "x"],
            ["node", "--probe", "--relay", "--forward", "on"], ["upgrade"], ["remote", "add", "x"],
            ["setup"], ["dashboard"], ["mcp"], ["link", "--serve"],
            ["search", "x", "--json"],  # without --relay it would skip this device's forward policy
            ["search", "x", "--host", "C", "--json"], ["session", "/p", "--host", "C"],
            ["search", "x", "--relay", "--db", "/tmp/other.db"], ["index", "--rebuild"],
        ]
        for argv in ok:
            self.assertTrue(link.allowed(argv), argv)
        for argv in refused:
            self.assertFalse(link.allowed(argv), argv)


class LinkTest(unittest.TestCase):
    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.demo = DemoHome()
        root = self.demo.root
        self.a, self.b, self.c = Device("A", root), Device("B", root), Device("C", root)
        for dev in (self.a, self.b, self.c):
            dev.node(forward=False)
        self.a.link(self.b, self.c)
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.terminate()
                p.wait(10)
            for f in (p.stdout, p.stderr):
                f.close()
        self.demo.cleanup()

    def mnemo(self, dev, *argv, check=True):
        p = subprocess.run(MNEMO + list(argv), env=dict(os.environ, HOME=dev.home),
                           capture_output=True, text=True, timeout=120)
        if check and p.returncode:
            self.fail("mnemo %s on %s failed: %s" % (" ".join(argv), dev.name, p.stderr))
        return p

    def start_link(self, frm, to):
        p = subprocess.Popen(MNEMO + ["link", to.name, "--allow-inbound"], env=dict(os.environ, HOME=frm.home),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.procs.append(p)
        deadline = time.time() + 30
        while time.time() < deadline:
            line = p.stdout.readline()
            if "linked:" in line:
                return p
            if not line and p.poll() is not None:
                break
        self.fail("link did not come up: %s" % p.stderr.read())

    def search(self, dev, word, *extra):
        out = json.loads(self.mnemo(dev, "search", word, "--json", *extra).stdout)
        return sorted({h["host"] for h in out})

    def test_linked_device_searches_and_reads_back(self):
        self.start_link(self.a, self.b)
        with open(os.path.join(self.b.config, "remotes.json")) as f:
            remotes = json.load(f)["remotes"]
        self.assertEqual([(r["name"], r["transport"]) for r in remotes], [("A", "link")])

        self.assertEqual(self.search(self.b, "wordA"), ["A"])
        hits = json.loads(self.mnemo(self.b, "search", "wordA", "--json").stdout)
        sess = json.loads(self.mnemo(self.b, "session", hits[0]["path"], "--host", "A", "--json").stdout)
        self.assertEqual(sess["count"], 2)

        tree = json.loads(self.mnemo(self.b, "node", "--json", "--probe").stdout)
        self.assertEqual([(n["name"], n["ok"]) for n in tree["neighbors"]], [("A", True)])

    def test_the_link_is_read_only_and_keeps_the_relay_policy(self):
        self.start_link(self.a, self.b)
        # A does not relay: B sees A, not C behind it.
        self.assertEqual(self.search(self.b, "wordC"), [])
        p = self.mnemo(self.b, "session", "/x", "--host", "A/C", check=False)
        self.assertIn("does not forward", p.stderr)

        # B cannot change A's settings, and never pushes code to it.
        saved = remote.CONFIG_DIR
        remote.CONFIG_DIR = self.b.config  # talk to A through B's end of the link
        try:
            with self.assertRaises(remote.RemoteError) as cm:
                remote.remote_exec({"name": "A", "transport": "link"}, ["node", "--forward", "on"], timeout=20)
            with self.assertRaises(remote.RemoteError):
                remote.install({"name": "A", "transport": "link"})
        finally:
            remote.CONFIG_DIR = saved
        self.assertIn("refused over an inbound link", str(cm.exception))
        self.assertIn("forward  off", self.mnemo(self.a, "node").stdout)
        self.assertIn("no other devices", self.mnemo(self.b, "remote", "upgrade").stdout)

        # Once A relays, C is reachable from B through the link.
        self.a.node(forward=True)
        self.assertEqual(self.search(self.b, "wordC"), ["A/C"])

    def test_a_closed_link_is_skipped_quietly(self):
        p = self.start_link(self.a, self.b)
        hits = json.loads(self.mnemo(self.b, "search", "wordA", "--json").stdout)
        p.terminate()
        p.wait(10)
        deadline = time.time() + 10
        saved, remote.CONFIG_DIR = remote.CONFIG_DIR, self.b.config
        try:
            sock = link.socket_path("A")  # as B sees it
        finally:
            remote.CONFIG_DIR = saved
        while os.path.exists(sock):
            self.assertLess(time.time(), deadline, "the socket goes away with the session")
            time.sleep(0.2)
        # A laptop that is asleep is not an error for B's searches...
        out = self.mnemo(self.b, "search", "wordA", check=False)
        self.assertEqual(out.stderr, "")
        self.assertIn("wordB", self.mnemo(self.b, "search", "wordB").stdout)
        # ...but asking A for something explicitly says why it cannot answer.
        out = self.mnemo(self.b, "session", hits[0]["path"], "--host", "A", check=False)
        self.assertIn("not linked right now", out.stderr)

    def test_a_name_clash_stops_the_link(self):
        with open(os.path.join(self.b.config, "remotes.json"), "w") as f:
            json.dump({"remotes": [{"name": "A", "host": "somewhere-else"}]}, f)
        p = subprocess.run(MNEMO + ["link", "B", "--allow-inbound"], env=dict(os.environ, HOME=self.a.home),
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 1)
        self.assertIn("already exists", p.stderr)

    def test_linking_needs_explicit_consent(self):
        p = self.mnemo(self.a, "link", "B", check=False)
        self.assertEqual(p.returncode, 2)
        self.assertIn("--allow-inbound", p.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.b.config, "remotes.json")))


if __name__ == "__main__":
    unittest.main()
