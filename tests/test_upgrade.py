import contextlib
import hashlib
import io
import os
import subprocess
import sys
import unittest

from helpers import REPO, DemoHome

from mnemo import remote, upgrade
from mnemo.index import SCHEMA_VERSION, Index, IndexTooNew


def digest(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class UpgradeTest(unittest.TestCase):
    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.demo = DemoHome()
        self.demo.activate()
        idx = Index(self.demo.db)
        idx.sync(home=self.demo.home)
        idx.close()

    def tearDown(self):
        self.demo.deactivate()
        self.demo.cleanup()

    # ------------------------------------------------------------ backups

    def test_backup_is_a_consistent_copy_and_prunes_old_ones(self):
        first = upgrade.backup(self.demo.db, keep=2)
        self.assertTrue(os.path.basename(first).startswith("index-v%s-" % SCHEMA_VERSION))
        copy = Index(first)
        self.assertEqual(sum(c["messages"] for c in copy.counts().values()), 45)
        copy.close()
        for label in ("a", "b", "c"):
            upgrade.backup(self.demo.db, keep=2, label=label)
        self.assertEqual(len(upgrade.list_backups(self.demo.db)), 2)

    def test_backup_of_missing_index_is_a_noop(self):
        self.assertIsNone(upgrade.backup(os.path.join(self.demo.root, "nope.db")))

    # ------------------------------------------------------------ rebuild

    def test_rebuild_swaps_in_a_verified_index(self):
        stats, counts = upgrade.rebuild(self.demo.db, home=self.demo.home)
        self.assertEqual(stats["files_new"], 5)
        self.assertEqual(sum(c["messages"] for c in counts.values()), 45)
        idx = Index(self.demo.db)
        self.assertEqual(upgrade.verify(idx), [])
        idx.close()
        self.assertEqual([p for p in os.listdir(self.demo.root) if ".rebuild-" in p], [])

    def test_failed_verification_leaves_the_live_index_untouched(self):
        before = digest(self.demo.db)
        original = upgrade.verify
        upgrade.verify = lambda idx: ["simulated failure"]
        try:
            with self.assertRaises(upgrade.UpgradeError):
                upgrade.rebuild(self.demo.db, home=self.demo.home)
        finally:
            upgrade.verify = original
        self.assertEqual(digest(self.demo.db), before)
        self.assertEqual([p for p in os.listdir(self.demo.root) if ".rebuild-" in p], [])

    def test_restore_brings_back_the_newest_backup(self):
        saved = upgrade.backup(self.demo.db)
        idx = Index(self.demo.db)
        idx.db.execute("DELETE FROM files")
        idx.close()
        source, safety = upgrade.restore(self.demo.db)
        self.assertEqual(source, saved)
        self.assertTrue(os.path.isfile(safety))
        idx = Index(self.demo.db)
        self.assertEqual(sum(c["files"] for c in idx.counts().values()), 5)
        idx.close()

    # ------------------------------------------------------- safety nets

    def test_sync_repairs_rows_written_by_an_older_mnemo(self):
        idx = Index(self.demo.db)
        path = idx.db.execute("SELECT path FROM files LIMIT 1").fetchone()[0]
        # What a pre-v2 writer leaves behind: no title, messages without text/envelope.
        idx.db.execute("UPDATE files SET title = NULL WHERE path = ?", (path,))
        idx.db.execute(
            "INSERT INTO messages(body, grams, source, session_id, cwd, ts, role, kind, path, lineno)"
            " VALUES('old row', '', 'claude', 's', '/x', '2026-09-28T00:00:00Z', 'user', 'text', ?, 999)",
            (path,),
        )
        self.assertEqual(idx.incomplete_paths(), [path])
        stats = idx.sync(home=self.demo.home)
        self.assertEqual(stats["files_updated"], 1)
        self.assertEqual(idx.incomplete_paths(), [])
        self.assertEqual(idx.db.execute("SELECT COUNT(*) FROM messages WHERE text IS NULL").fetchone()[0], 0)
        idx.close()

    def test_a_v2_index_is_rebuilt_to_store_text_once(self):
        idx = Index(self.demo.db)
        # v2 stored every message's text twice: body beside text.
        idx.db.execute("UPDATE meta SET value = '2' WHERE key = 'schema_version'")
        idx.db.execute("UPDATE messages SET body = text WHERE body IS NULL")
        idx.close()

        idx = Index(self.demo.db)
        self.assertTrue(idx._migration_pending)
        self.assertFalse(upgrade.schema_current(self.demo.db))
        idx.sync(home=self.demo.home)
        self.assertEqual(idx.stored_version, "3")
        self.assertEqual(idx.db.execute("SELECT COUNT(*) FROM messages WHERE body = text").fetchone()[0], 0)
        self.assertEqual(sum(c["messages"] for c in idx.counts().values()), 45)
        idx.close()
        self.assertTrue(upgrade.schema_current(self.demo.db))

    def test_older_code_refuses_to_write_a_newer_index(self):
        idx = Index(self.demo.db)
        idx.db.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
        idx.close()

        idx = Index(self.demo.db)
        self.assertTrue(idx.too_new)
        with self.assertRaises(IndexTooNew):
            idx.sync(home=self.demo.home)
        with self.assertRaises(IndexTooNew):
            idx.rebuild(home=self.demo.home)
        self.assertEqual(
            idx.db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], "99",
            "must not re-stamp the version of a newer index",
        )
        self.assertEqual(sum(c["messages"] for c in idx.counts().values()), 45, "reads still work")
        idx.close()

        warnings = []
        hits = remote._local_search(self.demo.db, "backoff", None, None, None, None, 5, True, warnings)
        self.assertTrue(hits, "search degrades to read-only instead of failing")
        self.assertTrue(any("newer than this mnemo" in w for w in warnings))

    # ----------------------------------------------------------- the CLI

    def test_cli_upgrade_list_and_restore(self):
        env = dict(os.environ, HOME=self.demo.home)
        run = lambda *a: subprocess.run(
            [sys.executable, os.path.join(REPO, "bin", "mnemo"), "--db", self.demo.db] + list(a),
            capture_output=True, text=True, env=env, timeout=120,
        )
        r = run("upgrade")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("backup:", r.stdout)
        self.assertIn("verified and swapped in: 5 sessions, 45 messages", r.stdout)
        self.assertEqual(len(upgrade.list_backups(self.demo.db)), 1)
        self.assertIn("index-v%s-" % SCHEMA_VERSION, run("upgrade", "--list").stdout)
        r = run("upgrade", "--restore")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("restored", r.stdout)
        self.assertIn("no other devices", run("upgrade", "--no-backup").stdout)

    def test_if_needed_only_syncs_a_current_schema(self):
        env = dict(os.environ, HOME=self.demo.home)
        r = subprocess.run([sys.executable, os.path.join(REPO, "bin", "mnemo"), "--db", self.demo.db,
                            "upgrade", "--if-needed"], capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("schema v%s is current" % SCHEMA_VERSION, r.stdout)
        self.assertEqual(upgrade.list_backups(self.demo.db), [], "no rebuild, so no backup")
        self.assertFalse(upgrade.schema_current(self.demo.db + ".missing"))


class StaleProcessTest(unittest.TestCase):
    def test_parses_both_ps_date_layouts(self):
        out = (
            "  1536 Mon Sep 28 13:23:00 2026 /usr/bin/python3 /home/a/.local/bin/mnemo mcp\n"
            "  2000 Mon 28 Sep 16:43:51 2026 python3 bin/mnemo dashboard --no-open\n"
            "garbage line\n"
        )
        rows = upgrade.parse_ps(out)
        self.assertEqual([r[0] for r in rows], [1536, 2000])
        self.assertLess(rows[0][1], rows[1][1])

    def test_rsync_never_ships_the_web_toolchain(self):
        for pattern in ("node_modules", ".next", ".pnpm-store", "index.db"):
            self.assertIn(pattern, remote.RSYNC_EXCLUDES)


if __name__ == "__main__":
    unittest.main()
