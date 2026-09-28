import contextlib
import glob
import io
import os
import time
import unittest

from helpers import DemoHome

from mnemo.index import Index
from mnemo.search import build_match, search


def quiet_demo():
    with contextlib.redirect_stdout(io.StringIO()):
        return DemoHome()


class IndexTest(unittest.TestCase):
    def setUp(self):
        self.demo = quiet_demo()
        self.demo.activate()
        self.idx = Index(self.demo.db)
        self.stats = self.idx.sync(home=self.demo.home)

    def tearDown(self):
        self.idx.close()
        self.demo.deactivate()
        self.demo.cleanup()

    def test_full_index(self):
        self.assertEqual(self.stats["files_new"], 5)
        self.assertEqual(self.stats["messages"], 45)
        counts = self.idx.counts()
        self.assertEqual({k: v["files"] for k, v in counts.items()}, {"claude": 2, "codex": 2, "pi": 1})

    def test_incremental_sync_is_a_noop_when_nothing_changed(self):
        stats = self.idx.sync(home=self.demo.home)
        self.assertEqual((stats["files_new"], stats["files_updated"], stats["files_removed"]), (0, 0, 0))

    def test_appended_and_removed_files(self):
        pi_file = glob.glob(self.demo.path(".pi", "agent", "sessions", "*", "*.jsonl"))[0]
        with open(pi_file, "a") as f:
            f.write('{"type":"message","timestamp":"2026-09-19T13:20:00Z",'
                    '"message":{"role":"user","content":[{"type":"text","text":"zebra crossing"}]}}\n')
        os.utime(pi_file, (time.time() + 5, time.time() + 5))
        stats = self.idx.sync(home=self.demo.home)
        self.assertEqual(stats["files_updated"], 1)
        self.assertEqual(len(search(self.idx, "zebra")), 1)

        os.remove(pi_file)
        stats = self.idx.sync(home=self.demo.home)
        self.assertEqual(stats["files_removed"], 1)
        self.assertEqual(search(self.idx, "zebra"), [])

    def test_sync_if_stale_throttles(self):
        self.assertIsNone(self.idx.sync_if_stale(min_interval=60))
        self.idx.db.execute("UPDATE meta SET value = ? WHERE key = 'last_sync'", (str(time.time() - 120),))
        stats = self.idx.sync_if_stale(min_interval=60)
        self.assertIsNotNone(stats)
        self.assertEqual(stats["files_removed"], 0)  # HOME is the demo home


class SearchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.demo = quiet_demo()
        cls.idx = Index(cls.demo.db)
        cls.idx.sync(home=cls.demo.home)

    @classmethod
    def tearDownClass(cls):
        cls.idx.close()
        cls.demo.cleanup()

    def test_build_match_ands_terms_and_adds_cjk_grams(self):
        self.assertEqual(build_match("retry budget"), '(body : "retry"*) AND (body : "budget"*)')
        m = build_match("退避")
        self.assertIn('body : "退避"*', m)
        self.assertIn('grams : (("退避"))', m)

    def test_quotes_are_escaped(self):
        self.assertIn('"a""b"', build_match('a"b'))

    def test_prefix_match_and_snippet_marks(self):
        hits = search(self.idx, "back")
        self.assertTrue(hits)
        self.assertTrue(all("[[" in h["snippet"] for h in hits))

    def test_and_semantics(self):
        self.assertEqual(len(search(self.idx, "retry budget")), len(search(self.idx, "budget retry")))
        self.assertEqual(search(self.idx, "backoff zzzunmatched"), [])

    def test_filters(self):
        self.assertEqual({h["source"] for h in search(self.idx, "backoff", sources=["pi"])}, {"pi"})
        self.assertTrue(all("relay" in h["cwd"] for h in search(self.idx, "backoff", cwd="relay")))
        self.assertTrue(all(h["ts"] >= "2026-09-25" for h in search(self.idx, "backoff", since="2026-09-25")))
        kinds = {h["kind"] for h in search(self.idx, "backoff", kinds=["tool_call"])}
        self.assertEqual(kinds, {"tool_call"})

    def test_limit(self):
        self.assertEqual(len(search(self.idx, "backoff", limit=3)), 3)


if __name__ == "__main__":
    unittest.main()
