"""Antigravity CLI (agy): one SQLite database per conversation, built from tests/fixtures."""
import contextlib
import io
import json
import os
import sqlite3
import stat
import subprocess
import unittest

import agy_store
from helpers import REPO, DemoHome

from test_archive import env
from test_mesh import MNEMO

from mnemo import mcp_server
from mnemo.index import Index
from mnemo.search import get_context, get_session, raw_session, recent, search
from mnemo.sources.agy import AgySource

RELAY, DOCS, EMPTY = (c["id"] for c in agy_store.conversations())


class AgyCase(unittest.TestCase):
    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.demo = DemoHome()
        self.demo.activate()
        self.paths = agy_store.write_all(self.demo.home)
        self.idx = Index(self.demo.db)
        self.stats = self.sync()

    def tearDown(self):
        self.idx.close()
        self.demo.deactivate()
        self.demo.cleanup()

    def sync(self):
        return self.idx.sync(["agy"], home=self.demo.home)

    def rows(self, sid):
        return [(r["lineno"], r["role"], r["kind"]) for r in self.idx.db.execute(
            "SELECT lineno, role, kind FROM messages WHERE session_id = ? ORDER BY rowid", (sid,))]

    def file_row(self, sid):
        return self.idx.db.execute("SELECT * FROM files WHERE session_id = ?", (sid,)).fetchone()

    def copy(self, sid):
        return os.path.join(self.idx.archive.root, "agy", agy_store.CONVERSATIONS, sid + ".db")

    def append(self, sid, idx, step, con=None):
        own = con is None
        con = con or sqlite3.connect(self.paths[sid])
        agy_store.add_step(con, idx, step, 1790000000 + 600)
        con.commit()
        if own:
            con.close()


class ParseTest(AgyCase):
    def test_indexes_every_conversation(self):
        self.assertEqual((self.stats["files_new"], self.stats["messages"]), (3, 14))
        self.assertEqual(self.idx.counts()["agy"], {"files": 3, "messages": 14})

    def test_steps_become_messages(self):
        self.assertEqual(self.rows(RELAY), [
            (0, "user", "text"),
            (1, "assistant", "reasoning"), (1, "assistant", "tool_call"),
            (2, "tool", "tool_result"),
            (3, "assistant", "text"), (3, "assistant", "tool_call"),
            (4, "tool", "tool_result"),  # a failed tool call: its error
            (5, "user", "text"),  # background task notification
            # 6 is an API error step: not indexed
            (7, "assistant", "summary"),
            (8, "user", "text"),
            (9, "assistant", "text"),
        ])
        call = self.idx.db.execute("SELECT text FROM messages WHERE session_id = ? AND lineno = 1"
                                   " AND kind = 'tool_call'", (RELAY,)).fetchone()[0]
        self.assertEqual(call, 'grep_search({"Query": "retry", "SearchPath": "/home/alex/relay"})')

    def test_session_metadata(self):
        relay = self.file_row(RELAY)
        self.assertEqual(relay["path"], self.paths[RELAY])
        self.assertEqual(relay["cwd"], "/home/alex/relay")
        self.assertEqual(relay["title"], "Webhook retries give up after three tries; add exponential backoff to the relay.")
        self.assertEqual(relay["started_ts"], "2026-09-21T14:13:20Z")
        docs = self.file_row(DOCS)
        self.assertEqual(docs["cwd"], "/home/alex/docs site")  # file URI, unquoted
        self.assertEqual(docs["title"], "Fix the flash of unstyled theme on the search page.")
        self.assertEqual(self.file_row(EMPTY)["title"], "")  # opened, never prompted

    def test_full_bodies_in_raw_reads(self):
        self.append(RELAY, 10, {"tool": {"output": "x" * 25000}})
        self.sync()
        stored = self.idx.db.execute("SELECT text FROM messages WHERE session_id = ? AND lineno = 10",
                                     (RELAY,)).fetchone()[0]
        self.assertTrue(stored.endswith("…[truncated]"))
        raw = raw_session(self.idx, self.paths[RELAY])
        self.assertEqual(len(raw["messages"][-1]["text"]), 25000)

    def test_unreadable_databases_are_skipped(self):
        folder = os.path.dirname(self.paths[RELAY])
        with open(os.path.join(folder, "garbage.db"), "wb") as f:
            f.write(b"not a database")
        undecodable = agy_store.write(self.demo.home, {"id": "undecodable", "workspace": "", "start": 1, "steps": []})
        con = sqlite3.connect(undecodable)
        con.execute("INSERT INTO steps(idx, step_type, step_payload) VALUES (0, 14, x'ffffffff')")
        con.commit()
        con.close()
        renumbered = agy_store.write(self.demo.home, {"id": "renumbered", "workspace": "", "start": 1, "steps": []})
        con = sqlite3.connect(renumbered)
        con.execute("INSERT INTO steps(idx, step_type, step_payload) VALUES (0, 900, x'')")
        con.commit()
        con.close()
        logged = []
        self.idx.sync(["agy"], home=self.demo.home, logger=logged.append)
        skipped = sorted(os.path.basename(m[len("skip "):].split(": ")[0]) for m in logged
                         if m.startswith("skip /"))
        self.assertEqual(skipped, ["garbage.db", "renumbered.db", "undecodable.db"])
        self.assertTrue(any(m.startswith("skip archiving") and "garbage.db" in m for m in logged), logged)
        self.assertEqual(self.idx.counts()["agy"]["files"], 3)
        # The other conversations are still copied and kept.
        os.remove(self.paths[DOCS])
        self.assertEqual(self.sync()["files_removed"], 0)


class SearchTest(AgyCase):
    def test_text_tools_summaries_and_cjk_are_searchable(self):
        hits = search(self.idx, "backoff", sources=["agy"])
        self.assertTrue(hits)
        self.assertEqual({h["session_id"] for h in hits}, {RELAY})
        self.assertEqual({h["kind"] for h in hits}, {"text", "tool_call", "summary"})
        self.assertTrue(all(h["account"] == ".gemini" and h["archived"] is False for h in hits))
        self.assertEqual([h["lineno"] for h in search(self.idx, '"permission check failed"', sources=["agy"])], [4])
        self.assertTrue(search(self.idx, "会话 检索", sources=["agy"]))

    def test_injected_text_is_left_out_by_default(self):
        self.assertEqual(search(self.idx, "AGENTS.md", sources=["agy"]), [])
        self.assertEqual({h["session_id"] for h in search(self.idx, "AGENTS.md", sources=["agy"],
                                                          include_injected=True)}, {DOCS})
        self.assertTrue(search(self.idx, "unstyled", sources=["agy"]))  # the prompt after it stays
        self.assertEqual(search(self.idx, "pytest", sources=["agy"]), [])
        self.assertEqual([h["lineno"] for h in search(self.idx, "pytest", sources=["agy"],
                                                      include_injected=True)], [5])

    def test_api_errors_are_not_indexed(self):
        self.assertEqual(search(self.idx, "UNAVAILABLE", include_injected=True), [])

    def test_context_session_and_recent(self):
        path = self.paths[RELAY]
        window = get_context(self.idx, path, 4, before=1, after=1)
        self.assertEqual([(m["lineno"], m["hit"]) for m in window], [(3, False), (4, True), (5, False)])
        sess = get_session(self.idx, path)
        self.assertEqual((sess["source"], sess["session_id"], sess["count"]), ("agy", RELAY, 11))
        self.assertEqual((sess["account"], sess["archived"]), (".gemini", False))
        self.assertEqual(raw_session(self.idx, path)["count"], 11)
        titles = [r["title"] for r in recent(self.idx, sources=["agy"])]
        self.assertIn("Fix the flash of unstyled theme on the search page.", titles)

    def test_mcp_tools(self):
        result = mcp_server.handle_call("search_sessions", {"query": "jitter", "source": "agy", "host": "local"},
                                        self.idx)["structuredContent"]
        self.assertTrue(result["hits"])
        self.assertTrue(all(h["source"] == "agy" for h in result["hits"]))
        rows = json.loads(mcp_server.handle_call("list_recent_sessions", {"source": "agy"},
                                                 self.idx)["content"][0]["text"])
        self.assertEqual({r["session_id"] for r in rows} & {RELAY, DOCS}, {RELAY, DOCS})
        sess = json.loads(mcp_server.handle_call("get_session", {"path": self.paths[DOCS]},
                                                 self.idx)["content"][0]["text"])
        self.assertEqual(sess["count"], 3)


class IncrementalTest(AgyCase):
    def test_unchanged_conversations_are_left_alone(self):
        stats = self.sync()
        self.assertEqual((stats["files_new"], stats["files_updated"], stats["files_archived"]), (0, 0, 0))

    def test_an_empty_write_ahead_log_changes_nothing(self):
        # agy leaves an empty <id>.db-wal beside every conversation and touches them when it starts.
        for path in self.paths.values():
            open(path + "-wal", "w").close()
            os.utime(path + "-wal", (os.stat(path).st_mtime + 60,) * 2)
        stats = self.sync()
        self.assertEqual((stats["files_updated"], stats["files_archived"]), (0, 0))

    def test_a_step_still_in_the_write_ahead_log_is_indexed(self):
        path = self.paths[RELAY]
        con = agy_store.live(path)
        try:
            before = os.stat(path)
            self.append(RELAY, 10, {"user": "also cap the retry budget at five minutes"}, con)
            after = os.stat(path)
            # agy is still running: the step is in <id>.db-wal, the .db is untouched.
            self.assertEqual((after.st_size, after.st_mtime_ns), (before.st_size, before.st_mtime_ns))
            self.assertGreater(os.path.getsize(path + "-wal"), 0)
            self.assertEqual(self.sync()["files_updated"], 1)
            self.assertEqual([h["lineno"] for h in search(self.idx, "budget", sources=["agy"])], [10])
            # The archive copy has it too, as one file.
            self.assertEqual(AgySource().parse(self.copy(RELAY))[2][-1][0], 10)
        finally:
            con.close()

    def test_new_and_deleted_conversations(self):
        conv = dict(agy_store.conversations()[1], id="5d1e7a90-3c2b-4f6e-8a4d-0b9c8e7f6a51")
        path = agy_store.write(self.demo.home, conv)
        self.assertEqual(self.sync()["files_new"], 1)
        os.remove(path)
        with env(MNEMO_ARCHIVE="off"):
            self.assertEqual(self.sync()["files_removed"], 1)
        self.assertEqual(self.idx.counts()["agy"]["files"], 3)


class ArchiveTest(AgyCase):
    def test_each_conversation_is_copied_as_one_private_file(self):
        self.assertEqual(self.stats["files_archived"], 3)
        copy = self.copy(RELAY)
        self.assertEqual(stat.S_IMODE(os.stat(copy).st_mode), 0o600)
        self.assertEqual(sorted(os.listdir(os.path.dirname(copy))), sorted(c + ".db" for c in (RELAY, DOCS, EMPTY)))
        con = sqlite3.connect("file:%s?mode=ro" % copy, uri=True)
        try:
            self.assertEqual(con.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        finally:
            con.close()

    def test_a_deleted_conversation_stays_searchable(self):
        path = self.paths[RELAY]
        os.remove(path)
        stats = self.sync()
        self.assertEqual((stats["files_removed"], stats["files_updated"]), (0, 0))  # kept, not even reparsed
        hits = search(self.idx, "jitter", sources=["agy"])
        self.assertTrue(hits)
        self.assertTrue(all(h["archived"] and h["account"] == ".gemini" for h in hits))
        sess = get_session(self.idx, path)
        self.assertEqual((sess["archived"], sess["archive_path"], sess["count"]), (True, self.copy(RELAY), 11))
        self.assertEqual(raw_session(self.idx, path)["count"], 11)
        self.assertEqual([m["lineno"] for m in get_context(self.idx, path, 4, before=1, after=0)], [3, 4])

    def test_a_changed_conversation_is_copied_again(self):
        self.append(RELAY, 10, {"user": "also cap the retry budget at five minutes"})
        self.assertEqual(self.sync()["files_archived"], 1)
        os.remove(self.paths[RELAY])
        self.sync()
        self.assertEqual([h["lineno"] for h in search(self.idx, "budget", sources=["agy"])], [10])


class CliTest(unittest.TestCase):
    def test_hits_are_labelled_agy(self):
        with contextlib.redirect_stdout(io.StringIO()):
            demo = DemoHome()
        try:
            agy_store.write_all(demo.home)
            run_env = dict(os.environ, HOME=demo.home)

            def mnemo(*argv):
                p = subprocess.run(MNEMO + list(argv), env=run_env, cwd=REPO, capture_output=True, text=True)
                self.assertEqual(p.returncode, 0, p.stderr)
                return p.stdout

            mnemo("index")
            out = mnemo("search", "jitter", "--host", "local", "--source", "agy")
            self.assertIn("[local/agy]", out)  # the default config dir: no account suffix
            self.assertIn("agy", json.loads(mnemo("status", "--json"))["sources"])
            self.assertIn("Fix the flash of unstyled theme", mnemo("recent", "--source", "agy"))
        finally:
            demo.cleanup()


if __name__ == "__main__":
    unittest.main()
