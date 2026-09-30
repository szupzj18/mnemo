"""CLI output hygiene (no escape codes off a terminal) and session titles free of injected text."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from helpers import REPO

from test_mesh import MNEMO

from mnemo import cli
from mnemo.index import Index
from mnemo.search import recent


class FakeStream:
    def __init__(self, tty):
        self.tty = tty

    def isatty(self):
        return self.tty


class ColorTest(unittest.TestCase):
    def test_color_only_on_a_terminal_without_no_color(self):
        saved = {k: os.environ.pop(k, None) for k in ("NO_COLOR", "TERM")}
        try:
            self.assertTrue(cli.color_enabled(FakeStream(True)))
            self.assertFalse(cli.color_enabled(FakeStream(False)))
            os.environ["NO_COLOR"] = "1"
            self.assertFalse(cli.color_enabled(FakeStream(True)))
            del os.environ["NO_COLOR"]
            os.environ["TERM"] = "dumb"
            self.assertFalse(cli.color_enabled(FakeStream(True)))
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v


def claude_line(role, text, **extra):
    d = {"type": role, "sessionId": "s", "cwd": "/w", "timestamp": "2026-09-30T08:00:00Z",
         "message": {"role": role, "content": text if role == "user" else [{"type": "text", "text": text}]}}
    d.update(extra)
    return json.dumps(d) + "\n"


class SessionTitleTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mnemo-titles-")
        self.home = os.path.join(self.root, "home")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, rel, lines):
        path = os.path.join(self.home, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.writelines(lines)

    def index(self):
        idx = Index(os.path.join(self.root, "index.db"))
        idx.sync(home=self.home)
        return idx

    def test_skill_body_is_not_the_title_but_is_kept(self):
        self.write(".claude/projects/-w/skill.jsonl", [
            claude_line("user", "<command-message>disk-scan</command-message>\n<command-name>/disk-scan</command-name>"),
            claude_line("user", "Base directory for this skill: /u/.claude/skills/disk-scan\n\n# disk-scan\nscan the disk",
                        isMeta=True),
            claude_line("user", "why is my disk full?"),
            claude_line("assistant", "Looking at ~/Library first."),
        ])
        # /loop prompts are isMeta too, but they are the session's task.
        self.write(".claude/projects/-w/loop.jsonl", [
            claude_line("user", "Run one round of the contribution loop", isMeta=True),
            claude_line("assistant", "Round done."),
        ])
        idx = self.index()
        try:
            titles = {r["path"].rsplit("/", 1)[1]: r["title"] for r in recent(idx, limit=10)}
            self.assertEqual(titles, {"skill.jsonl": "why is my disk full?",
                                      "loop.jsonl": "Run one round of the contribution loop"})
            row = idx.db.execute("SELECT text, body, envelope FROM messages WHERE body LIKE 'Base directory%'").fetchone()
            self.assertEqual((row["text"], row["envelope"]), ("", 1), "hidden from search, kept verbatim")
            self.assertIn("scan the disk", row["body"])
        finally:
            idx.close()

    def test_codex_approval_reviews_are_not_listed_as_sessions(self):
        def item(role, text):
            return json.dumps({"type": "response_item", "timestamp": "2026-09-30T08:00:00Z", "payload": {
                "type": "message", "role": role, "content": [{"type": "input_text", "text": text}]}}) + "\n"

        meta = {"type": "session_meta", "timestamp": "2026-09-30T08:00:00Z",
                "payload": {"id": "g", "cwd": "/w", "source": {"subagent": {"other": "guardian"}}}}
        self.write(".codex/sessions/2026/09/30/rollout-guardian.jsonl", [
            json.dumps(meta) + "\n",
            item("user", "The following is the Codex agent history added since your last approval assessment."
                         " <guardian_tool_descriptions>Untrusted descriptions</guardian_tool_descriptions>"),
            item("assistant", "Risk: low. Approve."),
        ])
        normal = dict(meta, payload={"id": "n", "cwd": "/w", "source": "cli"})
        self.write(".codex/sessions/2026/09/30/rollout-normal.jsonl", [
            json.dumps(normal) + "\n",
            item("user", "<guardian_tool_descriptions>x</guardian_tool_descriptions>\nrename the build step"),
        ])
        idx = self.index()
        try:
            self.assertEqual([r["title"] for r in recent(idx, limit=10)], ["rename the build step"])
            hits = idx.db.execute("SELECT COUNT(*) FROM messages WHERE text LIKE '%Approve%'").fetchone()[0]
            self.assertEqual(hits, 1, "the reviewer's own reply stays searchable")
        finally:
            idx.close()


class PipedOutputTest(unittest.TestCase):
    def test_no_escape_codes_when_piped(self):
        root = tempfile.mkdtemp(prefix="mnemo-pipe-")
        try:
            home = os.path.join(root, "home")
            os.makedirs(os.path.join(home, ".claude", "projects", "-w"))
            with open(os.path.join(home, ".claude", "projects", "-w", "s.jsonl"), "w") as f:
                f.write(claude_line("user", "tune the quokka cache"))
                f.write(claude_line("assistant", "quokka cache tuned"))
            env = dict(os.environ, HOME=home)
            env.pop("NO_COLOR", None)
            for argv in (["search", "quokka", "--host", "local"], ["recent"], ["status"], ["node"]):
                with self.subTest(argv[0]):
                    p = subprocess.run(MNEMO + argv, env=env, cwd=REPO, capture_output=True, text=True)
                    self.assertEqual(p.returncode, 0, p.stderr)
                    self.assertTrue(p.stdout.strip())
                    self.assertNotIn("\x1b", p.stdout)
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
