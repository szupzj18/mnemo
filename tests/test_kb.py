"""Knowledge cards: off by default, indexed and searched first when on, and the digest ledger."""
import json
import os
import unittest
from unittest.mock import patch

from helpers import DemoHome

from mnemo import kb
from mnemo.index import Index
from mnemo.remote import fan_out_search
from mnemo.search import DEFAULT_KINDS, recent, search

CARD = """---
type: gotcha
scope:
- cwd:/work/relay
sources:
- claude@demo:/home/demo/.claude/projects/x/s.jsonl#12
created: 2026-11-20
verified: 2026-11-25
---
# Backoff jitter can exceed the cap

Clamp after adding jitter: `min(cap, random.uniform(0, base * 2 ** attempt))`.
"""


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class CardParsing(unittest.TestCase):
    def setUp(self):
        self.demo = DemoHome()
        self.dir = os.path.join(self.demo.root, "kb")

    def tearDown(self):
        self.demo.cleanup()

    def card(self, name, text):
        path = os.path.join(self.dir, name)
        write(path, text)
        return kb.load_card(path, today="2026-11-26")

    def test_valid_card(self):
        c = self.card("backoff-jitter-cap.md", CARD)
        self.assertEqual(c["errors"], [])
        self.assertEqual(c["type"], "gotcha")
        self.assertEqual(c["scope"], ["cwd:/work/relay"])
        self.assertEqual(c["title"], "Backoff jitter can exceed the cap")
        self.assertEqual(c["status"], "active")

    def test_status_is_derived(self):
        expired = self.card("a.md", CARD.replace("verified: 2026-11-25", "verified: 2026-11-25\nexpires: 2026-11-25"))
        self.assertEqual(expired["status"], "expired")
        gone = self.card("b.md", CARD.replace("verified: 2026-11-25", "verified: 2026-11-25\nsuperseded_by: a"))
        self.assertEqual(gone["status"], "superseded")

    def test_errors_name_the_problem(self):
        c = self.card("Bad_Name.md", CARD.replace("type: gotcha", "type: tip").replace("#12", "#x"))
        joined = " | ".join(c["errors"])
        self.assertIn("type must be one of", joined)
        self.assertIn("must be <source>@<host>:<path>", joined)
        self.assertIn("file name must be", joined)
        self.assertIn("no `---` header", self.card("c.md", "# just a title\n")["errors"])


class Switch(unittest.TestCase):
    def test_off_by_default_and_env_wins(self):
        demo = DemoHome()
        demo.activate()
        try:
            self.assertFalse(kb.enabled())
            self.assertEqual(kb.default_kinds(DEFAULT_KINDS), list(DEFAULT_KINDS))
            with patch.dict(os.environ, {"MNEMO_KB": "on"}):
                self.assertTrue(kb.enabled())
                self.assertEqual(kb.default_kinds(DEFAULT_KINDS)[-1], "knowledge")
        finally:
            demo.deactivate()
            demo.cleanup()


class IndexedCards(unittest.TestCase):
    def setUp(self):
        self.demo = DemoHome()
        self.demo.activate()
        self.dir = os.path.join(self.demo.root, "kb")
        self.env = patch.dict(os.environ, {"MNEMO_KB": "on", "MNEMO_KB_DIR": self.dir})
        self.env.start()
        write(os.path.join(self.dir, "backoff-jitter-cap.md"), CARD)
        self.idx = Index(self.demo.db)
        self.idx.sync()

    def tearDown(self):
        self.env.stop()
        self.demo.deactivate()
        self.demo.cleanup()

    def test_cards_come_first_with_their_header(self):
        hits = search(self.idx, "backoff", kinds=kb.default_kinds(DEFAULT_KINDS))
        self.assertGreater(len(hits), 1, "the demo sessions mention backoff too")
        self.assertEqual(hits[0]["kind"], "knowledge")
        self.assertEqual(hits[0]["source"], "kb")
        self.assertEqual(hits[0]["cwd"], "/work/relay")
        self.assertEqual(hits[0]["card"]["id"], "backoff-jitter-cap")
        self.assertTrue(all(h["kind"] != "knowledge" for h in hits[1:]))

    def test_first_even_when_the_limit_is_full_of_sessions(self):
        hits = search(self.idx, "backoff", kinds=kb.default_kinds(DEFAULT_KINDS), limit=1)
        self.assertEqual([h["kind"] for h in hits], ["knowledge"])

    def test_first_after_the_device_merge(self):
        hits, _ = fan_out_search(self.idx, "backoff", kinds=kb.default_kinds(DEFAULT_KINDS),
                                 hosts=["local"], sync_local=False)
        self.assertEqual(hits[0]["kind"], "knowledge")

    def test_superseded_cards_are_left_out(self):
        write(os.path.join(self.dir, "backoff-jitter-cap.md"), CARD.replace(
            "verified: 2026-11-25", "verified: 2026-11-25\nsuperseded_by: other"))
        self.idx.sync()
        hits = search(self.idx, "backoff", kinds=kb.default_kinds(DEFAULT_KINDS))
        self.assertFalse(any(h["kind"] == "knowledge" for h in hits))

    def test_cards_are_not_sessions(self):
        self.assertFalse(any(r["source"] == "kb" for r in recent(self.idx)))

    def test_turning_it_off_drops_the_cards(self):
        with patch.dict(os.environ, {"MNEMO_KB": "off"}):
            self.idx.sync()
        self.assertEqual(self.idx.db.execute("SELECT COUNT(*) FROM files WHERE source='kb'").fetchone()[0], 0)
        self.assertEqual(self.idx.db.execute("SELECT COUNT(*) FROM messages WHERE kind='knowledge'").fetchone()[0], 0)


class Digest(unittest.TestCase):
    def setUp(self):
        self.demo = DemoHome()
        self.demo.activate()
        self.dir = os.path.join(self.demo.root, "kb")
        self.env = patch.dict(os.environ, {"MNEMO_KB": "on", "MNEMO_KB_DIR": self.dir})
        self.env.start()
        self.idx = Index(self.demo.db)
        self.idx.sync()

    def tearDown(self):
        self.env.stop()
        self.demo.deactivate()
        self.demo.cleanup()

    def pending(self):
        return kb.pending(self.idx, self.dir, "demo", min_user=1)

    def test_ledger_moves_a_session_out_of_pending_until_it_grows(self):
        before = self.pending()
        self.assertTrue(before)
        s = before[0]
        self.assertEqual(s["from"], 1)
        kb.record(self.dir, "demo", s["source"], s["path"], s["to"], [])
        self.assertNotIn(s["path"], [p["path"] for p in self.pending()])
        kb.record(self.dir, "demo", s["source"], s["path"], s["to"] - 1, [])  # an older, shorter entry
        self.assertNotIn(s["path"], [p["path"] for p in self.pending()])
        with open(kb.ledger_path(self.dir), encoding="utf-8") as f:
            self.assertEqual(len(f.read().splitlines()), 2, "the ledger only appends")

    def test_sessions_run_in_the_kb_dir_are_not_distilled(self):
        s = self.pending()[0]
        self.idx.db.execute("UPDATE files SET cwd=? WHERE path=?", (self.dir, s["path"]))
        self.assertNotIn(s["path"], [p["path"] for p in self.pending()])

    def test_material_has_user_messages_tools_and_related_cards(self):
        s = self.pending()[0]
        first = kb.material(self.idx, self.dir, "demo", s["path"])["turns"][0]["user"]
        words = sorted(kb._terms(first))[:2]
        self.assertEqual(len(words), 2, first)
        related = CARD.replace("cwd:/work/relay", "cwd:" + (s["cwd"] or "/")).replace(
            "# Backoff jitter can exceed the cap", "# Notes on `%s` and `%s`" % tuple(words))
        write(os.path.join(self.dir, "scoped-here.md"), related)
        write(os.path.join(self.dir, "unrelated.md"), CARD.replace("cwd:/work/relay", "global").replace(
            "# Backoff jitter can exceed the cap", "# `zzqx` and `qqzx` are unrelated"))
        cited = CARD.replace("claude@demo:/home/demo/.claude/projects/x/s.jsonl#12",
                             "%s@demo:%s#1" % (s["source"], s["path"]))
        write(os.path.join(self.dir, "cites-it.md"), cited.replace("cwd:/work/relay", "global"))
        m = kb.material(self.idx, self.dir, "demo", s["path"])
        self.assertEqual(m["session"]["from"], 1)
        self.assertTrue(m["turns"])
        self.assertTrue(m["turns"][0]["user"])
        self.assertRegex(m["turns"][0]["date"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(m["cards"], ["cites-it"])
        self.assertEqual([c["id"] for c in m["related"]], ["scoped-here"])
        json.dumps(m)  # what `mnemo digest show` prints

    def test_material_starts_past_the_ledger(self):
        s = self.pending()[0]
        kb.record(self.dir, "demo", s["source"], s["path"], 1, [])
        self.assertEqual(kb.material(self.idx, self.dir, "demo", s["path"])["session"]["from"], 2)


if __name__ == "__main__":
    unittest.main()


class SaveCard(unittest.TestCase):
    def setUp(self):
        self.demo = DemoHome()
        self.dir = os.path.join(self.demo.root, "kb")
        self.new = dict(title="NAS address", body="The NAS is `192.168.1.30`.", type_="fact",
                        scope=["host:demo"], sources=["claude@demo:/s.jsonl#3"], verified="2026-11-20",
                        today="2026-11-26")

    def tearDown(self):
        self.demo.cleanup()

    def test_create_then_update_merges_sources_and_keeps_the_rest(self):
        out = kb.save_card(self.dir, "nas-address", **self.new)
        self.assertTrue(out["created"])
        out = kb.save_card(self.dir, "nas-address", sources=["codex@demo:/t.jsonl#9"], verified="2026-11-25",
                           today="2026-11-26")
        self.assertFalse(out["created"])
        c = kb.load_card(os.path.join(self.dir, "nas-address.md"), today="2026-11-26")
        self.assertEqual(c["errors"], [])
        self.assertEqual(c["sources"], ["claude@demo:/s.jsonl#3", "codex@demo:/t.jsonl#9"])
        self.assertEqual((c["verified"], c["created"], c["title"]), ("2026-11-25", "2026-11-26", "NAS address"))
        self.assertIn("192.168.1.30", c["body"])

    def test_rejections_change_nothing(self):
        kb.save_card(self.dir, "nas-address", **self.new)
        path = os.path.join(self.dir, "nas-address.md")
        before = open(path, encoding="utf-8").read()
        with self.assertRaisesRegex(ValueError, "only moves forward"):
            kb.save_card(self.dir, "nas-address", verified="2026-11-01")
        with self.assertRaisesRegex(ValueError, "type must be one of"):
            kb.save_card(self.dir, "nas-address", type_="tip")
        with self.assertRaisesRegex(ValueError, "verified is required"):
            kb.save_card(self.dir, "other", title="x", type_="fact", scope=["global"], sources=["a@b:/c"])
        self.assertEqual(open(path, encoding="utf-8").read(), before)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "other.md")))

    def test_supersede_marks_the_old_card(self):
        kb.save_card(self.dir, "nas-address", **self.new)
        new = dict(self.new, title="NAS moved", verified="2026-11-25")
        out = kb.save_card(self.dir, "nas-address-v2", supersedes="nas-address", **new)
        self.assertEqual(out["superseded"], "nas-address")
        old = kb.load_card(os.path.join(self.dir, "nas-address.md"))
        self.assertEqual((old["status"], old["superseded_by"]), ("superseded", "nas-address-v2"))
        self.assertIn("192.168.1.30", old["body"], "the old claim is kept as it was")
        with self.assertRaisesRegex(ValueError, "no other card"):
            kb.save_card(self.dir, "x-card", supersedes="missing", **self.new)


class McpTools(unittest.TestCase):
    def setUp(self):
        self.demo = DemoHome()
        self.demo.activate()
        self.dir = os.path.join(self.demo.root, "kb")
        self.idx = Index(self.demo.db)
        self.idx.sync()

    def tearDown(self):
        self.demo.deactivate()
        self.demo.cleanup()

    def call(self, name, args):
        from mnemo import mcp_server
        out = mcp_server.handle_call(name, args, self.idx)
        return json.loads(out["content"][0]["text"])

    def test_tools_only_while_on(self):
        from mnemo import mcp_server
        names = lambda: {t["name"] for t in mcp_server.build_tools()}
        self.assertFalse(set(mcp_server.KB_TOOLS) & names())
        with patch.dict(os.environ, {"MNEMO_KB": "on"}):
            self.assertTrue(set(mcp_server.KB_TOOLS) <= names())
            for t in mcp_server.build_tools():
                if t["name"] in ("save_card", "mark_distilled"):
                    self.assertFalse(t["annotations"]["readOnlyHint"])
        with self.assertRaisesRegex(ValueError, "knowledge base is off"):
            self.call("list_cards", {})

    def test_distill_loop_over_mcp(self):
        with patch.dict(os.environ, {"MNEMO_KB": "on", "MNEMO_KB_DIR": self.dir}):
            listed = self.call("list_undistilled_sessions", {"since": "3650d", "min_user": 1})
            self.assertIn("## Loop", listed["guide"])
            s = listed["sessions"][0]
            digest = self.call("get_digest", {"path": s["path"]})
            turn = digest["turns"][0]
            src = "%s@%s:%s#%d" % (s["source"], listed["host"], s["path"], turn["lineno"])
            saved = self.call("save_card", {"id": "demo-claim", "title": "A claim", "type": "fact",
                                            "scope": ["global"], "sources": [src], "verified": turn["date"]})
            self.assertTrue(saved["created"])
            self.assertEqual(self.call("list_cards", {"id": "demo-claim"})["sources"], [src])
            self.call("mark_distilled", {"path": s["path"], "upto": digest["session"]["to"], "cards": ["demo-claim"]})
            again = self.call("list_undistilled_sessions", {"since": "3650d", "min_user": 1})["sessions"]
            self.assertNotIn(s["path"], [x["path"] for x in again])
            from mnemo import mcp_server
            with self.assertRaisesRegex(ValueError, "no such card"):
                mcp_server.handle_call("mark_distilled", {"path": s["path"], "upto": 1, "cards": ["nope"]}, self.idx)


class DistillingSessions(unittest.TestCase):
    def test_recording_a_distillation_marks_the_session(self):
        made = [
            'mcp__mnemo__mark_distilled({"path": "/s.jsonl", "upto": 9})',
            'mnemo/mark_distilled({"path": "/s.jsonl", "upto": 9})',
            'call_mcp_tool({"Arguments":{"path":"/s"},"ServerName":"mnemo","ToolName":"mark_distilled"})',
            'Bash({"command": "cd ~/.mnemo/kb && MNEMO_KB=on mnemo digest done /Users/a/s.jsonl --upto 9"})',
            'Bash({"command": "d(){ mnemo digest done \\"$@\\"; }\\nd /Users/a/s.jsonl"})',  # as indexed: one backslash
        ]
        talked_about = [
            'Write({"file_path": "/repo/distill.md", "content": "5. `mnemo digest done <path> --upto N`"})',
            'Bash({"command": "grep -n mark_distilled mnemo/mcp_server.py"})',
            'read_file({"Path":"/Users/a/.gemini/antigravity-cli/mcp/mnemo/mark_distilled.json"})',
        ]
        for t in made:
            self.assertTrue(kb._RECORDS_DISTILL.search(t), t)
        for t in talked_about:
            self.assertFalse(kb._RECORDS_DISTILL.search(t), t)
