"""Every account's sessions are indexed, and a copy keeps them searchable after the agent deletes them."""
import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from helpers import REPO, DemoHome

from test_mesh import MNEMO

from mnemo import mcp_server, upgrade
from mnemo.index import Index
from mnemo.search import get_context, get_session, raw_context, raw_session, recent, search
from mnemo.sources.claude import ClaudeSource
from mnemo.sources.codex import CodexSource


def claude_line(role, text, sid="s"):
    content = text if role == "user" else [{"type": "text", "text": text}]
    return json.dumps({"type": role, "sessionId": sid, "cwd": "/w", "timestamp": "2026-10-01T08:00:00Z",
                       "message": {"role": role, "content": content}}) + "\n"


def codex_log(sid, text):
    return "".join(json.dumps(r) + "\n" for r in (
        {"type": "session_meta", "timestamp": "2026-10-01T08:00:00Z", "payload": {"id": sid, "cwd": "/w"}},
        {"type": "response_item", "timestamp": "2026-10-01T08:00:01Z",
         "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}},
    ))


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(data)
    return path


@contextlib.contextmanager
def env(**values):
    saved = {k: os.environ.get(k) for k in values}
    os.environ.update({k: v for k, v in values.items() if v is not None})
    for k, v in values.items():
        if v is None:
            os.environ.pop(k, None)
    try:
        yield
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


class ArchiveCase(unittest.TestCase):
    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.demo = DemoHome()
        self.demo.activate()
        self.idx = Index(self.demo.db)

    def tearDown(self):
        self.idx.close()
        self.demo.deactivate()
        self.demo.cleanup()

    def sync(self):
        return self.idx.sync(home=self.demo.home)

    def claude(self, account, name, word, sid=None):
        return write(self.demo.path(account, "projects", "-w", name + ".jsonl"),
                     claude_line("user", "please %s the cache" % word, sid or name)
                     + claude_line("assistant", "%s done" % word, sid or name))

    def copies(self):
        return sorted(os.path.relpath(os.path.join(d, n), self.idx.archive.root)
                      for d, _, names in os.walk(self.idx.archive.root) for n in names if ".jsonl" in n)


class AccountDiscoveryTest(ArchiveCase):
    def test_every_claude_account_is_indexed_and_labelled(self):
        self.claude(".claude-work", "w1", "quokka")
        outside = os.path.join(self.demo.root, "elsewhere", "claude-cfg")
        write(os.path.join(outside, "projects", "-w", "e1.jsonl"), claude_line("user", "the quokka elsewhere", "e1"))
        with env(CLAUDE_CONFIG_DIR=outside):
            self.sync()
            hits = search(self.idx, "quokka")
        self.assertEqual(sorted(h["account"] for h in hits), [".claude-work", ".claude-work", "claude-cfg"])
        self.assertTrue(all(h["archived"] is False for h in hits))
        # Hits from the default dir say so too.
        self.assertEqual({h["account"] for h in search(self.idx, "backoff", sources=["claude"])}, {".claude"})

    def test_codex_accounts_and_a_symlinked_sessions_dir_are_read_once(self):
        os.makedirs(self.demo.path(".codex-work"))
        os.symlink(self.demo.path(".codex", "sessions"), self.demo.path(".codex-work", "sessions"))
        write(self.demo.path(".codex-team", "sessions", "2026", "10", "01", "rollout-team.jsonl"),
              codex_log("team", "wombat migration"))
        codex_home = os.path.join(self.demo.root, "codex-home")
        write(os.path.join(codex_home, "archived_sessions", "rollout-env.jsonl"), codex_log("env", "wombat env"))
        with env(CODEX_HOME=codex_home):
            self.sync()
        files = [r["path"] for r in self.idx.db.execute("SELECT path FROM files WHERE source='codex'")]
        self.assertEqual(len(files), 4, files)  # 2 demo rollouts, once each, + team + $CODEX_HOME
        self.assertFalse(any(".codex-work" in p for p in files))
        self.assertEqual(sorted(h["account"] for h in search(self.idx, "wombat")), [".codex-team", "codex-home"])

    def test_a_symlinked_or_hard_linked_session_file_is_indexed_once(self):
        real = self.claude(".claude-a", "same", "pangolin")
        os.makedirs(self.demo.path(".claude-b", "projects", "-w"))
        os.symlink(real, self.demo.path(".claude-b", "projects", "-w", "alias.jsonl"))
        os.link(real, self.demo.path(".claude-b", "projects", "-w", "hard.jsonl"))
        self.sync()
        hits = search(self.idx, "pangolin")
        self.assertEqual(len(hits), 2)  # user + assistant line of one file
        self.assertEqual({h["path"] for h in hits}, {real})

    def test_env_var_applies_to_own_home_only(self):
        other = tempfile.mkdtemp(prefix="mnemo-other-home-")
        self.addCleanup(shutil.rmtree, other, True)
        outside = os.path.join(self.demo.root, "cfg")
        write(os.path.join(outside, "projects", "-w", "x.jsonl"), claude_line("user", "x"))
        with env(CLAUDE_CONFIG_DIR=outside):
            self.assertIn(outside, ClaudeSource(home=self.demo.home).config_dirs())
            self.assertNotIn(outside, ClaudeSource(home=other).config_dirs())

    def test_account_comes_from_the_path(self):
        source = CodexSource()
        self.assertEqual(source.account("/h/sessions/.codex-x/archived_sessions/r.jsonl"), ".codex-x")
        self.assertEqual(source.account("/h/.codex/sessions/2026/10/01/r.jsonl"), ".codex")
        self.assertEqual(ClaudeSource().account("/p/projects/.claude/projects/-w/s/subagents/a.jsonl"), ".claude")

    def test_a_config_dir_seen_once_stays_indexed_without_its_env_var(self):
        outside = os.path.join(self.demo.root, "cfg", ".claude")  # same name as the default dir
        write(os.path.join(outside, "projects", "-w", "x.jsonl"), claude_line("user", "the narwhal plan", "x"))
        with env(CLAUDE_CONFIG_DIR=outside):
            self.sync()
        stats = self.sync()  # e.g. an MCP server started by Codex, without the variable
        self.assertEqual(stats["files_removed"], 0)
        hits = search(self.idx, "narwhal")
        self.assertEqual([(h["account"], h["archived"]) for h in hits], [(".claude", False)])
        labels = sorted(os.listdir(os.path.join(self.idx.archive.root, "claude")))
        self.assertEqual(len(labels), 2)
        self.assertEqual(labels[0], ".claude")
        self.assertTrue(labels[1].startswith(".claude-"), labels)


class ArchivedSessionTest(ArchiveCase):
    def test_a_deleted_session_stays_searchable_and_readable(self):
        path = self.claude(".claude-work", "w1", "quokka")
        os.makedirs(self.demo.path(".claude-work", "projects", "-w", "w1", "subagents"))
        agent = write(self.demo.path(".claude-work", "projects", "-w", "w1", "subagents", "agent-a1.jsonl"),
                      claude_line("user", "subagent quokka probe", "w1"))
        self.sync()
        before = get_session(self.idx, path)
        context = get_context(self.idx, path, 1)
        raw = raw_session(self.idx, path)

        shutil.rmtree(self.demo.path(".claude-work"))  # what cleanupPeriodDays does
        stats = self.sync()
        self.assertEqual((stats["files_removed"], stats["files_updated"]), (0, 0), "nothing to reparse")
        hits = search(self.idx, "quokka")
        self.assertEqual({h["path"] for h in hits}, {path, agent})
        self.assertTrue(all(h["archived"] for h in hits))
        after = get_session(self.idx, path)
        self.assertTrue(after["archived"])
        self.assertTrue(after["archive_path"].startswith(self.idx.archive.root))
        self.assertEqual(after["messages"], before["messages"])
        self.assertEqual(get_context(self.idx, path, 1), context)
        self.assertEqual(raw_session(self.idx, path)["messages"], raw["messages"])
        self.assertEqual([m["text"] for m in raw_context(self.idx, path, 2)], ["please quokka the cache", "quokka done"])
        self.assertTrue([r for r in recent(self.idx) if r["path"] == path][0]["archived"])

    def test_copies_follow_the_log_and_are_private(self):
        os.umask(0)  # an open umask must not widen the archive
        self.addCleanup(os.umask, 0o022)
        path = self.claude(".claude-work", "w1", "quokka")
        stats = self.sync()
        self.assertEqual(stats["files_archived"], 6)  # 5 demo sessions + this one
        copy = os.path.join(self.idx.archive.root, "claude", ".claude-work", "projects", "-w", "w1.jsonl")
        with open(path, "rb") as a, open(copy, "rb") as b:
            self.assertEqual(a.read(), b.read())
        self.assertEqual(os.stat(copy).st_mtime, os.stat(path).st_mtime)
        for dirpath, dirs, names in os.walk(self.idx.archive.root):
            self.assertEqual(stat.S_IMODE(os.stat(dirpath).st_mode), 0o700, dirpath)
            for n in names:
                self.assertEqual(stat.S_IMODE(os.stat(os.path.join(dirpath, n)).st_mode), 0o600, n)
        with open(os.path.join(self.idx.archive.root, "claude", ".claude-work", "origin.json")) as f:
            self.assertEqual(json.load(f), {"config_dir": self.demo.path(".claude-work")})
        self.assertEqual(self.sync()["files_archived"], 0, "an unchanged log is not copied again")

        with open(path, "a") as f:
            f.write(claude_line("user", "and the walrus too", "w1"))
        os.utime(path, (time.time() + 5, time.time() + 5))
        self.assertEqual(self.sync()["files_archived"], 1)
        os.remove(path)
        self.sync()
        self.assertTrue(search(self.idx, "walrus")[0]["archived"])

    def test_turning_the_archive_off_and_on(self):
        path = self.claude(".claude-work", "w1", "quokka")
        with env(MNEMO_ARCHIVE="off"):
            stats = self.sync()
        self.assertEqual(stats["files_archived"], 0)
        self.assertFalse(os.path.exists(self.idx.archive.root))
        self.sync()
        os.remove(path)
        with env(MNEMO_ARCHIVE="off"):
            self.assertEqual(self.sync()["files_removed"], 1)
        self.assertEqual(search(self.idx, "quokka"), [])
        self.assertTrue(self.copies(), "turning it off deletes nothing")
        self.assertEqual(self.sync()["files_new"], 1)
        self.assertTrue(search(self.idx, "quokka")[0]["archived"])

    def test_an_unreadable_archive_drops_nothing(self):
        path = self.claude(".claude-work", "w1", "quokka")
        self.sync()
        os.remove(path)
        with patch("mnemo.archive.Archive.sync", side_effect=PermissionError("archive unreadable")):
            stats = self.sync()
        self.assertEqual(stats["files_removed"], 0)
        self.assertEqual(len(search(self.idx, "quokka")), 2)
        warnings = json.loads(self.idx.db.execute(
            "SELECT value FROM meta WHERE key = 'sync_warnings:claude'").fetchone()[0])
        self.assertEqual(warnings, ["skip archive for claude: archive unreadable"])

    def test_rebuild_keeps_archived_sessions(self):
        path = self.claude(".claude-work", "w1", "quokka")
        self.sync()
        os.remove(path)
        self.idx.close()
        stats, counts = upgrade.rebuild(self.demo.db, home=self.demo.home)
        self.assertEqual(counts["claude"]["files"], 3)
        self.idx = Index(self.demo.db)
        self.assertEqual(upgrade.verify(self.idx), [])
        self.assertTrue(get_session(self.idx, path)["archived"])

    def test_a_moved_codex_rollout_is_one_session(self):
        live = self.demo.path(".codex", "sessions", "2026", "10", "01", "rollout-moved.jsonl")
        write(live, codex_log("moved", "platypus rollout"))
        self.sync()
        moved = self.demo.path(".codex", "archived_sessions", "rollout-moved.jsonl")
        os.makedirs(os.path.dirname(moved))
        os.rename(live, moved)  # Codex archiving a thread
        self.sync()
        self.assertEqual([h["path"] for h in search(self.idx, "platypus")], [moved])
        self.assertEqual([c for c in self.copies() if "moved" in c],
                         [os.path.join("codex", ".codex", "archived_sessions", "rollout-moved.jsonl")])
        os.remove(moved)
        self.sync()
        hits = search(self.idx, "platypus")
        self.assertEqual([(h["path"], h["archived"]) for h in hits], [(moved, True)])

    def test_a_session_continued_in_another_account_is_kept_once_both_are_gone(self):
        old = self.claude(".claude-a", "same", "ocelot")
        self.sync()
        with open(old) as f:  # how a session resumed under another account starts
            new = write(self.demo.path(".claude-b", "projects", "-w", "same.jsonl"),
                        f.read() + claude_line("user", "ocelot continued", "same"))
        os.utime(new, (time.time() + 5, time.time() + 5))
        self.sync()
        self.assertEqual(len(search(self.idx, "ocelot")), 5, "two live files: each is indexed")
        os.remove(old)
        self.sync()
        self.assertEqual({h["path"] for h in search(self.idx, "ocelot")}, {new}, "the live one carries it on")
        os.remove(new)
        self.sync()
        hits = search(self.idx, "ocelot")
        self.assertEqual({h["path"] for h in hits}, {new}, "the newest copy wins")
        self.assertEqual(len(hits), 3)

    @unittest.skipUnless(shutil.which("zstd"), "optional zstd executable not installed")
    def test_a_compressed_codex_rollout_replaces_its_plain_copy(self):
        path = self.demo.path(".codex", "sessions", "2026", "10", "01", "rollout-z.jsonl")
        write(path, codex_log("z", "dugong compressed"))
        self.sync()
        subprocess.run(["zstd", "-q", "--rm", path], check=True)
        self.sync()
        self.assertEqual([c for c in self.copies() if "rollout-z" in c],
                         [os.path.join("codex", ".codex", "sessions", "2026", "10", "01", "rollout-z.jsonl.zst")])
        os.remove(path + ".zst")
        self.sync()
        self.assertEqual(raw_session(self.idx, path)["messages"][0]["text"], "dugong compressed")

    def test_mcp_reads_an_archived_session(self):
        path = self.claude(".claude-work", "w1", "quokka")
        self.sync()
        os.remove(path)
        self.sync()
        hits = mcp_server.handle_call("search_sessions", {"query": "quokka", "host": "local"},
                                      self.idx)["structuredContent"]["hits"]
        self.assertTrue(all(h["archived"] and h["account"] == ".claude-work" for h in hits))
        sess = json.loads(mcp_server.handle_call("get_session", {"path": path, "raw": True},
                                                 self.idx)["content"][0]["text"])
        self.assertTrue(sess["archived"])
        self.assertEqual(len(sess["messages"]), 2)


class CliTest(unittest.TestCase):
    def test_node_switch_status_and_marked_results(self):
        with contextlib.redirect_stdout(io.StringIO()):
            demo = DemoHome()
        try:
            path = write(os.path.join(demo.home, ".claude-work", "projects", "-w", "w1.jsonl"),
                         claude_line("user", "tune the quokka cache", "w1"))
            run_env = dict(os.environ, HOME=demo.home)

            def mnemo(*argv):
                p = subprocess.run(MNEMO + list(argv), env=run_env, cwd=REPO, capture_output=True, text=True)
                self.assertEqual(p.returncode, 0, p.stderr)
                return p.stdout

            self.assertIn("6 copied to the archive", mnemo("index"))
            os.remove(path)
            text = mnemo("search", "quokka", "--host", "local")
            self.assertIn("[local/claude .claude-work]", text)
            self.assertIn("(archived)", text)
            self.assertIn("archived: the original log is gone", mnemo("session", path))
            self.assertIn("archived: the original log is gone", mnemo("context", path, "1"))
            status = json.loads(mnemo("status", "--json"))["archive"]
            self.assertEqual((status["enabled"], status["files"], status["sessions_archived"]), (True, 6, 1))
            self.assertIn("archive  off", mnemo("node", "--archive", "off"))
            self.assertFalse(json.loads(mnemo("node", "--json"))["archive"])
            self.assertIn("1 removed", mnemo("index"))
            self.assertIn("archive  on", mnemo("node", "--archive", "on"))
            self.assertIn("+1 new", mnemo("index"))
        finally:
            demo.cleanup()


if __name__ == "__main__":
    unittest.main()
