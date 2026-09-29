"""Multi-hop search over real mnemo processes, one throwaway HOME per device.

Device A is this test process; B, C, D are `bin/mnemo` subprocesses reached
through the "local" transport (a HOME on this machine instead of SSH).
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import unittest

from helpers import REPO, DemoHome

from mnemo import dashboard, remote
from mnemo.index import Index

MNEMO = [sys.executable, os.path.join(REPO, "bin", "mnemo")]


def _session(home, word):
    """One Claude session whose prompt contains a word unique to this device."""
    d = os.path.join(home, ".claude", "projects", "-home-" + word)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "%s.jsonl" % word), "w") as f:
        f.write(json.dumps({"type": "user", "sessionId": word, "cwd": "/home/" + word,
                            "timestamp": "2026-09-29T08:00:00Z",
                            "message": {"role": "user", "content": "codename %s rollout plan" % word}}) + "\n")
        f.write(json.dumps({"type": "assistant", "sessionId": word, "cwd": "/home/" + word,
                            "timestamp": "2026-09-29T08:00:05Z",
                            "message": {"role": "assistant", "content": [{"type": "text", "text": "%s acknowledged" % word}]}}) + "\n")


class Device:
    def __init__(self, name, root):
        self.name = name
        self.home = os.path.join(root, name)
        _session(self.home, "word" + name)
        self.config = os.path.join(self.home, ".mnemo")
        os.makedirs(self.config, exist_ok=True)

    def link(self, *neighbors, **extra):
        """Register neighbors (Device objects) in this device's remotes.json."""
        rows = [dict({"name": n.name, "host": "local:" + n.name, "transport": "local", "home": n.home}, **extra)
                for n in neighbors]
        with open(os.path.join(self.config, "remotes.json"), "w") as f:
            json.dump({"remotes": rows}, f)

    def node(self, forward):
        subprocess.run(MNEMO + ["node", "--name", self.name, "--forward", "on" if forward else "off"],
                       env=dict(os.environ, HOME=self.home), check=True, capture_output=True)


class MeshTest(unittest.TestCase):
    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.demo = DemoHome()
        self.demo.activate()
        root = self.demo.root
        _session(self.demo.home, "wordA")
        self.idx = Index(self.demo.db)
        self.idx.sync(home=self.demo.home)
        self.b, self.c, self.d = Device("B", root), Device("C", root), Device("D", root)
        for dev in (self.b, self.c, self.d):
            dev.node(forward=False)

    def tearDown(self):
        self.idx.close()
        self.demo.deactivate()
        self.demo.cleanup()

    def link_a(self, *neighbors, **extra):
        rows = [dict({"name": n.name, "host": "local:" + n.name, "transport": "local", "home": n.home}, **extra)
                for n in neighbors]
        remote.save_remotes(rows)

    def search(self, word, **kw):
        hits, warnings = remote.fan_out_search(self.idx, word, **kw)
        return hits, warnings

    def routes(self, word, **kw):
        return sorted({h["host"] for h in self.search(word, **kw)[0]})

    # ---------------------------------------------------------------- routing

    def test_direct_neighbors_behave_as_before(self):
        self.link_a(self.b)
        self.assertEqual(self.routes("wordA"), ["local"])
        self.assertEqual(self.routes("wordB"), ["B"])

    def test_chain_needs_forwarding_on_the_relay(self):
        self.link_a(self.b)
        self.b.link(self.c)
        self.assertEqual(self.routes("wordC"), [], "B does not relay by default")
        self.b.node(forward=True)
        self.assertEqual(self.routes("wordC"), ["B/C"])

    def test_diamond_keeps_the_shortest_route_once(self):
        self.link_a(self.b, self.c)
        self.b.link(self.c)
        self.b.node(forward=True)
        for _ in range(2):  # first run learns neighbor ids, second suppresses the detour
            hits, _ = self.search("wordC")
            # C's two matching lines, each once, via the direct route; no B/C copies.
            self.assertEqual([h["host"] for h in hits], ["C", "C"])
            self.assertEqual(len({(h["path"], h["lineno"]) for h in hits}), 2)
        learned = {r["name"]: r.get("node_id") for r in remote.load_remotes()}
        self.assertTrue(all(learned.values()), learned)

    def test_explicit_route_reaches_a_direct_neighbor_through_a_relay(self):
        self.link_a(self.b, self.c)
        self.b.link(self.c)
        self.b.node(forward=True)
        self.search("wordC")  # learn neighbor ids
        self.assertEqual(self.routes("wordC", hosts=["B/C"]), ["B/C"])

    def test_cycles_terminate(self):
        self.link_a(self.b)
        self.b.link(self.c)
        self.c.link(self.b)
        self.b.node(forward=True)
        self.c.node(forward=True)
        self.assertEqual(self.routes("wordC"), ["B/C"])
        self.assertEqual(self.routes("wordB"), ["B"])

    def test_ttl_bounds_the_depth(self):
        self.link_a(self.b)
        self.b.link(self.c)
        self.c.link(self.d)
        self.b.node(forward=True)
        self.c.node(forward=True)
        self.assertEqual(self.routes("wordD"), ["B/C/D"])
        self.assertEqual(self.routes("wordD", ttl=2), [])
        self.assertEqual(self.routes("wordC", ttl=2), ["B/C"])

    def test_host_filter_accepts_routes(self):
        self.link_a(self.b)
        self.b.link(self.c)
        self.b.node(forward=True)
        self.assertEqual(self.routes("codename", hosts=["B/C"]), ["B/C"])
        self.assertEqual(self.routes("codename", hosts=["B"]), ["B"])

    # ------------------------------------------------------------------ reads

    def test_reads_follow_the_route(self):
        self.link_a(self.b)
        self.b.link(self.c)
        self.b.node(forward=True)
        hit = self.search("wordC")[0][0]
        rows = remote.remote_context(hit["host"], hit["path"], hit["lineno"], 1, 1)
        self.assertTrue(any("wordC" in r["text"] for r in rows))
        sess = remote.remote_session(hit["host"], hit["path"])
        self.assertEqual(sess["count"], 2)

        self.b.node(forward=False)
        with self.assertRaises(remote.RemoteError) as cm:
            remote.remote_session(hit["host"], hit["path"])
        self.assertIn("does not forward", str(cm.exception))

    # --------------------------------------------------------- compatibility

    def test_protocol_1_neighbors_are_asked_the_old_way(self):
        self.link_a(self.b, proto=1)
        self.b.link(self.c)
        self.b.node(forward=True)
        self.assertEqual(self.routes("wordB"), ["B"])
        self.assertEqual(self.routes("wordC"), [], "an old neighbor is never asked to relay")
        with self.assertRaises(remote.RemoteError):
            remote.remote_session("B/C", "/nope")

    def test_dashboard_diagnose_includes_remote_and_relayed_hits(self):
        self.link_a(self.b)
        self.b.link(self.c)
        self.b.node(forward=True)
        saved = dashboard.DEFAULT_DB_PATH
        dashboard.DEFAULT_DB_PATH = self.demo.db
        try:
            result = dashboard.diagnose_search("codename", None, 20)
        finally:
            dashboard.DEFAULT_DB_PATH = saved
        self.assertEqual({h["host"] for h in result["merged"]}, {"local", "B", "B/C"})
        self.assertEqual({p["host"] for p in result["per_host"] if p["ok"]}, {"local", "B"})


if __name__ == "__main__":
    unittest.main()
