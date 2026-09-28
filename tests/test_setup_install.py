import contextlib
import io
import os
import stat
import subprocess
import sys
import tempfile
import unittest

from helpers import REPO, DemoHome

from mnemo import setup

FAKE_CLAUDE = """#!/bin/sh
# Records calls; `mcp get mnemo` succeeds once `mcp add` has run.
echo "$@" >> "$FAKE_CLAUDE_LOG"
if [ "$1 $2" = "mcp get" ]; then [ -f "$FAKE_CLAUDE_LOG.added" ]; exit $?; fi
if [ "$1 $2" = "mcp add" ]; then touch "$FAKE_CLAUDE_LOG.added"; fi
exit 0
"""


class AgentHome:
    """A throwaway HOME with a fake `claude` CLI first on PATH."""

    def __init__(self, agents=("claude", "codex", "pi")):
        self.root = tempfile.mkdtemp(prefix="mnemo-setup-")
        self.home = os.path.join(self.root, "home")
        self.bin = os.path.join(self.root, "bin")
        os.makedirs(self.bin)
        self.log = os.path.join(self.root, "claude.log")
        if "claude" in agents:
            os.makedirs(os.path.join(self.home, ".claude"))
            path = os.path.join(self.bin, "claude")
            with open(path, "w") as f:
                f.write(FAKE_CLAUDE)
            os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        if "codex" in agents:
            os.makedirs(os.path.join(self.home, ".codex"))
        if "pi" in agents:
            os.makedirs(os.path.join(self.home, ".pi", "agent"))
        self._saved = {k: os.environ.get(k) for k in ("HOME", "PATH", "FAKE_CLAUDE_LOG")}

    def __enter__(self):
        os.environ["HOME"] = self.home
        os.environ["PATH"] = self.bin + os.pathsep + "/usr/bin:/bin"
        os.environ["FAKE_CLAUDE_LOG"] = self.log
        return self

    def __exit__(self, *exc):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        subprocess.run(["rm", "-rf", self.root])

    def path(self, *parts):
        return os.path.join(self.home, *parts)

    def claude_calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return f.read().splitlines()


def statuses(steps):
    return {(s.agent, s.action): s.status for s in steps}


class SetupTest(unittest.TestCase):
    def test_connects_every_detected_agent_then_is_idempotent(self):
        with AgentHome() as h:
            codex_cfg = h.path(".codex", "config.toml")
            with open(codex_cfg, "w") as f:
                f.write('model = "gpt-5"\n')

            first = statuses(setup.run())
            self.assertEqual(first, {
                ("claude", "MCP server"): "added",
                ("claude", "skill"): "added",
                ("codex", "MCP server"): "added",
                ("pi", "extension"): "added",
            })
            self.assertTrue(any(c.startswith("mcp add --scope user mnemo -- ") and c.endswith(" mcp") for c in h.claude_calls()))
            self.assertEqual(os.path.realpath(h.path(".claude", "skills", "mnemo")), os.path.realpath(setup.SKILL_SRC))
            self.assertEqual(os.path.realpath(h.path(".pi", "agent", "extensions", "mnemo.ts")), os.path.realpath(setup.PI_SRC))
            with open(codex_cfg) as f:
                cfg = f.read()
            self.assertTrue(cfg.startswith('model = "gpt-5"\n'), "existing config is preserved")
            self.assertIn("[mcp_servers.mnemo]", cfg)
            self.assertIn('args = ["mcp"]', cfg)
            self.assertEqual(len([p for p in os.listdir(h.path(".codex")) if ".bak-" in p]), 1)

            second = statuses(setup.run())
            self.assertEqual(set(second.values()), {"ok"}, second)
            with open(codex_cfg) as f:
                self.assertEqual(f.read().count("[mcp_servers.mnemo]"), 1)

    def test_dry_run_changes_nothing(self):
        with AgentHome() as h:
            steps = setup.run(dry_run=True)
            self.assertIn("added", {s.status for s in steps})
            self.assertFalse(os.path.exists(h.path(".claude", "skills")))
            self.assertFalse(os.path.exists(h.path(".codex", "config.toml")))
            self.assertFalse(any(c.startswith("mcp add") for c in h.claude_calls()))

    def test_repairs_stale_links_but_never_replaces_real_files(self):
        with AgentHome(agents=("claude", "pi")) as h:
            os.makedirs(h.path(".claude", "skills"))
            os.symlink("/nonexistent/agentsearch/skill", h.path(".claude", "skills", "mnemo"))
            ext = h.path(".pi", "agent", "extensions", "mnemo.ts")
            os.makedirs(os.path.dirname(ext))
            with open(ext, "w") as f:
                f.write("// hand-written\n")
            result = statuses(setup.run())
            self.assertEqual(result[("claude", "skill")], "fixed")
            self.assertEqual(os.path.realpath(h.path(".claude", "skills", "mnemo")), os.path.realpath(setup.SKILL_SRC))
            self.assertEqual(result[("pi", "extension")], "skipped")
            with open(ext) as f:
                self.assertEqual(f.read(), "// hand-written\n")

    def test_only_detected_or_requested_agents(self):
        with AgentHome(agents=("pi",)) as h:
            result = statuses(setup.run())
            self.assertEqual(result[("claude", "detect")], "skipped")
            self.assertEqual(result[("codex", "detect")], "skipped")
            self.assertEqual(result[("pi", "extension")], "added")
            only = statuses(setup.run(agents=["codex"]))
            self.assertEqual(list(only), [("codex", "MCP server")])
            self.assertTrue(os.path.isfile(h.path(".codex", "config.toml")))


class InstallScriptTest(unittest.TestCase):
    """scripts/install.sh end to end, cloning this repository's HEAD."""

    def _source_repo(self, root):
        """HEAD of this checkout on a real branch, like the GitHub repo users clone.

        CI checks out a detached merge commit; cloning that directly leaves the
        install without an upstream to pull from.
        """
        src = os.path.join(root, "source")
        git = lambda *a: subprocess.run(["git", "-C", src] + list(a), check=True, capture_output=True)
        subprocess.run(["git", "init", "-q", src], check=True)
        git("fetch", "-q", REPO, "HEAD")
        git("checkout", "-q", "-b", "main", "FETCH_HEAD")
        return src

    def test_install_then_upgrade(self):
        with contextlib.redirect_stdout(io.StringIO()):
            demo = DemoHome()
        try:
            with AgentHome(agents=("claude",)) as h:
                env = dict(
                    os.environ,
                    HOME=demo.home,
                    MNEMO_DIR=os.path.join(demo.root, "mnemo"),
                    MNEMO_BIN_DIR=os.path.join(demo.root, "localbin"),
                    MNEMO_REPO=self._source_repo(demo.root),
                    PYTHON=sys.executable,  # the interpreter under test, not whatever is on PATH
                    PATH=h.bin + os.pathsep + "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
                )
                os.makedirs(os.path.join(demo.home, ".claude"), exist_ok=True)
                script = os.path.join(REPO, "scripts", "install.sh")
                run = lambda: subprocess.run(["sh", script], env=env, capture_output=True, text=True, timeout=300)

                first = run()
                self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
                self.assertIn("building the index", first.stdout)
                self.assertIn("is not on PATH", first.stderr)
                link = os.path.join(env["MNEMO_BIN_DIR"], "mnemo")
                self.assertTrue(os.path.islink(link))
                self.assertTrue(os.path.isfile(os.path.join(demo.home, ".mnemo", "index.db")))
                self.assertTrue(any(c.startswith("mcp add") for c in h.claude_calls()))

                second = run()
                self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
                self.assertIn("updating", second.stdout)
                self.assertIn("verified and swapped in: 5 sessions, 45 messages", second.stdout)
                self.assertTrue(os.listdir(os.path.join(demo.home, ".mnemo", "backups")))
        finally:
            demo.cleanup()

    def test_refuses_to_overwrite_a_foreign_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "mnemo")
            os.makedirs(target)
            env = dict(os.environ, HOME=tmp, MNEMO_DIR=target, MNEMO_REPO=REPO, MNEMO_NO_SETUP="1",
                       PYTHON=sys.executable)
            r = subprocess.run(["sh", os.path.join(REPO, "scripts", "install.sh")], env=env,
                               capture_output=True, text=True, timeout=60)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("not a mnemo checkout", r.stderr)


if __name__ == "__main__":
    unittest.main()
