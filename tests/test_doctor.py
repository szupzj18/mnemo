"""mnemo doctor: each check's verdicts, the report, and the CLI exit code."""
import contextlib
import io
import json
import os
import subprocess
import unittest

from helpers import REPO, DemoHome

from test_mesh import MNEMO

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

    def tearDown(self):
        self.demo.cleanup()

    def test_missing_index_says_how_to_build_it(self):
        [c] = doctor.check_index(os.path.join(self.demo.root, "nope.db"))
        self.assertEqual((c.status, c.fix), (doctor.FAIL, "mnemo index"))

    def test_healthy_index_and_full_check(self):
        idx = Index(self.demo.db)
        idx.sync(home=self.demo.home)
        idx.close()
        checks = doctor.check_index(self.demo.db, full=True, now=NOW)
        self.assertEqual([(c.name, c.status) for c in checks], [("index", doctor.OK), ("integrity", doctor.OK)])
        self.assertIn("5 sessions", checks[0].detail)

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
