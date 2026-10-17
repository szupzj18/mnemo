"""Context reads clip each message by default; full bodies are one flag away."""
import contextlib
import io
import json
import os
import subprocess
import sys
import unittest

from helpers import REPO, DemoHome
from mnemo import mcp_server
from mnemo.index import Index
from mnemo.search import CONTEXT_MAX_CHARS, clip_bodies, context_max_chars, get_context, search

MNEMO = [sys.executable, os.path.join(REPO, "bin", "mnemo")]
LONG = "backoff " + "x" * 5000  # a 5008-character tool result


class ClipRulesTest(unittest.TestCase):
    def test_default_and_overrides(self):
        self.assertEqual(context_max_chars(), CONTEXT_MAX_CHARS)
        self.assertIsNone(context_max_chars(full=True))
        self.assertIsNone(context_max_chars(raw=True))  # --raw asks for untruncated bodies
        self.assertEqual(context_max_chars(500, full=True), 500)  # an explicit length wins
        self.assertIsNone(context_max_chars(0))

    def test_marker_names_full_length_and_how_to_get_it(self):
        rows = clip_bodies([{"text": LONG, "body": "<env>" + LONG}, {"text": "short"}], 2000, hint="--full")
        clipped, short = rows
        self.assertTrue(clipped["text"].startswith(LONG[:2000] + " … [clipped: 2000 of 5008 chars; --full"))
        self.assertEqual((clipped["clipped"], clipped["chars"], clipped["body_chars"]), (True, 5008, 5013))
        self.assertEqual(short, {"text": "short"})


class ContextDefaultClipTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with contextlib.redirect_stdout(io.StringIO()):
            cls.demo = DemoHome()
        cls.demo.activate()
        cls.index = Index(cls.demo.db)
        cls.index.sync(home=cls.demo.home)
        cls.hit = search(cls.index, "backoff", limit=1)[0]
        # Stand in for a long tool result: the demo sessions are all short.
        cls.index.db.execute("UPDATE messages SET text = ?, body = ? WHERE path = ? AND lineno = ?",
                             (LONG, LONG, cls.hit["path"], cls.hit["lineno"]))
        cls.index.db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.index.close()
        cls.demo.deactivate()
        cls.demo.cleanup()

    def mcp_context(self, **args):
        value = mcp_server.handle_call("get_context", dict(path=self.hit["path"], line=self.hit["lineno"], **args),
                                       self.index)
        return json.loads(value["content"][0]["text"])

    def cli_context(self, *flags):
        p = subprocess.run(MNEMO + ["--db", self.demo.db, "context", self.hit["path"], str(self.hit["lineno"])]
                           + list(flags), env=dict(os.environ, HOME=self.demo.home), cwd=REPO,
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def long_row(self, rows):
        return next(r for r in rows if r["text"].startswith("backoff x"))

    def test_mcp_clips_by_default(self):
        row = self.long_row(self.mcp_context())
        self.assertEqual(row["chars"], len(LONG))
        self.assertTrue(row["clipped"])
        self.assertIn("[clipped: %d of %d chars; full=true" % (CONTEXT_MAX_CHARS, len(LONG)), row["text"])
        self.assertLess(len(row["text"]), CONTEXT_MAX_CHARS + 100)

    def test_mcp_full_and_raw_return_whole_bodies(self):
        row = self.long_row(self.mcp_context(full=True))
        self.assertEqual(row["text"], LONG)
        self.assertNotIn("clipped", row)
        self.assertEqual(self.long_row(self.mcp_context(max_chars=0))["text"], LONG)

    def test_mcp_max_chars_still_sets_the_length(self):
        row = self.long_row(self.mcp_context(max_chars=40, full=True))
        self.assertTrue(row["text"].startswith(LONG[:40] + " … [clipped: 40 of"))

    def test_cli_json_clips_by_default_and_full_turns_it_off(self):
        row = self.long_row(json.loads(self.cli_context("--json")))
        self.assertIn("[clipped: %d of %d chars; --full" % (CONTEXT_MAX_CHARS, len(LONG)), row["text"])
        self.assertEqual(row["chars"], len(LONG))
        self.assertEqual(self.long_row(json.loads(self.cli_context("--json", "--full")))["text"], LONG)

    def test_cli_text_output_says_how_to_get_the_rest(self):
        out = self.cli_context()
        self.assertIn("[clipped: %d of %d chars; --full for the whole message]" % (CONTEXT_MAX_CHARS, len(LONG)), out)
        self.assertIn(LONG, self.cli_context("--full").replace("\n    ", ""))

    def test_relayed_reads_leave_clipping_to_the_requesting_device(self):
        row = self.long_row(json.loads(self.cli_context("--json", "--relay")))
        self.assertEqual(row["text"], LONG)

    def test_short_messages_are_untouched(self):
        rows = self.mcp_context()
        self.assertEqual([r for r in rows if not r["text"].startswith("backoff x")],
                         [r for r in get_context(self.index, self.hit["path"], self.hit["lineno"])
                          if not r["text"].startswith("backoff x")])


if __name__ == "__main__":
    unittest.main()
