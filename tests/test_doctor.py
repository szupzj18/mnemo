"""mnemo doctor: each check's verdicts, the report, and the CLI exit code."""
import contextlib
import io
import json
import os
import sqlite3
import subprocess
import unittest
from unittest import mock

from helpers import REPO, DemoHome

from test_mesh import MNEMO, Device

from mnemo import doctor, remote
from mnemo import setup as st
from mnemo.index import Index

NOW = 1790000000.0


class VersionTest(unittest.TestCase):
    def test_newer_release_is_a_warning_with_the_upgrade_command(self):
        c = doctor.check_version(fetch=lambda: "99.0.0", commits=None)
        self.assertEqual(c.status, doctor.WARN)
        self.assertIn("99.0.0 is out", c.detail)
        self.assertIn("mnemo upgrade", c.fix)

    def test_current_offline_or_unreachable_is_fine(self):
        from mnemo import __version__
        c = doctor.check_version(fetch=lambda: __version__, commits=None)
        self.assertEqual((c.name, c.status, c.detail), ("mnemo", doctor.OK, "%s \u00b7 latest release" % __version__))
        self.assertEqual(doctor.check_version(fetch=lambda: "0.0.1", commits=None).status, doctor.OK)
        self.assertEqual(doctor.check_version(offline=True, commits=None).status, doctor.OK)

        def boom():
            raise OSError("no network")
        self.assertIn("could not check", doctor.check_version(fetch=boom, commits=None).detail)

    def test_a_checkout_says_how_far_it_is_past_the_release(self):
        from mnemo import __version__
        c = doctor.check_version(offline=True, commits=5)
        self.assertTrue(c.detail.startswith("%s + 5 unreleased commits \u00b7 checkout" % __version__), c.detail)
        self.assertIn("\u00b7 checkout", doctor.check_version(offline=True, commits=0).detail)
        self.assertNotIn("checkout", doctor.check_version(offline=True, commits=None).detail)


class IndexTest(unittest.TestCase):
    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.demo = DemoHome()
        self.demo.activate()  # the archive setting lives in the (demo) node.json

    def tearDown(self):
        self.demo.deactivate()
        self.demo.cleanup()

    def test_missing_index_says_how_to_build_it(self):
        [c] = doctor.check_index(os.path.join(self.demo.root, "nope.db"))
        self.assertEqual((c.status, c.fix), (doctor.FAIL, "mnemo index"))

    def test_healthy_index_and_full_check(self):
        idx = Index(self.demo.db)
        idx.sync(home=self.demo.home)
        idx.close()
        checks = doctor.check_index(self.demo.db, full=True, now=NOW)
        self.assertEqual([(c.name, c.status) for c in checks],
                         [("index", doctor.OK), ("archive", doctor.OK), ("integrity", doctor.OK)])
        self.assertIn("6 sessions", checks[0].detail)
        self.assertIn("6 session files", checks[1].detail)

    def test_archive_off_is_reported_with_the_switch(self):
        os.environ["MNEMO_ARCHIVE"] = "off"
        try:
            Index(self.demo.db).sync(home=self.demo.home)
            check = [c for c in doctor.check_index(self.demo.db, now=NOW) if c.name == "archive"][0]
        finally:
            del os.environ["MNEMO_ARCHIVE"]
        self.assertEqual((check.status, check.fix), (doctor.SKIP, "mnemo node --archive on"))

    def test_rows_from_an_older_mnemo_are_a_warning(self):
        idx = Index(self.demo.db)
        idx.sync(home=self.demo.home)
        idx.db.execute("UPDATE files SET title = NULL WHERE rowid = (SELECT MIN(rowid) FROM files)")
        idx.close()
        old = [c for c in doctor.check_index(self.demo.db) if c.name == "old rows"]
        self.assertEqual([(c.status, c.fix) for c in old], [(doctor.WARN, "mnemo index")])


class AgentsTest(unittest.TestCase):
    def test_each_agent_gets_one_verdict(self):
        S = st.Step
        checks = doctor.check_agents([
            S("claude", "MCP server", "ok", "ok"), S("claude", "skill", "x", "ok"),
            S("codex", "MCP server", "x", "added"),
            S("opencode", "MCP server", "x.jsonc has comments\nadd this", "skipped"),
            S("pi", "detect", "not installed", "skipped"),
        ])
        got = {c.name: (c.status, c.fix) for c in checks}
        self.assertEqual(got["Claude Code"], (doctor.OK, None))
        self.assertEqual(got["Codex"], (doctor.WARN, "mnemo setup"))
        self.assertEqual(got["OpenCode"][0], doctor.WARN)
        self.assertEqual(got["Pi"], (doctor.SKIP, None))


class ZstdTest(unittest.TestCase):
    def test_reported_only_when_compressed_logs_exist(self):
        import shutil
        import tempfile
        root = tempfile.mkdtemp(prefix="mnemo-doctor-zstd-")
        try:
            home = os.path.join(root, "home")
            folder = os.path.join(home, ".dsh", "sessions", "--p--", "session-a")
            os.makedirs(folder)
            plain = os.path.join(folder, "session.v4.jsonl")
            open(plain, "w").close()
            # A plaintext DSH backend needs nothing.
            self.assertIsNone(doctor.check_zstd(home, find=lambda: None))
            os.rename(plain, plain + ".zstd")
            ok = doctor.check_zstd(home, find=lambda: "/usr/bin/zstd")
            self.assertEqual((ok.name, ok.status), ("zstd", doctor.OK))
            self.assertIn("DeepSeek Harness", ok.detail)
            missing = doctor.check_zstd(home, find=lambda: None)
            self.assertEqual(missing.status, doctor.WARN)
            self.assertIn("install zstd", missing.fix)
        finally:
            shutil.rmtree(root, ignore_errors=True)


class DevicesTest(unittest.TestCase):
    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.demo = DemoHome()
        self.demo.activate()
        remote.save_remotes([{"name": n, "host": n} for n in ("a", "gone", "old")]
                            + [{"name": "lap", "host": "link:lap", "transport": "link"}])

    def tearDown(self):
        self.demo.deactivate()
        self.demo.cleanup()

    def test_reachability_code_and_relays(self):
        tree = {"code": "c1", "neighbors": [
            {"name": "a", "ok": True, "ms": 40, "node": {"code": "c1", "forward": True, "neighbors": [
                {"name": "b", "ok": True, "ms": 90, "node": {"code": "c0", "forward": False, "neighbors": []}},
                {"name": "back", "seen": True, "node": None},
            ]}},
            {"name": "gone", "ok": False, "error": "gone: timed out", "node": None},
            {"name": "old", "ok": True, "ms": 5, "legacy": True, "node": None},
            {"name": "lap", "ok": False, "error": "not linked", "node": None},
        ]}
        checks = {c.name: c for c in doctor.check_devices(tree)}
        self.assertEqual(checks["a"].fields, ["40 ms", "same", "on"])
        self.assertEqual(checks["a/b"].fields, ["90 ms", "other", "off"])
        self.assertEqual(checks["old"].fields, ["5 ms", "older mnemo", "?"])
        self.assertIsNone(checks["gone"].fields, "unreachable rows say why instead")
        got = {c.name: (c.status, c.fix) for c in checks.values()}
        self.assertEqual(got["a"], (doctor.OK, None))
        self.assertEqual(got["a/b"], (doctor.WARN, "mnemo remote upgrade"))
        self.assertEqual(got["gone"], (doctor.FAIL, "check the connection: ssh gone"))
        self.assertEqual(got["old"], (doctor.WARN, "mnemo remote upgrade"))
        self.assertEqual(got["lap"], (doctor.SKIP, None), "a laptop that linked in and is asleep is fine")
        self.assertNotIn("a/back", got)

    def test_no_devices(self):
        remote.save_remotes([])
        [c] = doctor.check_devices({"neighbors": []})
        self.assertEqual(c.status, doctor.SKIP)


class ConnectivityTest(unittest.TestCase):
    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.demo = DemoHome()
        self.demo.activate()

    def tearDown(self):
        self.demo.deactivate()
        self.demo.cleanup()

    def response(self, **extra):
        return json.dumps(dict({"hits": [], "warnings": [],
                                "coverage": [{"host": "local", "status": "searched"}]}, **extra))

    def test_direct_link_and_legacy_searches_do_not_refresh_or_forward(self):
        remote.save_remotes([{"name": "box", "host": "ssh-box"},
                             {"name": "lap", "host": "link:lap", "transport": "link"},
                             {"name": "old", "host": "old", "proto": 1}])

        def answer(device, argv, timeout):
            self.assertEqual(timeout, doctor.CONNECTIVITY_TIMEOUT)
            self.assertIn("--no-sync", argv)
            self.assertEqual(argv[argv.index("--host") + 1], "local")
            self.assertEqual(argv[argv.index("--limit") + 1], "1")
            if device["name"] == "old":
                self.assertNotIn("--relay", argv)
                return "[]"
            self.assertIn("--relay", argv)
            self.assertEqual(argv[argv.index("--ttl") + 1], "0")
            return self.response()

        with mock.patch.object(remote, "remote_exec", side_effect=answer) as execute, \
                mock.patch.object(doctor.link, "is_up", return_value=True):
            checks = doctor.check_connectivity()
        self.assertEqual(execute.call_count, 3)
        self.assertTrue(all(c.status == doctor.OK for c in checks), checks)
        self.assertIn("reverse link", checks[1].detail)

    def test_disconnected_link_is_skipped_but_a_stale_socket_is_not_healthy(self):
        remote.save_remotes([{"name": "lap", "host": "link:lap", "transport": "link"}])
        with mock.patch.object(doctor.link, "is_up", return_value=False), \
                mock.patch.object(remote, "remote_exec") as execute:
            [check] = doctor.check_connectivity()
        self.assertEqual(check.status, doctor.SKIP)
        execute.assert_not_called()
        with mock.patch.object(doctor.link, "is_up", return_value=True), \
                mock.patch.object(remote, "remote_exec", side_effect=remote.RemoteError("lap: timed out")):
            [check] = doctor.check_connectivity()
        self.assertEqual(check.status, doctor.WARN)
        self.assertIn("timed out", check.detail)
        self.assertIn("mnemo link", check.fix)

    def test_a_successful_process_must_confirm_a_search_and_report_its_warnings(self):
        remote.save_remotes([{"name": "box", "host": "box"}])
        for reply, reason in [
            ("not json", "search failed"),
            ("[]", "invalid search response"),
            ("{}", "invalid search response"),
            (self.response(coverage=[]), "did not confirm"),
            (self.response(coverage=[{"host": "local", "status": "failed"}]), "did not confirm"),
            (self.response(warnings=["index unavailable"]), "index unavailable"),
        ]:
            with self.subTest(reply=reply), mock.patch.object(remote, "remote_exec", return_value=reply):
                [check] = doctor.check_connectivity()
            self.assertEqual(check.status, doctor.WARN)
            self.assertIn(reason, check.detail)
            self.assertIn("ssh box", check.fix)

    def test_real_search_does_not_sync_or_reach_the_neighbors_neighbor(self):
        box, other = Device("box", self.demo.root), Device("other", self.demo.root)
        box.node(forward=True)
        box.link(other)
        env = dict(os.environ, HOME=box.home)
        subprocess.run(MNEMO + ["index"], env=env, check=True, capture_output=True)
        db = os.path.join(box.config, "index.db")
        with sqlite3.connect(db) as connection:
            connection.execute("UPDATE meta SET value='0' WHERE key='last_sync'")
        connection.close()
        with open(db, "rb") as f:
            before = f.read()
        remote.save_remotes([{"name": "box", "host": "local:box", "transport": "local", "home": box.home}])
        [check] = doctor.check_connectivity()
        self.assertEqual(check.status, doctor.OK, check.detail)
        with open(db, "rb") as f:
            self.assertEqual(f.read(), before, "the connectivity search must not refresh the index")
        self.assertFalse(os.path.exists(os.path.join(other.config, "index.db")), "the probe must not forward")


class LinksTest(unittest.TestCase):
    def test_states(self):
        items = [
            {"remote": "up", "installed": True, "state": "connected", "since": NOW - 3600, "error": None},
            {"remote": "trying", "installed": True, "state": "retrying", "since": NOW, "error": "timed out"},
            {"remote": "dead", "installed": True, "state": "stopped", "since": None, "error": None},
            {"remote": "none", "installed": False, "state": "off", "since": None, "error": None},
        ]
        got = {c.name: c for c in doctor.check_links(items, now=NOW)}
        self.assertEqual(got["up"].detail, "connected for 1 h")
        self.assertEqual(got["trying"].status, doctor.WARN)
        self.assertEqual(got["dead"].status, doctor.FAIL)
        self.assertNotIn("none", got, "links never set up are not listed")


class RenderTest(unittest.TestCase):
    def test_all_good_counts_what_was_checked(self):
        C = doctor.Check
        out = doctor.render([C(doctor.MACHINE, "mnemo", doctor.OK, "0.4.1 · latest release"),
                             C("Agents", "OpenCode", doctor.SKIP, "not installed")], node_name="lap")
        self.assertEqual(out.splitlines()[0], "mnemo doctor · lap")
        self.assertIn("  This machine", out.splitlines())
        self.assertTrue(out.endswith("→ All good: 1 check passed · 1 skipped"))

    def test_devices_render_as_aligned_columns(self):
        C = doctor.Check
        rows = [C("Devices", "devbox-109", doctor.OK, "d", fields=["174 ms", "same", "off"]),
                C("Devices", "gpu-box/devbox-126", doctor.WARN, "d", "mnemo remote upgrade",
                  fields=["1400 ms", "other", "on"], slow=True),
                C("Devices", "gone", doctor.FAIL, "unreachable: timed out", "ssh gone")]
        lines = doctor.render_group("Devices", rows).splitlines()
        # Column titles sit over the values: name column is as wide as the longest route.
        self.assertEqual(lines[0], "  Devices" + " " * len("gpu-box/devbox-126") + "latency   code    relay")
        self.assertEqual(lines[1], "    ✓ devbox-109           174 ms    same    off")
        self.assertEqual(lines[2], "    ! gpu-box/devbox-126   1400 ms   other   on")
        self.assertEqual(lines[3], "    ✗ gone                 unreachable: timed out")
        colored = doctor.render_group("Devices", rows, doctor.Style(color=True))
        self.assertIn("\033[33m1400 ms", colored, "slow latency is highlighted")
        self.assertIn("\033[33mother", colored)

    def test_counts_and_one_line_per_fix(self):
        C = doctor.Check
        out = doctor.render([
            C("Devices", "a", doctor.WARN, "runs other code", "mnemo remote upgrade"),
            C("Devices", "b", doctor.WARN, "runs other code", "mnemo remote upgrade"),
            C("Links", "c", doctor.FAIL, "not running", "mnemo link c --allow-inbound --install"),
        ], unicode=False)
        lines = out.splitlines()
        self.assertIn("  1 problem - 2 warnings", lines)
        self.assertEqual(lines[-2:], ["  -> mnemo link c --allow-inbound --install", "  -> mnemo remote upgrade"])
        self.assertTrue(all(ord(ch) < 128 for ch in out))


class CliTest(unittest.TestCase):
    def test_a_problem_exits_1_and_json_lists_checks(self):
        with contextlib.redirect_stdout(io.StringIO()):
            demo = DemoHome()
        try:
            blocker = os.path.join(demo.root, "blocker")
            open(blocker, "w").close()
            os.makedirs(os.path.join(demo.home, ".mnemo"), exist_ok=True)
            with open(os.path.join(demo.home, ".mnemo", "remotes.json"), "w") as f:
                json.dump({"remotes": [{"name": "gone", "host": "x", "transport": "local",
                                        "home": os.path.join(blocker, "home")}]}, f)
            env = dict(os.environ, HOME=demo.home)
            subprocess.run(MNEMO + ["index"], env=env, cwd=REPO, check=True, capture_output=True)
            p = subprocess.run(MNEMO + ["doctor", "--offline", "--json"], env=env, cwd=REPO,
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 1, p.stderr)
            checks = {(c["group"], c["name"]): c["status"] for c in json.loads(p.stdout)["checks"]}
            self.assertEqual(checks[("Devices", "gone")], "fail")
            self.assertEqual(checks[("This machine", "index")], "ok")
            text = subprocess.run(MNEMO + ["doctor", "--offline"], env=env, cwd=REPO, capture_output=True, text=True)
            self.assertIn("1 problem", text.stdout)
            self.assertNotIn("\x1b", text.stdout)
            self.assertNotIn("checking", text.stdout, "progress lines only on a terminal")
            self.assertNotIn("\r", text.stdout)
            groups = [ln.strip() for ln in text.stdout.splitlines() if ln.startswith("  ") and not ln.startswith("    ")]
            self.assertEqual([g.split()[0] for g in groups[:3]], ["This", "Agents", "Devices"])
        finally:
            demo.cleanup()


if __name__ == "__main__":
    unittest.main()
