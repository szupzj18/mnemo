"""Automatic and scheduled index backups (`mnemo backup` and `mnemo index` hook)."""
import contextlib
import io
import json
import os
import plistlib
import subprocess
import sys
import time
import unittest

from helpers import REPO, DemoHome

from mnemo import backup, upgrade
from mnemo.index import SCHEMA_VERSION, Index
from mnemo.remote import RemoteError


class BackupCoreTest(unittest.TestCase):
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

    def test_parse_interval(self):
        self.assertEqual(backup.parse_interval("hourly"), 3600)
        self.assertEqual(backup.parse_interval("daily"), 86400)
        self.assertEqual(backup.parse_interval("weekly"), 7 * 86400)
        self.assertEqual(backup.parse_interval("15m"), 900)
        self.assertEqual(backup.parse_interval("12h"), 43200)
        self.assertEqual(backup.parse_interval("3d"), 3 * 86400)
        self.assertEqual(backup.parse_interval("2w"), 14 * 86400)
        for bad in ("", "0m", "30s", "abc", "-1d", None):
            with self.assertRaises(ValueError):
                backup.parse_interval(bad)

    def test_manual_backup_if_due_dest_and_prune(self):
        dest = os.path.join(self.demo.root, "custom-backups")
        first = backup.run_backup(self.demo.db, keep=2, dest=dest, if_due=True, every="daily")
        self.assertFalse(first["skipped"])
        self.assertTrue(os.path.isfile(first["created"]))
        self.assertEqual(first["dest"], dest)

        # Immediately running --if-due skips while the backup is fresh.
        second = backup.run_backup(self.demo.db, keep=2, dest=dest, if_due=True, every="daily")
        self.assertTrue(second["skipped"])
        self.assertIsNone(second["created"])

        # Once older than the interval, --if-due creates a new snapshot.
        old_time = time.time() - 90000
        os.utime(first["created"], (old_time, old_time))
        third = backup.run_backup(self.demo.db, keep=2, dest=dest, if_due=True, every="daily", label="due")
        self.assertFalse(third["skipped"])
        self.assertEqual(third["kept"], 2)

        # Pruning down to 1 removes the older snapshot.
        removed = backup.prune(self.demo.db, keep=1, dest=dest)
        self.assertEqual(len(removed), 1)
        self.assertEqual(len(upgrade.list_backups(self.demo.db, dest_dir=dest)), 1)

    def test_auto_backup_on_index_when_enabled(self):
        # Off by default.
        self.assertIsNone(backup.maybe_auto_backup(self.demo.db))
        self.assertEqual(upgrade.list_backups(self.demo.db), [])

        backup.save_config(auto=True, every="daily", keep=2)
        res = backup.maybe_auto_backup(self.demo.db)
        self.assertFalse(res["skipped"])
        self.assertTrue(os.path.isfile(res["created"]))
        self.assertIn("-auto.db", res["created"])

        # Second call within interval skips.
        res2 = backup.maybe_auto_backup(self.demo.db)
        self.assertTrue(res2["skipped"])


class BackupSchedulerTest(unittest.TestCase):
    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.demo = DemoHome()
        self.demo.activate()
        self.calls = []
        self._saved = (backup._run, os.environ.get("MNEMO_SERVICE_MANAGER"))
        backup._run = lambda argv, check=True: self.calls.append(argv) or 0

    def tearDown(self):
        backup._run, forced = self._saved
        if forced is None:
            os.environ.pop("MNEMO_SERVICE_MANAGER", None)
        else:
            os.environ["MNEMO_SERVICE_MANAGER"] = forced
        self.demo.deactivate()
        self.demo.cleanup()

    def use(self, mgr):
        os.environ["MNEMO_SERVICE_MANAGER"] = mgr

    def test_launchd_schedule_install_and_uninstall(self):
        self.use("launchd")
        mgr, cfg = backup.install_schedule(every="12h", keep=5, db_path=self.demo.db)
        self.assertEqual(mgr, "launchd")
        self.assertEqual(cfg["every"], "12h")
        self.assertEqual(cfg["keep"], 5)

        path = os.path.join(self.demo.home, "Library", "LaunchAgents", "dev.mnemo.backup.plist")
        self.assertTrue(os.path.isfile(path))
        with open(path, "rb") as f:
            plist = plistlib.load(f)
        self.assertEqual(plist["Label"], "dev.mnemo.backup")
        self.assertEqual(plist["StartInterval"], 12 * 3600)
        self.assertTrue(plist["RunAtLoad"])
        self.assertEqual(plist["ProgramArguments"][-4:], ["backup", "--if-due", "--label", "scheduled"])
        self.assertEqual([c[1] for c in self.calls], ["bootout", "bootstrap"])
        self.assertTrue(backup.schedule_installed())

        self.calls.clear()
        self.assertEqual(backup.uninstall_schedule(), "launchd")
        self.assertEqual([c[1] for c in self.calls], ["bootout"])
        self.assertFalse(os.path.exists(path))
        self.assertFalse(backup.schedule_installed())

    def test_systemd_timer_install_and_uninstall(self):
        self.use("systemd")
        mgr, _ = backup.install_schedule(every="weekly", keep=4, db_path=self.demo.db)
        self.assertEqual(mgr, "systemd")

        spath = os.path.join(self.demo.home, ".config", "systemd", "user", "mnemo-backup.service")
        tpath = os.path.join(self.demo.home, ".config", "systemd", "user", "mnemo-backup.timer")
        with open(spath) as f:
            svc = f.read()
        with open(tpath) as f:
            tmr = f.read()
        self.assertIn("Type=oneshot", svc)
        self.assertIn("backup --if-due --label scheduled", svc)
        self.assertIn("OnUnitActiveSec=604800s", tmr)
        self.assertIn("Persistent=true", tmr)
        self.assertEqual(self.calls, [
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "enable", "--now", "mnemo-backup.timer"],
        ])
        self.assertTrue(backup.schedule_installed())

        self.calls.clear()
        self.assertEqual(backup.uninstall_schedule(), "systemd")
        self.assertEqual(self.calls[0], ["systemctl", "--user", "disable", "--now", "mnemo-backup.timer"])
        self.assertFalse(os.path.exists(spath))
        self.assertFalse(os.path.exists(tpath))

    def test_unsupported_scheduler_raises(self):
        self.use("none")
        with self.assertRaises(RemoteError):
            backup.install_schedule(every="daily")


class BackupCLITest(unittest.TestCase):
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

    def run_cli(self, *args):
        env = dict(os.environ, HOME=self.demo.home)
        return subprocess.run(
            [sys.executable, os.path.join(REPO, "bin", "mnemo"), "--db", self.demo.db] + list(args),
            capture_output=True, text=True, env=env, timeout=120,
        )

    def test_cli_backup_auto_index_list_restore_and_status(self):
        dest = os.path.join(self.demo.root, "bkp")

        # Enable auto-backup on index.
        r = self.run_cli("backup", "--auto", "on", "--every", "daily", "--keep", "2", "--dest", dest, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = json.loads(r.stdout)
        self.assertTrue(cfg["auto"])
        self.assertEqual(cfg["every"], "daily")

        # `mnemo index --no-backup` skips auto backup.
        r = self.run_cli("index", "--no-backup")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(upgrade.list_backups(self.demo.db, dest_dir=dest), [])

        # `mnemo index` triggers auto backup when due.
        r = self.run_cli("index")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("auto backup:", r.stdout)
        self.assertEqual(len(upgrade.list_backups(self.demo.db, dest_dir=dest)), 1)

        # `mnemo backup --list --json` lists it.
        r = self.run_cli("backup", "--list", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        listed = json.loads(r.stdout)
        self.assertEqual(len(listed["backups"]), 1)
        self.assertIn("index-v%s-" % SCHEMA_VERSION, listed["backups"][0]["path"])

        # `mnemo backup --status --json` reports auto=True and count=1.
        r = self.run_cli("backup", "--status", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        st = json.loads(r.stdout)
        self.assertTrue(st["auto"])
        self.assertEqual(st["count"], 1)

        # `mnemo backup --restore` brings back wiped rows.
        idx = Index(self.demo.db)
        idx.db.execute("DELETE FROM files")
        idx.close()
        r = self.run_cli("backup", "--restore")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("restored", r.stdout)
        idx = Index(self.demo.db)
        self.assertEqual(sum(c["files"] for c in idx.counts().values()), 6)
        idx.close()


if __name__ == "__main__":
    unittest.main()
