import contextlib
import datetime
import io
import json
import unittest

from helpers import DemoHome

from mnemo import cli, mcp_server
from mnemo.index import Index
from mnemo.search import recent, search, since_ts

# The demo sessions start between 2026-09-19 and 2026-09-26.
NOW = datetime.datetime(2026, 9, 28, 0, 0, tzinfo=datetime.timezone.utc)


class SinceTsTest(unittest.TestCase):
    def test_days_count_back_from_now(self):
        self.assertEqual(since_ts("3d", NOW), "2026-09-25T00:00:00Z")
        self.assertEqual(since_ts("10d", NOW), "2026-09-18T00:00:00Z")
        self.assertEqual(since_ts("0d", NOW), "2026-09-28T00:00:00Z")

    def test_dates_and_timestamps(self):
        self.assertEqual(since_ts("2026-09-25"), "2026-09-25T00:00:00Z")
        self.assertEqual(since_ts("2026-09-25T08:00:00+08:00"), "2026-09-25T00:00:00Z")
        self.assertEqual(since_ts("2026-09-25T08:00"), "2026-09-25T08:00:00Z")
        # What a device forwards to its neighbors comes back unchanged.
        self.assertEqual(since_ts("2026-09-25T00:00:00Z"), "2026-09-25T00:00:00Z")

    def test_empty_means_no_filter(self):
        self.assertIsNone(since_ts(None))
        self.assertIsNone(since_ts(""))

    def test_anything_else_is_an_error(self):
        for value in ("3x", "d", "last week", "2026-13-01"):
            with self.assertRaises(ValueError, msg=value):
                since_ts(value, NOW)


class SinceFilterTest(unittest.TestCase):
    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.demo = DemoHome()
        self.demo.activate()
        self.idx = Index(self.demo.db)
        self.idx.sync(home=self.demo.home)

    def tearDown(self):
        self.idx.close()
        self.demo.deactivate()
        self.demo.cleanup()

    def test_recent_keeps_only_the_last_n_days(self):
        # '3d' used to be compared as text: '2026-…' < '3d…' matched nothing, and
        # '10d' matched everything because '2026-…' > '10d…'.
        three = recent(self.idx, since=since_ts("3d", NOW))
        self.assertEqual([r["started_ts"][:10] for r in three], ["2026-09-26", "2026-09-25"])
        self.assertEqual(len(recent(self.idx, since=since_ts("8d", NOW))), 4)
        self.assertEqual(len(recent(self.idx, since=since_ts("10d", NOW))), 5)

    def test_search_keeps_only_the_last_n_days(self):
        hits = search(self.idx, "backoff", since=since_ts("3d", NOW))
        self.assertTrue(hits)
        self.assertTrue(all(h["ts"] >= "2026-09-25" for h in hits))

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main(["--db", self.demo.db] + list(argv))
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_cli_recent_takes_days(self):
        code, out, _ = self.run_cli("recent", "--since", "100000d", "--no-sync", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)), 5)
        code, out, _ = self.run_cli("recent", "--since", "0d", "--no-sync", "--json")
        self.assertEqual((code, json.loads(out)), (0, []))

    def test_cli_rejects_a_value_it_cannot_read(self):
        code, _, err = self.run_cli("recent", "--since", "3x", "--no-sync", "--json")
        self.assertEqual(code, 2)
        self.assertIn("Nd (e.g. 7d)", err)

    def test_mcp_recent_takes_days(self):
        result = mcp_server.handle_call("list_recent_sessions", {"since": "100000d"}, self.idx)
        self.assertEqual(len(json.loads(result["content"][0]["text"])), 5)
        with self.assertRaises(ValueError):
            mcp_server.handle_call("list_recent_sessions", {"since": "3x"}, self.idx)


if __name__ == "__main__":
    unittest.main()
