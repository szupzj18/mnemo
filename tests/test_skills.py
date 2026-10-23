"""Skills: the per-agent use rules, skill_events in the index and its v3 migration, the
folder inventory (links, dangling links, differing copies) and the stats views."""
import contextlib
import datetime
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from helpers import REPO, DemoHome

from mnemo import link, mcp_server, skills, upgrade
from mnemo.index import SCHEMA_VERSION, Index

TODAY = datetime.date(2026, 10, 23)


def write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def claude_session(home, sid, steps, day="2026-10-20"):
    rows = []
    for i, (kind, payload) in enumerate(steps):
        base = {"sessionId": sid, "cwd": "/w", "timestamp": "%sT10:00:%02d.000Z" % (day, i)}
        if kind == "user":
            rows.append(dict(base, type="user", message={"role": "user", "content": payload}))
        elif kind == "call":
            rows.append(dict(base, type="assistant", message={"role": "assistant", "content": [
                {"type": "tool_use", "id": "t%d" % i, "name": payload[0], "input": payload[1]}]}))
        elif kind == "result":
            rows.append(dict(base, type="user", message={"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t", "content": payload}]}))
    write_jsonl(os.path.join(home, ".claude", "projects", "-w", sid + ".jsonl"), rows)


def codex_session(home, sid, steps, day="2026-10-21"):
    rows = [{"type": "session_meta", "timestamp": "%sT09:00:00.000Z" % day, "payload": {"id": sid, "cwd": "/w"}}]
    for i, (kind, payload) in enumerate(steps):
        ts = "%sT09:00:%02d.000Z" % (day, i + 1)
        if kind == "user":
            pl = {"type": "message", "role": "user", "content": [{"type": "input_text", "text": payload}]}
        else:
            pl = {"type": "function_call", "name": "exec_command", "arguments": json.dumps(payload)}
        rows.append({"type": "response_item", "timestamp": ts, "payload": pl})
    write_jsonl(os.path.join(home, ".codex", "sessions", "2026", "10", "21", "rollout-%s.jsonl" % sid), rows)


SKILL_LOAD = "Base directory for this skill: /h/.claude/skills/%s\n\n# Body"
SLASH = "<command-name>/%s</command-name>\n<command-message>%s</command-message>\n<command-args></command-args>"


class EventRulesTest(unittest.TestCase):
    def test_claude_skill_tool_slash_load_and_reads(self):
        ev = skills.events
        self.assertEqual(ev("claude", "assistant", "tool_call", 'Skill({"skill": "html-report"})'), [("html-report", "tool")])
        self.assertEqual(ev("claude", "assistant", "tool_call", 'Skill({"skill": "anthropic-skills:pdf", "args": "x"})'),
                         [("pdf", "tool")], "plugin skills count under their own name")
        self.assertEqual(ev("claude", "user", "text", SLASH % ("disk-scan", "disk-scan")), [("disk-scan", "slash")])
        self.assertEqual(ev("claude", "user", "text", SKILL_LOAD % "disk-scan"), [("disk-scan", "load")])
        self.assertEqual(ev("claude", "assistant", "tool_call", 'Read({"file_path": "/h/dev/s/skills/read-pr/SKILL.md"})'),
                         [("read-pr", "read")])
        self.assertEqual(ev("claude", "assistant", "tool_call", 'Bash({"command": "cat ~/s/skills/say-it/SKILL.md; ls"})'),
                         [("say-it", "read")])

    def test_codex_dollar_injection_and_both_shell_forms(self):
        ev = skills.events
        body = "<skill>\n<name>read-pr</name>\n<path>/h/.agents/skills/read-pr/SKILL.md</path>\n---\n</skill>" \
               "<skill>\n<name>writing-style</name>\n</skill>"
        self.assertEqual(ev("codex", "user", "text", body), [("read-pr", "user"), ("writing-style", "user")])
        self.assertEqual(ev("codex", "assistant", "tool_call",
                            'exec_command(["/bin/zsh", "-lc", "cat /h/.agents/skills/bytedcli/SKILL.md"])'),
                         [("bytedcli", "read")])
        self.assertEqual(ev("codex", "assistant", "tool_call",
                            'exec_command({"cmd": "sed -n 1,80p /h/.agents/skills/bytedcli/SKILL.md"})'),
                         [("bytedcli", "read")])

    def test_pi_agy_grok_opencode(self):
        ev = skills.events
        self.assertEqual(ev("pi", "user", "text", '<skill name="bytedcli" location="/h/.agents/skills/bytedcli/SKILL.md">'),
                         [("bytedcli", "user")])
        self.assertEqual(ev("pi", "assistant", "tool_call", 'read({"path": "/h/.pi/agent/skills/lark-doc/SKILL.md"})'),
                         [("lark-doc", "read")])
        self.assertEqual(ev("agy", "assistant", "tool_call",
                            'view_file({"AbsolutePath":"/h/.agents/skills/lark-doc/SKILL.md","toolAction":"x"})'),
                         [("lark-doc", "read")])
        self.assertEqual(ev("grok", "assistant", "tool_call", 'read_file({"target_file": "/h/.grok/skills/commit/SKILL.md"})'),
                         [("commit", "read")])
        self.assertEqual(ev("opencode", "assistant", "tool_call", 'skill({"name": "x", "path": "/h/.agents/skills/x/SKILL.md"})'),
                         [("x", "read")])

    def test_mentions_that_are_not_uses(self):
        ev = skills.events
        self.assertEqual(ev("claude", "assistant", "text", "I read skills/x/SKILL.md"), [])
        self.assertEqual(ev("claude", "assistant", "tool_call", 'Bash({"command": "rm ~/s/skills/x/SKILL.md"})'), [])
        self.assertEqual(ev("claude", "assistant", "tool_call", 'Write({"file_path": "/s/skills/x/SKILL.md"})'), [])
        self.assertEqual(ev("codex", "tool", "tool_result", "<skill><name>x</name></skill>"), [])
        self.assertEqual(ev("codex", "assistant", "tool_call",
                            'exec_command(["/bin/zsh", "-lc", "git add skills/x/SKILL.md"])'), [])
        self.assertEqual(ev("claude", "assistant", "tool_call", "Skill(not json)"), [])


class IndexEventsTest(unittest.TestCase):
    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.demo = DemoHome()
        self.demo.activate()
        home = self.demo.home
        claude_session(home, "c1", [
            ("user", "make the report page"),
            ("call", ("Skill", {"skill": "html-report"})),
            ("result", "Launching skill: html-report"),
            ("user", [{"type": "text", "text": SKILL_LOAD % "html-report"}]),
            ("call", ("Bash", {"command": "cat ~/dev/s/skills/say-it-plainly/SKILL.md"})),
        ])
        claude_session(home, "c2", [
            ("user", SLASH % ("disk-scan", "disk-scan")),
            ("user", [{"type": "text", "text": SKILL_LOAD % "disk-scan"}]),
            ("user", SLASH % ("login", "login")),
        ], day="2026-09-01")
        codex_session(home, "x1", [
            ("user", "<skill>\n<name>read-pr</name>\n<path>/h/.agents/skills/read-pr/SKILL.md</path>\n</skill>\n$read-pr 4521"),
            ("call", ["/bin/zsh", "-lc", "cat /h/.agents/skills/bytedcli/SKILL.md"]),
            ("call", {"cmd": "sed -n 1,40p /h/.agents/skills/bytedcli/SKILL.md"}),
        ])
        self.idx = Index(self.demo.db)
        self.idx.sync(home=home)

    def tearDown(self):
        self.idx.close()
        self.demo.deactivate()
        self.demo.cleanup()

    def events(self, idx=None):
        return sorted(tuple(r) for r in (idx or self.idx).db.execute(
            "SELECT msg, path, source, skill, trigger, ts FROM skill_events"))

    def test_sync_records_uses_with_their_message(self):
        got = {(r[2], r[3], r[4]) for r in self.events()}
        self.assertEqual(got, {
            ("claude", "html-report", "tool"), ("claude", "html-report", "load"), ("claude", "say-it-plainly", "read"),
            ("claude", "disk-scan", "slash"), ("claude", "disk-scan", "load"), ("claude", "login", "slash"),
            ("codex", "read-pr", "user"), ("codex", "bytedcli", "read"),
        })
        for msg, path, *_ in self.events():
            row = self.idx.db.execute("SELECT path FROM messages WHERE rowid = ?", (msg,)).fetchone()
            self.assertEqual(row[0], path, "msg points at the message the use came from")

    def test_events_follow_their_file(self):
        path = self.idx.db.execute("SELECT path FROM skill_events WHERE source='codex' LIMIT 1").fetchone()[0]
        with open(path, "a") as f:
            f.write(json.dumps({"type": "response_item", "timestamp": "2026-10-21T09:10:00.000Z", "payload": {
                "type": "function_call", "name": "exec_command",
                "arguments": json.dumps({"cmd": "cat /h/.agents/skills/bytedcli/SKILL.md"})}}) + "\n")
        self.idx.sync(home=self.demo.home)
        self.assertEqual(self.idx.db.execute(
            "SELECT COUNT(*) FROM skill_events WHERE skill='bytedcli'").fetchone()[0], 3, "reparsed once, not doubled")
        os.remove(path)
        self.idx.sync(home=self.demo.home)
        self.assertEqual(self.idx.db.execute("SELECT COUNT(*) FROM skill_events WHERE source='codex'").fetchone()[0], 4,
                         "the archive keeps a deleted session, and its uses with it")
        os.environ["MNEMO_ARCHIVE"] = "off"
        try:
            self.idx.sync(home=self.demo.home)
        finally:
            del os.environ["MNEMO_ARCHIVE"]
        self.assertEqual(self.idx.db.execute("SELECT COUNT(*) FROM skill_events WHERE source='codex'").fetchone()[0], 0)

    def _make_v3(self):
        self.idx.db.execute("DROP TABLE skill_events")
        self.idx.db.execute("UPDATE meta SET value = '3' WHERE key = 'schema_version'")
        self.idx.close()

    def test_migration_finds_what_indexing_finds_and_is_idempotent(self):
        before = self.events()
        messages = sum(c["messages"] for c in self.idx.counts().values())
        self._make_v3()
        self.idx = Index(self.demo.db)
        self.assertTrue(self.idx._migration_pending)
        self.assertIsNone(self.idx.db.execute("SELECT 1 FROM sqlite_master WHERE name='skill_events'").fetchone(),
                          "opening a v3 index must not write to it")
        self.idx.sync(home=self.demo.home)
        self.assertEqual(self.idx.stored_version, SCHEMA_VERSION)
        self.assertEqual(self.events(), before)
        self.assertEqual(self.idx.migrate(), len(before))
        self.assertEqual(self.events(), before, "a second migration changes nothing")
        stats = self.idx.sync(home=self.demo.home)
        self.assertEqual((stats["files_new"], stats["files_updated"]), (0, 0), "no reparse")
        self.assertEqual(sum(c["messages"] for c in self.idx.counts().values()), messages)

    def test_upgrade_migrates_a_copy_and_swaps_it_in(self):
        before = self.events()
        self._make_v3()
        self.assertTrue(upgrade.migratable(self.demo.db))
        uses, counts = upgrade.migrate(self.demo.db)
        self.assertEqual(uses, len(before))
        self.idx = Index(self.demo.db)
        self.assertEqual(self.idx.stored_version, SCHEMA_VERSION)
        self.assertEqual(self.events(), before)
        self.assertEqual([p for p in os.listdir(self.demo.root) if ".migrate-" in p], [])

    def test_cli_upgrade_says_it_migrates(self):
        uses = len(self.events())
        self._make_v3()
        env = dict(os.environ, HOME=self.demo.home)
        r = subprocess.run([sys.executable, os.path.join(REPO, "bin", "mnemo"), "--db", self.demo.db,
                            "upgrade", "--no-remotes"], capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("migrating schema v3 to v%s" % SCHEMA_VERSION, r.stdout)
        self.assertIn("(%d skill uses found)" % uses, r.stdout)
        self.idx = Index(self.demo.db)

    def test_usage_counts_windows_triggers_and_skips_commands(self):
        rows = {(r["agent"], r["skill"]): r for r in skills.usage(self.idx.db, datetime.date(2026, 10, 1))}
        self.assertNotIn(("claude", "login"), rows, "/login is a command, not a skill")
        disk = rows[("claude", "disk-scan")]
        self.assertEqual((disk["calls"], disk["total"], disk["user"], disk["last"]), (0, 1, 0, "2026-09-01"),
                         "a load proves /disk-scan is a skill; it is outside the window")
        html = rows[("claude", "html-report")]
        self.assertEqual((html["calls"], html["sessions"], html["tool"], html["user"]), (1, 1, 1, 0),
                         "the load after a Skill call is not a second use")
        self.assertEqual(rows[("codex", "bytedcli")]["read"], 2)
        self.assertEqual(rows[("codex", "read-pr")]["user"], 1)
        rows = {(r["agent"], r["skill"]): r for r in skills.usage(self.idx.db, datetime.date(2026, 8, 1))}
        self.assertEqual(rows[("claude", "disk-scan")]["user"], 1)

    def test_cli_stats_json(self):
        env = dict(os.environ, HOME=self.demo.home)
        r = subprocess.run([sys.executable, os.path.join(REPO, "bin", "mnemo"), "--db", self.demo.db, "skill", "stats",
                            "--host", "local", "--no-sync", "--today", "2026-10-23", "--by", "agent", "--json"],
                           capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["first_day"], "2026-09-24")
        self.assertEqual(out["devices"], ["local"])
        self.assertIn({"agent": "codex", "skill": "bytedcli", "calls": 2, "sessions": 1, "user": 0, "tool": 0,
                       "read": 2, "total": 2, "total_sessions": 1, "last": "2026-10-21"}, out["rows"])

    def test_mcp_skill_usage(self):
        result = mcp_server.handle_call("skill_usage", {"host": "local", "since": "2026-08-01"}, self.idx)
        rows = json.loads(result["content"][0]["text"])["rows"]
        self.assertIn(("claude", "disk-scan"), {(r["agent"], r["skill"]) for r in rows})
        names = {t["name"] for t in mcp_server.build_tools()}
        self.assertTrue({"list_skills", "skill_usage"} <= names)


class InventoryTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mnemo-skills-")
        self.home = os.path.join(self.root, "home")
        self.repo = os.path.join(self.root, "repo")
        self.saved = os.environ.get("HOME")
        os.environ["HOME"] = self.home
        for name, body in (("read-pr", "pr"), ("say-it", "plain")):
            self._skill(os.path.join(self.repo, "skills", name), name, body)
        subprocess.run(["git", "init", "-q", self.repo], check=True)
        subprocess.run(["git", "-C", self.repo, "add", "."], check=True)
        claude = os.path.join(self.home, ".claude", "skills")
        agents = os.path.join(self.home, ".agents", "skills")
        os.makedirs(claude)
        os.makedirs(agents)
        os.symlink(os.path.join(self.repo, "skills", "read-pr"), os.path.join(claude, "read-pr"))
        os.symlink(os.path.join(self.repo, "skills", "read-pr"), os.path.join(agents, "read-pr"))
        os.symlink(os.path.join(self.root, "gone"), os.path.join(agents, "old"))
        self._skill(os.path.join(claude, "say-it"), "say-it", "plain")
        self._skill(os.path.join(agents, "say-it"), "say-it", "changed by hand")
        os.makedirs(os.path.join(claude, "synced", "org_acct", "pdf"))
        os.makedirs(os.path.join(self.home, ".claude-work"))
        os.symlink(claude, os.path.join(self.home, ".claude-work", "skills"))

    def tearDown(self):
        os.environ["HOME"] = self.saved
        shutil.rmtree(self.root, ignore_errors=True)
        skills._TRACKED.clear()

    @staticmethod
    def _skill(folder, name, body):
        os.makedirs(folder)
        with open(os.path.join(folder, "SKILL.md"), "w") as f:
            f.write("---\nname: %s\ndescription: >\n  Use when reviewing %s\n  changes.\n---\n%s\n" % (name, name, body))

    def test_links_dangling_copies_and_managed_folders(self):
        inv = skills.inventory(self.home)
        dirs = {d["account"]: d for d in inv["dirs"]}
        self.assertEqual(dirs[".claude"]["also"], [".claude-work"], "a linked account folder is listed once")
        self.assertEqual(dirs[".claude"]["managed"], ["synced"], "what Claude Code syncs itself is left alone")
        kinds = {(s["account"], s["name"]): s["kind"] for s in inv["skills"]}
        self.assertEqual(kinds, {(".claude", "read-pr"): "link", (".claude", "say-it"): "dir",
                                 (".agents", "read-pr"): "link", (".agents", "old"): "dangling",
                                 (".agents", "say-it"): "dir"})
        desc = next(s["desc"] for s in inv["skills"] if s["name"] == "read-pr")
        self.assertEqual(desc, "Use when reviewing read-pr changes.")
        found = {(i["skill"], i["issue"]) for i in skills.issues({"local": inv})}
        self.assertEqual(found, {("old", "dangling"), ("say-it", "forked")})

    def test_hash_ignores_files_git_does_not_track(self):
        folder = os.path.join(self.repo, "skills", "read-pr")
        before = skills.tree_hash(folder)
        with open(os.path.join(folder, "cron.log"), "w") as f:
            f.write("runtime output")
        skills._TRACKED.clear()
        self.assertEqual(skills.tree_hash(folder), before)
        with open(os.path.join(folder, "SKILL.md"), "a") as f:
            f.write("edit")
        self.assertNotEqual(skills.tree_hash(folder), before)

    def test_search_matches_every_word(self):
        devices = {"local": skills.inventory(self.home, hashes=False)}
        self.assertEqual([h["skill"] for h in skills.search(devices, "reviewing changes")], ["read-pr", "say-it"])
        self.assertEqual([h["skill"] for h in skills.search(devices, "read-pr")], ["read-pr"])
        self.assertEqual(skills.search(devices, "nothing-like-this"), [])

    def test_skill_ls_cli_and_doctor_never_write(self):
        # Only the skill folders: git on macOS may create ~/Library/Caches in a fresh HOME.
        walk = lambda: sorted(w for d in (".claude", ".claude-work", ".agents") for w in os.walk(os.path.join(self.home, d)))
        before = walk()
        env = dict(os.environ, HOME=self.home)
        r = subprocess.run([sys.executable, os.path.join(REPO, "bin", "mnemo"), "--db", os.path.join(self.root, "i.db"),
                            "skill", "ls", "--json"], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual({(i["skill"], i["issue"]) for i in json.loads(r.stdout)["issues"]},
                         {("old", "dangling"), ("say-it", "forked")})
        from mnemo import doctor
        check = doctor.check_skills(skills.inventory(self.home))
        self.assertEqual(check.status, doctor.WARN)
        self.assertIn("dangling: old", check.detail)
        self.assertEqual(walk(), before)


class ReportTest(unittest.TestCase):
    @staticmethod
    def row(agent, skill, calls, sessions, last="2026-10-20"):
        return {"agent": agent, "skill": skill, "calls": calls, "sessions": sessions, "user": 0, "tool": 0,
                "read": calls, "total": calls, "total_sessions": sessions, "last": last}

    def test_group_unused_and_gaps(self):
        devices = {
            "local": {"first_day": "2026-09-24", "installed": ["read-pr", "say-it", "idle"],
                      "rows": [self.row("codex", "read-pr", 9, 5), self.row("claude", "read-pr", 2, 2),
                               self.row("claude", "say-it", 4, 4), self.row("claude", "plugin-only", 8, 8)]},
            "devbox": {"first_day": "2026-09-24", "installed": ["read-pr"],
                       "rows": [self.row("claude", "say-it", 1, 1, last="2026-10-22")]},
        }
        out = skills.report(devices, ["device"], "30d", True, True)
        first = out["rows"][0]
        self.assertEqual((first["device"], first["skill"], first["calls"], first["sessions"]), ("local", "read-pr", 11, 7))
        self.assertEqual(out["unused"], ["idle"])
        self.assertEqual(out["gaps"], [], "say-it was used on the devbox, so it has it; plugin-only is no folder")
        devices["devbox"]["rows"] = []
        gaps = skills.gaps(devices)
        self.assertEqual([(g["skill"], g["to"], g["sessions"]) for g in gaps], [("say-it", "devbox", 4)])

    def test_since(self):
        self.assertEqual(skills.parse_since("30d", TODAY), datetime.date(2026, 9, 24))
        self.assertEqual(skills.parse_since("2026-10-01", TODAY), datetime.date(2026, 10, 1))
        with self.assertRaises(ValueError):
            skills.parse_since("last month", TODAY)


class LinkPolicyTest(unittest.TestCase):
    def test_only_read_only_skill_views_for_this_device(self):
        self.assertTrue(link.allowed(["skill", "stats", "--since", "30d", "--json", "--host", "local"]))
        self.assertTrue(link.allowed(["skill", "ls", "--json", "--host", "local"]))
        self.assertFalse(link.allowed(["skill", "stats", "--json", "--host", "all"]), "no fan-out through the link")
        self.assertFalse(link.allowed(["skill", "stats", "--json"]))
        self.assertFalse(link.allowed(["skill", "sync", "--json", "--host", "local"]))
        self.assertFalse(link.allowed(["skill", "ls", "--json", "--host", "local", "--db", "/x"]))


if __name__ == "__main__":
    unittest.main()
