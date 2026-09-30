import json
import os
import shutil
import sqlite3
import tempfile
import unittest

from mnemo.index import Index
from mnemo.search import get_context, get_session, search

SCHEMA = """
CREATE TABLE session_v2 (
  id text PRIMARY KEY, directory text NOT NULL,
  time_created integer NOT NULL, time_updated integer NOT NULL);
CREATE TABLE message (
  id text PRIMARY KEY, session_id text NOT NULL,
  time_created integer NOT NULL, time_updated integer NOT NULL, data text NOT NULL);
CREATE TABLE part (
  id text PRIMARY KEY, message_id text NOT NULL, session_id text NOT NULL,
  time_created integer NOT NULL, time_updated integer NOT NULL, data text NOT NULL);
CREATE INDEX part_session_idx ON part(session_id);
CREATE INDEX part_message_id_id_idx ON part(message_id, id);
"""

T0 = 1759000000000  # fixed milliseconds


class OpenCodeTest(unittest.TestCase):
    """A synthetic OpenCode database, built here so the test stays hermetic."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mnemo-opencode-")
        self.home = os.path.join(self.root, "home")
        os.makedirs(os.path.join(self.home, ".local", "share", "opencode"))
        self.db_path = os.path.join(self.home, ".local", "share", "opencode", "opencode.db")
        self._write(self.fixture())
        self.idx = Index(os.path.join(self.root, "index.db"))
        self.stats = self.idx.sync(home=self.home)

    def tearDown(self):
        self.idx.close()
        shutil.rmtree(self.root, ignore_errors=True)

    # ------------------------------------------------------------- fixture
    def _write(self, rows):
        con = sqlite3.connect(self.db_path)
        con.executescript(SCHEMA)
        con.execute("INSERT INTO session_v2 VALUES (?,?,?,?)",
                    ("ses_alpha", "/home/alex/relay", T0, T0 + 5000))
        con.execute("INSERT INTO session_v2 VALUES (?,?,?,?)",
                    ("ses_beta", "/home/alex/docs", T0 + 9000, T0 + 9200))
        for mid, sid, role, t in (("m1", "ses_alpha", "user", T0 + 1000),
                                  ("m2", "ses_alpha", "assistant", T0 + 2000),
                                  ("m3", "ses_alpha", "user", T0 + 3000),
                                  ("m4", "ses_beta", "user", T0 + 9000),
                                  ("m5", "ses_beta", "assistant", T0 + 9200)):
            con.execute("INSERT INTO message VALUES (?,?,?,?,?)",
                        (mid, sid, t, t, json.dumps({"role": role})))
        for pid, mid, sid, t, data in rows:
            con.execute("INSERT INTO part VALUES (?,?,?,?,?,?)",
                        (pid, mid, sid, t, t, json.dumps(data, ensure_ascii=False)))
        con.commit()
        con.close()

    def fixture(self):
        return [
            ("p101", "m1", "ses_alpha", T0 + 1000,
             {"type": "text", "text": "Webhook retries give up too early, add exponential backoff."}),
            ("p102", "m2", "ses_alpha", T0 + 2000, {"type": "step-start"}),
            ("p103", "m2", "ses_alpha", T0 + 2100,
             {"type": "reasoning", "text": "The retry loop uses a fixed delay; jitter is the fix."}),
            ("p104", "m2", "ses_alpha", T0 + 2200,
             {"type": "text", "text": "Switching to exponential backoff with jitter, capped at 30s."}),
            ("p105", "m2", "ses_alpha", T0 + 2300,
             {"type": "tool", "tool": "bash", "state": {
                 "status": "completed", "input": {"command": "rg -n backoff relay/"},
                 "output": "relay/backoff.py:3:def backoff(attempt, base=0.5, cap=30.0):"}}),
            ("p106", "m2", "ses_alpha", T0 + 2400,
             {"type": "patch", "files": ["/home/alex/relay/backoff.py"]}),
            ("p107", "m2", "ses_alpha", T0 + 2500, {"type": "step-finish"}),
            ("p108", "m3", "ses_alpha", T0 + 3000,
             {"type": "text", "text": "中文子串匹配也要能搜到：跨设备会话检索。"}),
            ("p201", "m4", "ses_beta", T0 + 9100,
             {"type": "text", "text": "<system-reminder>\nAGENTS.md instructions for /home/alex/docs\n"
                                      "</system-reminder>\n\nFix the search page flash."}),
            ("p202", "m5", "ses_beta", T0 + 9200,
             {"type": "text", "text": "The theme stylesheet loads async; make it blocking."}),
        ]

    def touch(self, part_id, t):
        con = sqlite3.connect(self.db_path)
        con.execute("UPDATE part SET time_updated = ? WHERE id = ?", (t, part_id))
        con.commit()
        con.close()

    # --------------------------------------------------------------- tests
    def test_indexes_the_sessions_and_their_parts(self):
        self.assertEqual(self.stats["files_new"], 2)
        self.assertEqual(self.stats["messages"], 9)  # text x5, reasoning, tool_call, tool_result, patch
        counts = self.idx.counts()["opencode"]
        self.assertEqual(counts["files"], 2)
        self.assertEqual(counts["messages"], 9)

    def test_search_matches_text_tools_and_cjk(self):
        hits = search(self.idx, "backoff", sources=["opencode"])
        self.assertTrue(hits)
        self.assertTrue(all(h["session_id"] == "ses_alpha" for h in hits))
        self.assertTrue(any("jitter" in h["snippet"] for h in hits))
        self.assertTrue(any(h["kind"] == "tool_call" for h in hits))
        self.assertTrue(any(h["kind"] == "tool_result" for h in hits))
        self.assertTrue(search(self.idx, "会话 检索"))
        self.assertTrue(search(self.idx, "patched"))

    def test_injected_boilerplate_is_hidden_by_default(self):
        self.assertEqual(search(self.idx, "AGENTS.md"), [])
        hits = search(self.idx, "AGENTS.md", include_injected=True)
        self.assertTrue(any(h["session_id"] == "ses_beta" for h in hits))
        # The genuine instruction inside the same message stays searchable.
        self.assertTrue(search(self.idx, "flash"))

    def test_incremental_sync_follows_parts_and_sessions(self):
        self.assertEqual(self.idx.sync(home=self.home)["files_updated"], 0)
        self.touch("p202", T0 + 60000)  # an updated part alone must be noticed
        stats = self.idx.sync(home=self.home)
        self.assertEqual((stats["files_new"], stats["files_updated"]), (0, 1))

    def test_new_part_is_searchable_after_sync(self):
        con = sqlite3.connect(self.db_path)
        con.execute("INSERT INTO message VALUES (?,?,?,?,?)",
                    ("m6", "ses_beta", T0 + 40000, T0 + 40000, json.dumps({"role": "user"})))
        con.execute("INSERT INTO part VALUES (?,?,?,?,?,?)",
                    ("p203", "m6", "ses_beta", T0 + 40000, T0 + 40000,
                     json.dumps({"type": "text", "text": "Also update the changelog."})))
        con.commit()
        con.close()
        stats = self.idx.sync(home=self.home)
        self.assertEqual((stats["files_new"], stats["files_updated"]), (0, 1))
        hits = search(self.idx, "changelog")
        self.assertTrue(any(h["session_id"] == "ses_beta" for h in hits))

    def test_deleted_session_is_removed(self):
        con = sqlite3.connect(self.db_path)
        con.execute("DELETE FROM part WHERE session_id = 'ses_alpha'")
        con.execute("DELETE FROM message WHERE session_id = 'ses_alpha'")
        con.execute("DELETE FROM session_v2 WHERE id = 'ses_alpha'")
        con.commit()
        con.close()
        stats = self.idx.sync(home=self.home)
        self.assertEqual(stats["files_removed"], 1)
        self.assertEqual(search(self.idx, "backoff"), [])

    def test_context_and_session_reads(self):
        hits = search(self.idx, "jitter")
        path = hits[0]["path"]
        sess = get_session(self.idx, path)
        self.assertEqual(sess["session_id"], "ses_alpha")
        self.assertEqual(sess["cwd"], "/home/alex/relay")
        self.assertEqual(sess["count"], 7)
        window = get_context(self.idx, path, hits[0]["lineno"], before=1, after=2)
        self.assertTrue(any(r["hit"] for r in window))
        self.assertTrue(any("Switching" in r["text"] for r in window))

    def test_broken_database_is_skipped(self):
        with open(self.db_path, "wb") as f:
            f.write(b"not a database")
        idx = Index(os.path.join(self.root, "other.db"))
        stats = idx.sync(home=self.home)
        self.assertEqual(stats["files_new"], 0)
        idx.close()

    def test_an_unreadable_database_keeps_what_was_indexed(self):
        # Unreadable is not empty: sessions indexed earlier must not be dropped as deleted.
        broken = [("garbage", lambda: open(self.db_path, "wb").write(b"not a database"))]
        if os.geteuid() != 0:  # root reads mode-000 files anyway
            broken.append(("mode 000", lambda: os.chmod(self.db_path, 0)))
        good = open(self.db_path, "rb").read()
        for label, breakit in broken:
            with self.subTest(label):
                breakit()
                try:
                    stats = self.idx.sync(home=self.home)
                finally:
                    os.chmod(self.db_path, 0o644)
                    with open(self.db_path, "wb") as f:
                        f.write(good)
                self.assertEqual(stats["files_removed"], 0)
                self.assertEqual(self.idx.counts()["opencode"]["files"], 2)

    def test_uri_characters_in_the_path(self):
        # "#", "?" and "%" would otherwise be read as SQLite URI syntax.
        for name in ("a#b", "a?b", "a%25b"):
            with self.subTest(name):
                home = os.path.join(self.root, name, "home")
                shutil.copytree(os.path.join(self.home, ".local"), os.path.join(home, ".local"))
                idx = Index(os.path.join(self.root, name, "index.db"))
                try:
                    idx.sync(home=home)
                    self.assertEqual(idx.counts()["opencode"]["files"], 2)
                finally:
                    idx.close()


class XdgDataHomeTest(unittest.TestCase):
    """OpenCode stores its database under $XDG_DATA_HOME/opencode when that is set."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mnemo-opencode-xdg-")
        self.saved = {k: os.environ.get(k) for k in ("HOME", "XDG_DATA_HOME")}
        self.home = os.path.join(self.root, "home")
        self.xdg = os.path.join(self.root, "xdg-data")
        os.makedirs(os.path.join(self.xdg, "opencode"))
        os.makedirs(self.home)
        test = OpenCodeTest("test_indexes_the_sessions_and_their_parts")
        test.db_path = os.path.join(self.xdg, "opencode", "opencode.db")
        test._write(test.fixture())

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.root, ignore_errors=True)

    def sync(self, home=None):
        idx = Index(os.path.join(self.root, "index-%s.db" % bool(home)))
        try:
            idx.sync(home=home)
            return idx.counts().get("opencode", {}).get("files", 0)
        finally:
            idx.close()

    def test_used_for_the_users_own_home(self):
        os.environ["HOME"] = self.home
        os.environ["XDG_DATA_HOME"] = self.xdg
        self.assertEqual(self.sync(), 2)

    def test_ignored_for_another_home(self):
        os.environ["XDG_DATA_HOME"] = self.xdg
        other = os.path.join(self.root, "other-home")
        os.makedirs(other)
        self.assertEqual(self.sync(home=other), 0)


if __name__ == "__main__":
    unittest.main()
