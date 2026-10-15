"""Searches leave out the calling agent's own session; context reads can be clipped."""
import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from helpers import REPO, DemoHome
from mnemo import mcp_server, remote
from mnemo.index import Index
from mnemo.search import caller_session_ids, clip_bodies, get_context, search, split_ids

MNEMO = [sys.executable, os.path.join(REPO, "bin", "mnemo")]


class CallerSessionIdsTest(unittest.TestCase):
    def test_reads_claude_and_codex_variables(self):
        env = {"CLAUDE_CODE_SESSION_ID": "c1", "CODEX_THREAD_ID": "t1", "CODEX_SESSION_ID": "s1", "OTHER": "x"}
        self.assertEqual(caller_session_ids(env), ["c1", "t1", "s1"])

    def test_reads_codex_mcp_meta_and_drops_duplicates(self):
        # A Codex root thread's threadId and sessionId are the same id.
        meta = {"threadId": "t1", "sessionId": "t1", "progressToken": 7}
        self.assertEqual(caller_session_ids({}, meta), ["t1"])

    def test_nothing_set_means_nothing_excluded(self):
        self.assertEqual(caller_session_ids({}, None), [])
        self.assertEqual(caller_session_ids({"CODEX_THREAD_ID": ""}, {"threadId": 3}), [])

    def test_split_ids_accepts_repeats_and_commas(self):
        self.assertEqual(split_ids(["a,b", " b ", "", "c"]), ["a", "b", "c"])
        self.assertEqual(split_ids(None), [])


class ExcludeSessionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with contextlib.redirect_stdout(io.StringIO()):
            cls.demo = DemoHome()
        cls.demo.activate()
        cls.index = Index(cls.demo.db)
        cls.index.sync(home=cls.demo.home)
        hits = search(cls.index, "backoff", limit=100)
        cls.sessions = sorted({h["session_id"] for h in hits})
        cls.hit = hits[0]

    @classmethod
    def tearDownClass(cls):
        cls.index.close()
        cls.demo.deactivate()
        cls.demo.cleanup()

    def mcp_search(self, args, env=None):
        with patch.dict(os.environ, env or {}):
            value = mcp_server.handle_call("search_sessions", dict(args, query="backoff", host="local"), self.index)
        return value["structuredContent"]

    def test_demo_has_several_matching_sessions(self):
        self.assertGreaterEqual(len(self.sessions), 2)

    def test_search_drops_excluded_sessions(self):
        mine = self.sessions[0]
        hits = search(self.index, "backoff", limit=100, exclude_sessions=[mine])
        self.assertTrue(hits)
        self.assertNotIn(mine, {h["session_id"] for h in hits})

    def test_mcp_leaves_out_the_claude_session_calling_it(self):
        mine = self.sessions[0]
        data = self.mcp_search({}, {"CLAUDE_CODE_SESSION_ID": mine})
        self.assertEqual(data["excluded_sessions"], [mine])
        self.assertNotIn(mine, {h["session_id"] for h in data["hits"]})

    def test_mcp_include_current_session_keeps_it(self):
        mine = self.sessions[0]
        data = self.mcp_search({"include_current_session": True}, {"CLAUDE_CODE_SESSION_ID": mine})
        self.assertEqual(data["excluded_sessions"], [])
        self.assertIn(mine, {h["session_id"] for h in data["hits"]})

    def test_mcp_reads_codex_thread_from_request_meta(self):
        mine = self.sessions[-1]
        data = self.mcp_search({"_meta": {"threadId": mine, "sessionId": mine}})
        self.assertEqual(data["excluded_sessions"], [mine])
        self.assertNotIn(mine, {h["session_id"] for h in data["hits"]})

    def test_mcp_explicit_ids_stay_excluded_with_include_current(self):
        data = self.mcp_search({"exclude_session_ids": self.sessions[:1], "include_current_session": True},
                               {"CLAUDE_CODE_SESSION_ID": self.sessions[-1]})
        self.assertEqual(data["excluded_sessions"], self.sessions[:1])
        found = {h["session_id"] for h in data["hits"]}
        self.assertNotIn(self.sessions[0], found)
        self.assertIn(self.sessions[-1], found)

    def test_tools_call_meta_reaches_the_tool_process(self):
        params = {"name": "search_sessions", "arguments": {"query": "x"}, "_meta": {"threadId": "t1"}}
        self.assertEqual(mcp_server.call_arguments(params), {"query": "x", "_meta": {"threadId": "t1"}})
        self.assertEqual(mcp_server.call_arguments({"arguments": {"query": "x"}}), {"query": "x"})

    def test_search_schema_lists_the_new_arguments(self):
        tool = {t["name"]: t for t in mcp_server.build_tools()}["search_sessions"]
        props = tool["inputSchema"]["properties"]
        self.assertEqual(props["exclude_session_ids"]["type"], "array")
        self.assertEqual(props["include_current_session"]["type"], "boolean")

    def test_neighbor_rows_from_excluded_sessions_are_dropped(self):
        mine = self.sessions[0]
        rows = [dict(self.hit, host="devbox", session_id=mine, path="/n/a.jsonl"),
                dict(self.hit, host="devbox", session_id="other", path="/n/b.jsonl")]
        neighbor = {"name": "devbox", "node_id": "n1"}
        with patch.object(remote, "load_remotes", return_value=[neighbor]), \
                patch.object(remote, "_remote_search", return_value=(rows, [])):
            hits, _ = remote.fan_out_search(self.index, "backoff", hosts=["devbox"], sync_local=False,
                                            exclude_sessions=[mine])
        self.assertEqual([h["session_id"] for h in hits], ["other"])

    def run_cli(self, *argv, env=None):
        p = subprocess.run(MNEMO + ["--db", self.demo.db] + list(argv) + ["--json", "--host", "local", "--no-sync"],
                           env=dict(os.environ, HOME=self.demo.home, **(env or {})),
                           cwd=REPO, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p

    def test_cli_leaves_out_current_session_unless_asked(self):
        mine = self.sessions[0]
        env = {"CODEX_THREAD_ID": mine}
        found = {h["session_id"] for h in json.loads(self.run_cli("search", "backoff", env=env).stdout)}
        self.assertNotIn(mine, found)
        kept = json.loads(self.run_cli("search", "backoff", "--include-current", env=env).stdout)
        self.assertIn(mine, {h["session_id"] for h in kept})

    def test_cli_exclude_session_flag_and_coverage_report(self):
        out = json.loads(self.run_cli("search", "backoff", "--coverage",
                                      "--exclude-session", ",".join(self.sessions[:2])).stdout)
        self.assertEqual(out["excluded_sessions"], self.sessions[:2])
        self.assertFalse({h["session_id"] for h in out["hits"]} & set(self.sessions[:2]))

    def test_search_hits_carry_a_snippet_not_the_body(self):
        data = self.mcp_search({"include_current_session": True})
        self.assertTrue(data["hits"])
        for h in data["hits"]:
            self.assertNotIn("text", h)
            self.assertNotIn("body", h)
            self.assertLess(len(h["snippet"]), 400)

    def test_context_max_chars_clips_every_message(self):
        rows = get_context(self.index, self.hit["path"], self.hit["lineno"], before=4, after=8)
        longest = max(len(r["text"]) for r in rows)
        self.assertGreater(longest, 40)
        clipped = clip_bodies(get_context(self.index, self.hit["path"], self.hit["lineno"], 4, 8), 40)
        for r in clipped:
            head = r["text"].split(" … [+", 1)[0]
            self.assertLessEqual(len(head), 40)
        self.assertTrue(any(r.get("clipped") for r in clipped))
        self.assertTrue(any("[+%d chars]" % (longest - 40) in r["text"] for r in clipped))

    def test_context_without_max_chars_is_unchanged(self):
        rows = get_context(self.index, self.hit["path"], self.hit["lineno"])
        self.assertEqual(clip_bodies(rows, None), rows)
        self.assertFalse(any("clipped" in r for r in rows))

    def test_mcp_and_cli_context_accept_max_chars(self):
        value = mcp_server.handle_call("get_context", {"path": self.hit["path"], "line": self.hit["lineno"],
                                                       "max_chars": 40}, self.index)
        self.assertTrue(any(r.get("clipped") for r in json.loads(value["content"][0]["text"])))
        p = subprocess.run(MNEMO + ["--db", self.demo.db, "context", self.hit["path"], str(self.hit["lineno"]),
                                    "--json", "--max-chars", "40"],
                           env=dict(os.environ, HOME=self.demo.home), cwd=REPO, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(any(r.get("clipped") for r in json.loads(p.stdout)))


if __name__ == "__main__":
    unittest.main()
