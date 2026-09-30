"""Concurrent writers: MCP server, CLI, dashboard and relayed searches all sync one index."""
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest

from helpers import REPO

from test_mesh import MNEMO

from mnemo.index import Index


def sessions(home, count=6, lines=40):
    for n in range(count):
        d = os.path.join(home, ".claude", "projects", "-p%d" % n)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "s%d.jsonl" % n), "w") as f:
            for i in range(lines):
                f.write(json.dumps({"type": "user", "sessionId": "s%d" % n, "cwd": "/p",
                                    "timestamp": "2026-09-30T08:00:00Z",
                                    "message": {"role": "user", "content": "codename line %d of %d" % (i, n)}}) + "\n")


class ConcurrentSyncTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mnemo-conc-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_simultaneous_syncs_of_a_new_index_all_succeed(self):
        # Two writers that read before writing used to deadlock: SQLite refused one
        # at once ("database is locked") and the other waited out busy_timeout.
        for attempt in range(4):
            home = os.path.join(self.root, "h%d" % attempt)
            sessions(home)
            env = dict(os.environ, HOME=home)
            cmd = MNEMO + ["search", "codename", "--json", "--relay", "--ttl", "0"]
            procs = [subprocess.Popen(cmd, env=env, cwd=REPO, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True) for _ in range(3)]
            for p in procs:
                out, err = p.communicate(timeout=60)
                self.assertEqual(p.returncode, 0, err)
                self.assertEqual(json.loads(out)["warnings"], [], "attempt %d" % attempt)
            idx = Index(os.path.join(home, ".mnemo", "index.db"))
            try:
                self.assertEqual(idx.counts()["claude"], {"files": 6, "messages": 240})
            finally:
                idx.close()

    def test_a_failed_write_releases_the_lock_and_leaves_nothing_behind(self):
        path = os.path.join(self.root, "index.db")
        idx = Index(path)
        with self.assertRaises(RuntimeError):
            with idx._write():
                idx.db.execute("INSERT INTO meta(key, value) VALUES('half', 'done')")
                raise RuntimeError("parse blew up mid-write")
        other = sqlite3.connect(path, timeout=0, isolation_level=None)
        try:
            other.execute("BEGIN IMMEDIATE")  # would fail at once if the lock were still held
            other.execute("ROLLBACK")
        finally:
            other.close()
        self.assertIsNone(idx.db.execute("SELECT value FROM meta WHERE key='half'").fetchone())
        idx.close()

    def test_tables_missing_from_an_older_index_are_created(self):
        path = os.path.join(self.root, "old.db")
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE files (path TEXT PRIMARY KEY, source TEXT, mtime REAL, size INTEGER)")
        db.commit()
        db.close()
        idx = Index(path)
        try:
            names = {r[0] for r in idx.db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            self.assertLessEqual({"files", "file_ranges", "meta", "messages"}, names)
        finally:
            idx.close()


if __name__ == "__main__":
    unittest.main()
