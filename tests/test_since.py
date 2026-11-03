import contextlib
import datetime
import io
import json
import sys
import unittest
from unittest import mock

from helpers import DemoHome

from mnemo import cli, mcp_server
from mnemo.index import Index
from mnemo.search import recent, search, since_ts

# The demo sessions start between 2026-09-19 and 2026-09-26.
NOW = datetime.datetime(2026, 9, 28, 0, 0, tzinfo=datetime.timezone.utc)
INVALID_TIMESTAMPS = ("2026-13-01T00:00:00Z", "2026-02-30T12:00:00Z", "2026-10-28T25:00:00Z")


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

    def test_timestamps_that_are_no_date_are_an_error(self):
        # Shaped like a cutoff, so they used to pass through and silently match nothing.
        for value in INVALID_TIMESTAMPS:
            with self.assertRaises(ValueError, msg=value):
                since_ts(value, NOW)

    def test_days_past_the_calendar_are_an_error(self):
        for value in ("1000000d", "99999999999d"):
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

    def call_mcp(self, name, args):
        """One tool call the way the MCP server runs it: `mnemo mcp --call NAME`."""
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(args))), contextlib.redirect_stdout(out):
            mcp_server.call_main(name, db_path=self.demo.db)
        return json.loads(out.getvalue())

    def test_cli_rejects_a_value_it_cannot_read(self):
        for value in ("3x", "1000000d") + INVALID_TIMESTAMPS:
            for argv in (["recent", "--since", value, "--no-sync", "--json"],
                         ["search", "backoff", "--since", value, "--no-sync", "--json"]):
                code, out, err = self.run_cli(*argv)
                self.assertEqual(code, 2, argv)
                self.assertEqual(out, "", argv)
                self.assertIn("Nd (e.g. 7d)", err, argv)

    def test_mcp_rejects_a_value_it_cannot_read(self):
        for value in ("3x", "1000000d") + INVALID_TIMESTAMPS:
            for name, args in (("list_recent_sessions", {"since": value}),
                               ("search_sessions", {"query": "backoff", "since": value})):
                result = self.call_mcp(name, args)
                self.assertTrue(result.get("isError"), (name, value))
                self.assertIn("Nd (e.g. 7d)", result["content"][0]["text"], (name, value))

    def test_mcp_recent_takes_days(self):
        result = mcp_server.handle_call("list_recent_sessions", {"since": "100000d"}, self.idx)
        self.assertEqual(len(json.loads(result["content"][0]["text"])), 5)
        with self.assertRaises(ValueError):
            mcp_server.handle_call("list_recent_sessions", {"since": "3x"}, self.idx)

    def test_a_timestamp_cutoff_is_inclusive_to_the_second(self):
        # The second-newest demo session starts at 2026-09-25T01:30:00Z.
        cases = (("2026-09-25T01:29:59Z", 2), ("2026-09-25T01:30:00Z", 2), ("2026-09-25T01:30:01Z", 1),
                 ("2026-09-25T09:30:00+08:00", 2), ("2026-09-25T09:30:01+08:00", 1))
        for value, n in cases:
            code, out, _ = self.run_cli("recent", "--since", value, "--no-sync", "--json")
            self.assertEqual((code, len(json.loads(out))), (0, n), value)
            result = self.call_mcp("list_recent_sessions", {"since": value})
            self.assertFalse(result.get("isError"), value)
            self.assertEqual(len(json.loads(result["content"][0]["text"])), n, value)

if __name__ == "__main__":
    unittest.main()
