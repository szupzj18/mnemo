"""Grok CLI: one updates.jsonl per session folder, built from tests/fixtures."""
import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import unittest

import grok_store
from helpers import REPO, DemoHome

from test_archive import env
from test_mesh import MNEMO

from mnemo import mcp_server
from mnemo.index import Index
from mnemo.search import get_context, get_session, raw_session, recent, search
from mnemo.sources.grok import GrokSource

RELAY, DOCS, EMPTY = (s["id"] for s in grok_store.sessions())
SESSIONS = {s["id"]: s for s in grok_store.sessions()}


class GrokCase(unittest.TestCase):
    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.demo = DemoHome()
        self.demo.activate()
        self.paths = grok_store.write_all(self.demo.home)
        self.idx = Index(self.demo.db)
        self.stats = self.sync()

    def tearDown(self):
        self.idx.close()
        self.demo.deactivate()
        self.demo.cleanup()

    def sync(self, logger=None):
        return self.idx.sync(["grok"], home=self.demo.home, logger=logger)

    def rows(self, sid):
        return [(r["lineno"], r["role"], r["kind"]) for r in self.idx.db.execute(
            "SELECT lineno, role, kind FROM messages WHERE session_id = ? ORDER BY rowid", (sid,))]

    def text(self, sid, lineno):
        return self.idx.db.execute("SELECT text FROM messages WHERE session_id = ? AND lineno = ?",
                                   (sid, lineno)).fetchone()[0]

    def file_row(self, sid):
        return self.idx.db.execute("SELECT * FROM files WHERE session_id = ?", (sid,)).fetchone()

    def copy(self, sid):
        rel = os.path.relpath(self.paths[sid], self.demo.path(".grok"))
        return os.path.join(self.idx.archive.root, "grok", ".grok", rel)

    def append(self, sid, update, prompt=None):
        grok_store.append(self.paths[sid], SESSIONS[sid], update, prompt=prompt)


class ParseTest(GrokCase):
    def test_indexes_every_session(self):
        self.assertEqual((self.stats["files_new"], self.stats["messages"]), (3, 22))
        self.assertEqual(self.idx.counts()["grok"], {"files": 3, "messages": 22})

    def test_updates_become_messages(self):
        self.assertEqual(self.rows(RELAY), [
            # 1 is a hook run: not indexed
            (2, "user", "text"),
            (3, "assistant", "reasoning"),
            (4, "assistant", "text"),
            (5, "assistant", "tool_call"),  # 6 restates its input: no second call
            (7, "tool", "tool_result"),
            (8, "assistant", "tool_call"), (9, "tool", "tool_result"),
            (10, "assistant", "tool_call"), (11, "tool", "tool_result"),  # a failed call: its error
            (12, "assistant", "tool_call"), (13, "tool", "tool_result"),  # a search the server ran
            (14, "assistant", "text"),  # two chunks of one reply (14, 15); 16 ends the turn
            (17, "user", "text"),
            (18, "assistant", "text"),
            # 19 is a kind grok 1.0.62 never wrote, 21 an image
            (20, "assistant", "text"),
            (22, "assistant", "summary"),
            (23, "user", "text"),
        ])
        self.assertEqual(self.text(RELAY, 5),
                         'run_terminal_command({"command": "rg -n retry relay/", "description": "Find the retry loop"})')
        self.assertEqual(self.text(RELAY, 7), "relay/webhook.py:41:    for attempt in range(3):\nexit 0")
        # grep shows "Found 1 match"; its output is logged as bytes.
        self.assertEqual(self.text(RELAY, 9), "relay/util.py:7:def backoff(attempt, cap=30):\n")
        self.assertEqual(self.text(RELAY, 11), "Directory not found: /home/alex/relay/docs")
        self.assertTrue(self.text(RELAY, 12).startswith("WebSearch("))
        self.assertIn('"query": "exponential backoff full jitter"', self.text(RELAY, 13))
        self.assertEqual(self.text(RELAY, 14), "I'll switch the loop to exponential backoff with full jitter, capped at 30 s.")
        self.assertEqual(self.text(RELAY, 20), "Retries now back off 2 s, 4 s, 8 s, up to 30 s.")

    def test_session_metadata(self):
        relay = self.file_row(RELAY)
        self.assertEqual(relay["path"], self.paths[RELAY])
        self.assertEqual(relay["cwd"], "/home/alex/relay")
        self.assertEqual(relay["title"], "Webhook retries give up after three tries; add exponential backoff to the relay.")
        self.assertEqual(relay["started_ts"], "2026-09-21T14:13:20Z")
        docs = self.file_row(DOCS)
        self.assertEqual(docs["cwd"], "/home/alex/docs site")  # the folder name, unquoted
        self.assertEqual(docs["started_ts"], "2026-09-22T15:13:20Z")  # the line's own timestamp
        self.assertEqual(self.file_row(EMPTY)["title"], "")  # opened, never prompted
        ts = [r[0] for r in self.idx.db.execute("SELECT ts FROM messages WHERE session_id = ? ORDER BY rowid", (RELAY,))]
        self.assertEqual((ts[0], ts[-1]), ("2026-09-21T14:13:20Z", "2026-09-21T14:15:20Z"))

    def test_a_long_working_directory_comes_from_the_cwd_file(self):
        group = os.path.join(self.demo.home, ".grok", "sessions", "home-alex-very-long-a1b2c3d4")
        path = os.path.join(group, "0199a3c7-0000-7000-8000-000000000001", "updates.jsonl")
        os.makedirs(os.path.dirname(path))
        shutil.copy(self.paths[DOCS], path)
        with open(os.path.join(group, ".cwd"), "w") as f:
            f.write("/home/alex/" + "very-long/" * 30 + "\n")
        self.assertEqual(GrokSource().parse(path)[1], "/home/alex/" + "very-long/" * 30)

    def test_what_was_typed_is_searchable_text(self):
        # Ahead of a scheduled task's prompt grok puts a reminder; displayText is the prompt.
        user = self.idx.db.execute("SELECT text, body, envelope FROM messages WHERE session_id = ? AND lineno = 23",
                                   (RELAY,)).fetchone()
        self.assertEqual(user["text"], "Re-run the relay tests and report failures.")
        self.assertTrue(user["body"].startswith("<system-reminder>"))
        self.assertEqual(user["envelope"], 1)
        # A prefix in no known envelope still goes when displayText says what was typed.
        self.append(DOCS, {"sessionUpdate": "user_message_chunk", "content": {
            "type": "text", "text": "[from the mobile app]\nAlso check the dark theme.",
            "_meta": {"displayText": "Also check the dark theme."}}})
        self.sync()
        last = self.idx.db.execute("SELECT text, body FROM messages WHERE session_id = ? ORDER BY rowid DESC LIMIT 1",
                                   (DOCS,)).fetchone()
        self.assertEqual((last["text"], last["body"]), ("Also check the dark theme.",
                                                        "[from the mobile app]\nAlso check the dark theme."))

    def test_full_bodies_in_raw_reads(self):
        self.append(RELAY, {"sessionUpdate": "tool_call_update", "toolCallId": "call-9", "status": "completed",
                            "rawOutput": {"type": "Bash", "output_for_prompt": "x" * 25000}})
        self.sync()
        stored = self.idx.db.execute("SELECT text FROM messages WHERE session_id = ? AND kind = 'tool_result'"
                                     " ORDER BY rowid DESC LIMIT 1", (RELAY,)).fetchone()[0]
        self.assertTrue(stored.endswith("…[truncated]"))
        raw = raw_session(self.idx, self.paths[RELAY])
        self.assertEqual(len(raw["messages"][-1]["text"]), 25000)

    def test_only_session_logs_are_read_and_another_format_is_reported(self):
        sessions = self.demo.path(".grok", "sessions")
        # Folders and files beside the logs that are not sessions.
        nested = os.path.join(os.path.dirname(self.paths[RELAY]), "compaction_checkpoints", "c1", "updates.jsonl")
        os.makedirs(os.path.dirname(nested))
        shutil.copy(self.paths[RELAY], nested)
        os.makedirs(os.path.join(sessions, "%2Fhome%2Falex", "0199a3c8-0000-7000-8000-000000000002"))
        # A log in a format grok might move to: JSON, but no update mnemo knows.
        moved = os.path.join(sessions, "%2Fhome%2Falex", "0199a3c9-0000-7000-8000-000000000003", "updates.jsonl")
        os.makedirs(os.path.dirname(moved))
        with open(moved, "w") as f:
            f.write(json.dumps({"params": {"update": {"sessionUpdate": "message_v2", "text": "hi"}}}) + "\n")
        # Not JSON at all: nothing to index, nothing to report.
        garbage = os.path.join(sessions, "%2Fhome%2Falex", "0199a3ca-0000-7000-8000-000000000004", "updates.jsonl")
        os.makedirs(os.path.dirname(garbage))
        with open(garbage, "wb") as f:
            f.write(b"\x00\xff not json\n")
        logged = []
        self.sync(logged.append)
        self.assertEqual([m for m in logged if m.startswith("skip ")],
                         ["skip %s: no known session updates (grok changed its log format?)" % moved])
        self.assertEqual(sorted(r[0] for r in self.idx.db.execute("SELECT path FROM files WHERE source = 'grok'")),
                         sorted(list(self.paths.values()) + [garbage]))


class SearchTest(GrokCase):
    def test_text_tools_summaries_and_cjk_are_searchable(self):
        hits = search(self.idx, "backoff", sources=["grok"])
        self.assertEqual({h["session_id"] for h in hits}, {RELAY})
        self.assertEqual({h["kind"] for h in hits}, {"text", "tool_call", "tool_result", "summary"})
        self.assertTrue(all(h["account"] == ".grok" and h["archived"] is False for h in hits))
        self.assertEqual([h["lineno"] for h in search(self.idx, '"Directory not found"', sources=["grok"])], [11])
        self.assertTrue(search(self.idx, "会话 检索", sources=["grok"]))
        self.assertEqual(search(self.idx, '"usual fix"', sources=["grok"]), [])  # reasoning: not by default

    def test_injected_text_is_left_out_by_default(self):
        self.assertEqual(search(self.idx, "confirmation", sources=["grok"]), [])
        self.assertEqual([h["lineno"] for h in search(self.idx, "confirmation", sources=["grok"],
                                                      include_injected=True)], [23])
        self.assertEqual([h["lineno"] for h in search(self.idx, '"relay tests"', sources=["grok"])], [23])

    def test_context_session_and_recent(self):
        path = self.paths[RELAY]
        window = get_context(self.idx, path, 11, before=1, after=1)
        self.assertEqual([(m["lineno"], m["hit"]) for m in window], [(10, False), (11, True), (12, False)])
        sess = get_session(self.idx, path)
        self.assertEqual((sess["source"], sess["session_id"], sess["count"]), ("grok", RELAY, 17))
        self.assertEqual((sess["account"], sess["archived"]), (".grok", False))
        self.assertEqual(raw_session(self.idx, path)["count"], 17)
        titles = [r["title"] for r in recent(self.idx, sources=["grok"])]
        self.assertIn("Fix the flash of unstyled theme on the search page.", titles)

    def test_mcp_tools(self):
        result = mcp_server.handle_call("search_sessions", {"query": "jitter", "source": "grok", "host": "local"},
                                        self.idx)["structuredContent"]
        self.assertTrue(result["hits"])
        self.assertTrue(all(h["source"] == "grok" for h in result["hits"]))
        rows = json.loads(mcp_server.handle_call("list_recent_sessions", {"source": "grok"},
                                                 self.idx)["content"][0]["text"])
        self.assertEqual({r["session_id"] for r in rows} & {RELAY, DOCS}, {RELAY, DOCS})
        sess = json.loads(mcp_server.handle_call("get_session", {"path": self.paths[DOCS]},
                                                 self.idx)["content"][0]["text"])
        self.assertEqual(sess["count"], 5)


class IncrementalTest(GrokCase):
    def test_unchanged_sessions_are_left_alone(self):
        stats = self.sync()
        self.assertEqual((stats["files_new"], stats["files_updated"], stats["files_archived"]), (0, 0, 0))

    def test_a_session_being_written(self):
        path = self.paths[DOCS]
        whole = grok_store.line(SESSIONS[DOCS], {"at": 600, "update": {
            "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Preloading the theme fixed it."}}})
        # grok is halfway through appending a line.
        with open(path, "a") as f:
            f.write(whole[:40])
        self.assertEqual(self.sync()["files_updated"], 1)
        self.assertEqual(self.idx.counts()["grok"]["messages"], 22)
        self.assertEqual(search(self.idx, "preloading", sources=["grok"]), [])
        with open(path, "a") as f:
            f.write(whole[40:])
        self.assertEqual(self.sync()["files_updated"], 1)
        self.assertEqual([h["lineno"] for h in search(self.idx, "preloading", sources=["grok"])], [6])
        # The archive copy caught up as well.
        self.assertEqual(GrokSource().parse(self.copy(DOCS))[2][-1][0], 6)

    def test_new_and_deleted_sessions(self):
        session = dict(SESSIONS[DOCS], id="0199a3cb-2c3d-7e4f-9a0b-1c2d3e4f5a6b")
        path = grok_store.write(self.demo.home, session)
        self.assertEqual(self.sync()["files_new"], 1)
        shutil.rmtree(os.path.dirname(path))
        with env(MNEMO_ARCHIVE="off"):
            self.assertEqual(self.sync()["files_removed"], 1)
        self.assertEqual(self.idx.counts()["grok"]["files"], 3)


class ArchiveTest(GrokCase):
    def test_only_the_log_is_copied_and_it_is_private(self):
        self.assertEqual(self.stats["files_archived"], 3)
        copy = self.copy(RELAY)
        self.assertEqual(stat.S_IMODE(os.stat(copy).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(copy)).st_mode), 0o700)
        self.assertEqual(os.listdir(os.path.dirname(copy)), ["updates.jsonl"])
        with open(os.path.join(self.idx.archive.root, "grok", ".grok", "origin.json")) as f:
            self.assertEqual(json.load(f), {"config_dir": self.demo.path(".grok")})

    def test_a_deleted_session_stays_searchable(self):
        path = self.paths[RELAY]
        shutil.rmtree(os.path.dirname(path))  # /delete removes the whole folder
        stats = self.sync()
        self.assertEqual((stats["files_removed"], stats["files_updated"]), (0, 0))  # kept, not even reparsed
        hits = search(self.idx, "jitter", sources=["grok"])
        self.assertTrue(hits)
        self.assertTrue(all(h["archived"] and h["account"] == ".grok" for h in hits))
        sess = get_session(self.idx, path)
        self.assertEqual((sess["archived"], sess["archive_path"], sess["count"]), (True, self.copy(RELAY), 17))
        self.assertEqual(raw_session(self.idx, path)["count"], 17)
        self.assertEqual([m["lineno"] for m in get_context(self.idx, path, 11, before=1, after=0)], [10, 11])
        # Parsed from the copy, the session keeps its id and working directory.
        sid, cwd, _ = GrokSource().parse(self.copy(RELAY))
        self.assertEqual((sid, cwd), (RELAY, "/home/alex/relay"))

    def test_a_changed_session_is_copied_again(self):
        self.append(RELAY, {"sessionUpdate": "user_message_chunk",
                            "content": {"type": "text", "text": "also cap the retry budget at five minutes"}})
        self.assertEqual(self.sync()["files_archived"], 1)
        shutil.rmtree(os.path.dirname(self.paths[RELAY]))
        self.sync()
        self.assertEqual([h["lineno"] for h in search(self.idx, "budget", sources=["grok"])], [24])


class GrokHomesTest(GrokCase):
    def test_every_grok_home_is_indexed_and_labelled(self):
        session = dict(SESSIONS[DOCS], id="0199a3cc-3d4e-7f5a-8b6c-7d8e9f0a1b2c", cwd="/home/alex/work")
        grok_store.write(self.demo.home, session, ".grok-work")
        outside = os.path.join(self.demo.root, "elsewhere", "grok-ci")
        env_path = grok_store.write(self.demo.home, dict(session, id="0199a3cd-4e5f-7a6b-9c7d-8e9f0a1b2c3d"), outside)
        with env(GROK_HOME=outside):
            self.sync()
        hits = search(self.idx, "unstyled", sources=["grok"])
        self.assertEqual(sorted(h["account"] for h in hits), [".grok", ".grok-work", "grok-ci"])
        # Seen once, a $GROK_HOME stays indexed (and archived) without the variable.
        self.assertEqual(self.sync()["files_removed"], 0)
        shutil.rmtree(os.path.dirname(env_path))
        self.sync()
        archived = [h for h in search(self.idx, "unstyled", sources=["grok"]) if h["archived"]]
        self.assertEqual([h["account"] for h in archived], ["grok-ci"])


class CliTest(unittest.TestCase):
    def test_hits_are_labelled_grok(self):
        with contextlib.redirect_stdout(io.StringIO()):
            demo = DemoHome()
        try:
            grok_store.write_all(demo.home)
            run_env = dict(os.environ, HOME=demo.home)

            def mnemo(*argv):
                p = subprocess.run(MNEMO + list(argv), env=run_env, cwd=REPO, capture_output=True, text=True)
                self.assertEqual(p.returncode, 0, p.stderr)
                return p.stdout

            mnemo("index")
            out = mnemo("search", "jitter", "--host", "local", "--source", "grok")
            self.assertIn("[local/grok]", out)  # the default grok home: no account suffix
            self.assertIn("grok", json.loads(mnemo("status", "--json"))["sources"])
            self.assertIn("Fix the flash of unstyled theme", mnemo("recent", "--source", "grok"))
        finally:
            demo.cleanup()


if __name__ == "__main__":
    unittest.main()
