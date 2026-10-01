"""Synthetic regression cases for Codex representations and bounded retrieval."""
import contextlib
import glob
import io
import json
import os
import shutil
import subprocess
import unittest
from unittest.mock import patch

from helpers import DemoHome
from mnemo import mcp_server, remote
from mnemo.index import Index
from mnemo.search import get_session, raw_session, search
from mnemo.sources.codex import CodexSource


def write_log(path, records):
    with open(path, "w") as stream:
        for payload in records:
            stream.write(json.dumps(dict(timestamp="2026-10-01T00:00:00Z", **payload)) + "\n")


def response(text, role="assistant", **extra):
    return {"type": "response_item", "payload": dict(type="message", role=role,
            content=[{"type": "text", "text": text}], **extra)}


def completed(item, turn="turn1"):
    return {"type": "event_msg", "payload": {"type": "item_completed", "turn_id": turn, "item": item}}


class RetrievalTest(unittest.TestCase):
    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.demo = DemoHome()
        self.demo.activate()
        self.addCleanup(self.demo.cleanup)
        self.addCleanup(self.demo.deactivate)
        self.index = Index(self.demo.db)
        self.addCleanup(self.index.close)
        self.path = self.demo.path(".codex", "sessions", "paging.jsonl")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.records = [{"type": "session_meta", "payload": {"id": "paging", "cwd": "/synthetic"}}]
        self.records += [response("message %d 退避" % i, "user" if i % 2 == 0 else "assistant") for i in range(60)]
        write_log(self.path, self.records)
        self.index.sync(home=self.demo.home)

    def test_pages_reconstruct_session_and_support_backwards_and_anchor(self):
        whole = get_session(self.index, self.path)
        page = get_session(self.index, self.path, limit=7)
        result = []
        while True:
            result.extend(page["messages"])
            if not page["page"]["next_cursor"]:
                break
            page = get_session(self.index, self.path, limit=7, cursor=page["page"]["next_cursor"])
        self.assertEqual(result, whole["messages"])
        previous = get_session(self.index, self.path, limit=7, cursor=page["page"]["previous_cursor"])
        self.assertEqual(previous["messages"], whole["messages"][49:56])
        centered = get_session(self.index, self.path, limit=7, anchor_line=32)
        self.assertIn(32, [m["lineno"] for m in centered["messages"]])
        for kwargs, expected in (({"head": 3}, whole["messages"][:3]), ({"tail": 3}, whole["messages"][-3:])):
            value = get_session(self.index, self.path, **kwargs)
            self.assertEqual(value["messages"], expected)
            self.assertEqual(value["count"], 60)

    def test_cursor_rejects_other_session_and_reindexed_file(self):
        cursor = get_session(self.index, self.path, limit=7)["page"]["next_cursor"]
        other = next(p for p in glob.glob(self.demo.path(".codex", "sessions", "**", "*.jsonl"), recursive=True) if p != self.path)
        with self.assertRaisesRegex(ValueError, "changed"):
            get_session(self.index, other, limit=7, cursor=cursor)
        write_log(self.path, self.records + [response("appended")])
        self.index.sync(home=self.demo.home)
        with self.assertRaisesRegex(ValueError, "changed"):
            get_session(self.index, self.path, limit=7, cursor=cursor)

    def test_invalid_selections_are_rejected(self):
        for kwargs in ({"limit": 0}, {"limit": 501}, {"head": -1}, {"head": 1, "tail": 1},
                       {"limit": 3, "head": 1}, {"cursor": "bad"}, {"cursor": "bad", "limit": 3},
                       {"anchor_line": 2}, {"anchor_line": 2, "limit": 3, "cursor": "bad"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                get_session(self.index, self.path, **kwargs)

    def test_normalized_head_reads_only_selected_bodies(self):
        # SQL must restrict the rowid range, not fetch all bodies then slice.
        queries = []
        self.index.db.set_trace_callback(queries.append)
        get_session(self.index, self.path, tail=3)
        self.index.db.set_trace_callback(None)
        lo, hi = self.index.db.execute("SELECT lo,hi FROM file_ranges WHERE path=?", (self.path,)).fetchone()
        reads = [q for q in queries if "SELECT lineno, ts, role, kind, body" in q]
        self.assertEqual(len(reads), 1)
        self.assertIn("BETWEEN %d AND %d" % (hi - 2, hi), reads[0])

    def test_raw_pages_keep_full_bodies_and_original_line_anchors(self):
        write_log(self.path, self.records + [response("x" * 22000 + " tailneedle")])
        self.index.sync(home=self.demo.home)
        self.assertEqual(search(self.index, "tailneedle"), [])
        whole = raw_session(self.index, self.path)
        page = raw_session(self.index, self.path, limit=5, anchor_line=62)
        self.assertEqual(page["messages"], whole["messages"][page["page"]["offset"]:])
        self.assertIn("tailneedle", page["messages"][-1]["text"])

    def test_mcp_reports_coverage_and_page_schema(self):
        value = mcp_server.handle_call("search_sessions", {"query": "退避", "host": "local"}, self.index)
        data = json.loads(value["content"][0]["text"])
        self.assertEqual(data, value["structuredContent"])
        self.assertTrue(data["hits"])
        self.assertEqual(data["coverage"][0]["status"], "searched")
        self.assertEqual(data["coverage"][0]["body_char_limit"], 20000)
        page = mcp_server.handle_call("get_session", {"path": self.path, "limit": 3}, self.index)
        self.assertEqual(len(json.loads(page["content"][0]["text"])["messages"]), 3)

    def test_codex_new_events_deduplicate_mirrors_but_keep_repetition(self):
        events = [
            {"type": "session_meta", "payload": {"id": "new", "cwd": "/synthetic"}},
            {"type": "event_msg", "payload": {"type": "turn_started", "turn_id": "turn1"}},
            response("same", id="message1"),
            completed({"type": "AgentMessage", "id": "message1", "content": [{"type": "Text", "text": "same"}]}),
            completed({"type": "AgentMessage", "id": "message2", "content": [{"type": "Text", "text": "same"}]}),
            completed({"type": "UserMessage", "id": "user1", "content": [{"type": "text", "text": "<environment_context>noise</environment_context>\nnewuserneedle"}]}),
            completed({"type": "Plan", "id": "plan1", "text": "planneedle"}),
            completed({"type": "CommandExecution", "id": "command1", "command": ["echo", "hello"], "aggregated_output": "outputneedle"}),
            completed({"type": "FunctionCallOutput", "id": "output1", "output": [{"type": "input_text", "text": "functionneedle"}]}),
            {"type": "response_item", "payload": {"type": "agent_message", "content": [{"type": "input_text", "text": "interagentneedle"}]}},
        ]
        write_log(self.path, events)
        self.index.sync(home=self.demo.home)
        parsed = CodexSource(home=self.demo.home).parse(self.path)[2]
        self.assertEqual([m.text for _, m in parsed].count("same"), 2)
        for word in ("newuserneedle", "planneedle", "outputneedle", "functionneedle", "interagentneedle"):
            self.assertEqual(len(search(self.index, word)), 1, word)
        self.assertEqual(search(self.index, "noise"), [])

    @unittest.skipUnless(shutil.which("zstd"), "optional zstd executable not installed")
    def test_compressed_transition_keeps_logical_path_context_and_no_duplicates(self):
        subprocess.run(["zstd", "-q", "-f", self.path, "-o", self.path + ".zst"], check=True)
        self.index.sync(home=self.demo.home)  # plain sibling wins
        before = get_session(self.index, self.path)
        os.remove(self.path)
        self.index.sync(home=self.demo.home)
        self.assertEqual(get_session(self.index, self.path), before)
        self.assertEqual(raw_session(self.index, self.path)["messages"], before["messages"])
        self.assertEqual(len([p for p in CodexSource(self.demo.home).files() if "paging" in p]), 1)
        self.index.db.execute("DELETE FROM meta WHERE key=?", ("adapter:codex:" + self.path,))
        with patch("mnemo.sources.base.shutil.which", return_value=None):
            self.index.sync(home=self.demo.home)
        self.assertEqual(get_session(self.index, self.path), before)
        coverage = []
        hits, warnings = remote.fan_out_search(self.index, "退避", hosts=["local"], sync_local=False, coverage=coverage)
        self.assertTrue(hits)
        self.assertTrue(warnings)
        self.assertIn("zstd", coverage[0]["index_warnings"][0])

    def test_missing_decoder_skips_only_compressed_file_and_keeps_old_rows(self):
        before = get_session(self.index, self.path)
        os.remove(self.path)
        with open(self.path + ".zst", "wb") as stream:
            stream.write(b"synthetic compressed representation; decoder unavailable")
        plain = self.demo.path(".codex", "sessions", "new-plain.jsonl")
        write_log(plain, [response("plainpelicanneedle")])
        with patch("mnemo.sources.base.shutil.which", return_value=None):
            self.index.sync(home=self.demo.home)
        self.assertEqual(len(search(self.index, "plainpelicanneedle")), 1)
        self.assertEqual(get_session(self.index, self.path), before)
        coverage = []
        remote.fan_out_search(self.index, "plainpelicanneedle", hosts=["local"], coverage=coverage)
        self.assertEqual(len(coverage[0]["index_warnings"]), 1)
        self.assertIn(self.path, coverage[0]["index_warnings"][0])

    def test_paging_rejection_preserves_relay_protocol(self):
        remote.save_remotes([{"name": "old", "host": "local:old", "proto": 2}])
        for flag, kwargs in (("--limit", {"limit": 10}),
                             ("--cursor", {"limit": 10, "cursor": "opaque"}),
                             ("--anchor-line", {"limit": 10, "anchor_line": 10})):
            with self.subTest(flag=flag), patch("mnemo.remote.remote_exec", side_effect=remote.RemoteError(
                    "old: mnemo: error: unrecognized arguments: " + flag + " 10")) as execute:
                with self.assertRaisesRegex(remote.RemoteError, "without paged reads"):
                    remote.remote_session("old", "/synthetic", **kwargs)
                self.assertEqual(remote.get_remote("old")["proto"], 2)
                self.assertEqual(execute.call_count, 1)

    def test_only_relay_rejection_downgrades_an_old_peer(self):
        remote.save_remotes([{"name": "old", "host": "local:old", "proto": 2}])
        with patch("mnemo.remote.remote_exec", side_effect=[remote.RemoteError(
                "old: mnemo: error: unrecognized arguments: --relay"), "{\"count\": 1}"]) as execute:
            self.assertEqual(remote.remote_session("old", "/synthetic"), {"count": 1})
            self.assertEqual(remote.get_remote("old")["proto"], 1)
            self.assertEqual(execute.call_count, 2)
            self.assertNotIn("--relay", execute.call_args[0][1])

    def test_adapter_upgrade_reparses_unchanged_codex_files(self):
        self.index.db.execute("DELETE FROM meta WHERE key=?", ("adapter:codex:" + self.path,))
        self.index.db.execute("UPDATE messages SET text='outdated' WHERE path=?", (self.path,))
        self.index.sync(home=self.demo.home)
        self.assertTrue(search(self.index, "退避"))

    def test_older_writer_signature_is_repaired_after_an_append(self):
        write_log(self.path, self.records + [response("newformatneedle")])
        stat = os.stat(self.path)
        # An older writer updated files/ranges without updating adapter metadata.
        self.index.db.execute("UPDATE files SET mtime=?,size=? WHERE path=?", (stat.st_mtime, stat.st_size, self.path))
        self.index.db.execute("UPDATE messages SET text='oldwriter' WHERE path=?", (self.path,))
        self.assertIn(self.path, self.index.incomplete_paths())
        self.index.sync(home=self.demo.home)
        self.assertEqual(len(search(self.index, "newformatneedle")), 1)
        self.assertNotIn(self.path, self.index.incomplete_paths())

    def test_failed_device_is_structured_not_zero_hit_success(self):
        remote.save_remotes([{"name": "offline", "host": "local:offline", "transport": "local", "home": self.demo.path("absent")}])
        with patch("mnemo.remote._remote_search", side_effect=remote.RemoteError("offline: synthetic failure")):
            coverage = []
            hits, warnings = remote.fan_out_search(self.index, "missingneedle", coverage=coverage)
        self.assertEqual(hits, [])
        self.assertTrue(warnings)
        self.assertEqual({r["host"]: r["status"] for r in coverage}, {"local": "searched", "offline": "failed"})


if __name__ == "__main__":
    unittest.main()
