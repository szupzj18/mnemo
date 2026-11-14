"""DeepSeek Harness: one compressed JSONL generation per session directory."""
import json
import os
import shutil
import tempfile
import unittest

from helpers import REPO  # noqa: F401  (puts the checkout on sys.path)

from mnemo.index import Index
from mnemo.search import get_context, get_session, raw_session, search
from mnemo.sources.dsh import DshSource

ZSTD = shutil.which("zstd")


def header(sid, cwd, version=4):
    return {"type": "session", "version": version, "id": sid, "createdAt": 1794545374473,
            "cwd": cwd, "isSeeded": False, "delegationDepth": 0, "agentPreset": "standard"}


def user(text, seq, source_kind="user", op="append"):
    data = {"content": [{"type": "text", "text": text}], "role": "user", "id": "u%d" % seq}
    if source_kind:
        data["source"] = {"kind": source_kind}
    event = {"type": "user/message", "seq": seq, "time": 1794545386897 + seq * 1000, "data": data}
    if op is not None:
        event["surfaceOp"] = op
    return event


def assistant(blocks, seq):
    return {"type": "assistant/message", "seq": seq, "time": 1794545395452 + seq * 1000,
            "data": {"turn": 1, "step": 1, "message": {"role": "assistant", "content": blocks}},
            "surfaceOp": "append"}


def tool_result(text, seq):
    return {"type": "tool/result", "seq": seq, "time": 1794545397628 + seq * 1000,
            "data": {"turn": 1, "step": 1,
                     "message": {"role": "tool", "content": [{"type": "text", "text": text}]}},
            "surfaceOp": "append"}


ROWS = [
    header("session-relay", "/home/alex/relay"),
    {"type": "permission/preset", "seq": 0, "time": 1794545374482, "data": {"preset": "ask"}},
    user("Webhook retries give up too fast. Add exponential backoff.", 1),
    user("<system-reminder>Instructions from: AGENTS.md\nUse tabs.</system-reminder>",
         2, source_kind="agent-instructions"),
    assistant([
        {"type": "reasoning", "text": "The loop lives in relay/delivery.py."},
        {"type": "text", "text": "I'll look at the retry loop."},
        {"type": "tool-call", "id": "c1", "name": "bash",
         "arguments": "{\"command\": \"rg -n retry relay/\"}"},
    ], 3),
    tool_result("relay/delivery.py:42:    for attempt in range(3):", 4),
    user("Cap the delay at 30s.", 5),
    assistant([{"type": "text", "text": "Done: full jitter, capped at 30s."}], 6),
    {"type": "compaction/summary", "seq": 7, "time": 1794545400000,
     "data": {"compactionId": "c", "summary": "Retry loop now backs off with full jitter."}},
    user("Compacted checkpoint is model-only.", 8, op={"op": "replace", "startSeq": 1, "endSeq": 7}),
]


def write_session(home, rows, sid="session-relay", cwd="/home/alex/relay", version=4, compressed=True):
    project = "--" + cwd.strip("/").replace("/", "-") + "--"
    folder = os.path.join(home, ".dsh", "sessions", project, sid)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "session.v%d.jsonl" % version)
    text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    if compressed:
        import subprocess
        with open(path + ".zstd", "wb") as f:
            subprocess.run([ZSTD, "-q", "-c"], input=text.encode("utf-8"), stdout=f, check=True)
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    return path


class DshCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mnemo-dsh-")
        self.home = os.path.join(self.root, "home")
        os.makedirs(self.home)
        self.db = os.path.join(self.root, "index.db")
        self.path = write_session(self.home, ROWS)
        self.idx = Index(self.db)
        self.stats = self.idx.sync(["dsh"], home=self.home)

    def tearDown(self):
        self.idx.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def rows(self):
        return [(r["lineno"], r["role"], r["kind"], r["text"], r["body"], r["envelope"])
                for r in self.idx.db.execute(
                    "SELECT lineno, role, kind, text, body, envelope FROM messages ORDER BY rowid")]

    def file_row(self):
        return self.idx.db.execute("SELECT * FROM files").fetchone()


@unittest.skipUnless(ZSTD, "the compressed DSH logs need the zstd executable")
class CompressedTest(DshCase):
    def test_indexes_the_transcript(self):
        self.assertEqual(self.stats["files_new"], 1)
        self.assertEqual(self.idx.counts()["dsh"], {"files": 1, "messages": 9})
        self.assertEqual(self.rows(), [
            (3, "user", "text", "Webhook retries give up too fast. Add exponential backoff.",
             None, 0),
            # Injected context stays verbatim in body but cannot match a query.
            (4, "user", "text", "",
             "<system-reminder>Instructions from: AGENTS.md\nUse tabs.</system-reminder>", 1),
            (5, "assistant", "reasoning", "The loop lives in relay/delivery.py.", None, 0),
            (5, "assistant", "text", "I'll look at the retry loop.", None, 0),
            (5, "assistant", "tool_call", 'bash({"command": "rg -n retry relay/"})', None, 0),
            (6, "tool", "tool_result", "relay/delivery.py:42:    for attempt in range(3):", None, 0),
            (7, "user", "text", "Cap the delay at 30s.", None, 0),
            (8, "assistant", "text", "Done: full jitter, capped at 30s.", None, 0),
            # A compaction summary is searchable; its replacement checkpoint is not.
            (9, "user", "summary", "Retry loop now backs off with full jitter.", None, 0),
        ])

    def test_metadata(self):
        row = self.file_row()
        self.assertEqual(row["path"], self.path)
        self.assertEqual(row["session_id"], "session-relay")
        self.assertEqual(row["cwd"], "/home/alex/relay")
        self.assertEqual(row["title"], "Webhook retries give up too fast. Add exponential backoff.")
        self.assertEqual(row["started_ts"], "2026-11-13T04:49:47Z")

    def test_injected_context_is_not_searchable_but_raw_reads_keep_it(self):
        self.assertEqual(search(self.idx, "tabs"), [])
        hits = search(self.idx, "backoff")
        self.assertTrue(hits)
        context = get_context(self.idx, self.path, 3)
        self.assertEqual(context[1]["text"], ROWS[3]["data"]["content"][0]["text"])
        self.assertEqual(context[1]["envelope"], 1)

    def test_session_and_raw_reads(self):
        session = get_session(self.idx, self.path)
        self.assertEqual((session["source"], session["session_id"], session["cwd"]),
                         ("dsh", "session-relay", "/home/alex/relay"))
        self.assertEqual(session["count"], 9)
        raw = raw_session(self.idx, self.path)
        self.assertEqual(raw["messages"][0]["text"],
                         "Webhook retries give up too fast. Add exponential backoff.")
        self.assertTrue(raw["raw"])

    def test_archived_copy_stands_in_for_a_deleted_log(self):
        os.remove(self.path + ".zstd")
        stats = self.idx.sync(["dsh"], home=self.home)
        self.assertEqual(stats["files_removed"], 0)
        self.assertTrue(search(self.idx, "backoff"))
        self.assertEqual(get_session(self.idx, self.path)["count"], 9)
        self.assertTrue(search(self.idx, "backoff")[0]["archived"])

    def test_truncated_tail_keeps_complete_records(self):
        # DSH appends one Zstandard frame per write; catching it mid-append
        # leaves only the newest frame truncated.
        write_truncated(self.path + ".zstd", ROWS)
        source = DshSource()
        sid, cwd, msgs = source.parse(self.path)
        self.assertEqual((sid, cwd), ("session-relay", "/home/alex/relay"))
        self.assertGreaterEqual(len(msgs), 5)
        self.assertLess(len(msgs), 9)
        self.assertEqual(source.take_partial_read(), self.path + ".zstd")
        self.assertIsNone(source.take_partial_read())

    def test_sync_logs_a_partial_read_without_a_warning(self):
        write_truncated(self.path + ".zstd", ROWS + [user("one more line", 9)])
        lines = []
        stats = self.idx.sync(["dsh"], home=self.home, logger=lines.append)
        self.assertEqual(stats["files_updated"], 1)
        self.assertTrue(any("still being written" in l for l in lines), lines)
        self.assertFalse(any(l.startswith("skip ") for l in lines), lines)
        self.assertTrue(search(self.idx, "backoff"))

    def test_a_moved_session_keeps_one_archived_copy(self):
        moved = os.path.join(self.home, ".dsh", "sessions", "--elsewhere--", "session-relay")
        os.makedirs(os.path.dirname(moved))
        os.rename(os.path.dirname(self.path), moved)
        for _ in range(2):
            self.idx.sync(["dsh"], home=self.home)
        archive = self.idx.archive.root
        copies = sorted(os.path.relpath(os.path.join(d, n), archive)
                        for d, _, names in os.walk(archive) for n in names if n != "origin.json")
        self.assertEqual(copies, [os.path.join("dsh", ".dsh", "sessions", "--elsewhere--", "session-relay",
                                               "session.v4.jsonl.zstd")])


def write_truncated(path, rows):
    """Two Zstandard frames, the second cut short, like a log caught mid-append."""
    import subprocess
    lines = [json.dumps(row, ensure_ascii=False) + "\n" for row in rows]
    frames = [subprocess.run([ZSTD, "-q", "-c"], input="".join(chunk).encode("utf-8"),
                             stdout=subprocess.PIPE, check=True).stdout
              for chunk in (lines[:5], lines[5:])]
    with open(path, "wb") as f:
        f.write(frames[0] + frames[1][:-12])


class FindZstdTest(unittest.TestCase):
    def test_looks_in_usual_install_folders_when_path_lacks_it(self):
        # A desktop app's MCP server gets a PATH of /usr/bin:/bin only.
        from unittest import mock
        from mnemo.sources import base
        root = tempfile.mkdtemp(prefix="mnemo-dsh-zstd-")
        try:
            empty, brew = os.path.join(root, "empty"), os.path.join(root, "brew")
            os.makedirs(empty)
            os.makedirs(brew)
            fake = os.path.join(brew, "zstd")
            with open(fake, "w") as f:
                f.write("#!/bin/sh\n")
            with mock.patch.dict(os.environ, {"PATH": empty}), \
                    mock.patch.object(base, "ZSTD_DIRS", (os.path.join(root, "missing"), brew)):
                os.chmod(fake, 0o644)
                self.assertIsNone(base.find_zstd(), "not executable")
                os.chmod(fake, 0o755)
                self.assertEqual(base.find_zstd(), fake)
        finally:
            shutil.rmtree(root, ignore_errors=True)


@unittest.skipUnless(ZSTD, "compressed logs need the zstd executable")
class OtherSourcesStayStrictTest(unittest.TestCase):
    def test_a_truncated_codex_rollout_is_still_an_error(self):
        from mnemo.sources.base import SourceUnavailable
        from mnemo.sources.codex import CodexSource
        root = tempfile.mkdtemp(prefix="mnemo-dsh-codex-")
        try:
            path = os.path.join(root, "rollout-z.jsonl")
            write_truncated(path + ".zst", [{"type": "session_meta", "payload": {"id": str(i)}} for i in range(8)])
            with self.assertRaises(SourceUnavailable):
                CodexSource().parse(path)
        finally:
            shutil.rmtree(root, ignore_errors=True)


@unittest.skipUnless(ZSTD, "the compressed DSH logs need the zstd executable")
class GenerationTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mnemo-dsh-gen-")
        self.home = os.path.join(self.root, "home")
        os.makedirs(self.home)
        self.idx = Index(os.path.join(self.root, "index.db"))

    def tearDown(self):
        self.idx.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_highest_generation_wins_and_the_older_one_returns_when_it_is_gone(self):
        older = [header("session-relay", "/home/alex/relay", version=3),
                 user("Only the old generation says zebra.", 1, op=None)]
        v3 = write_session(self.home, older, version=3)
        self.idx.sync(["dsh"], home=self.home)
        self.assertEqual(len(search(self.idx, "zebra")), 1)

        v4 = write_session(self.home, ROWS)
        stats = self.idx.sync(["dsh"], home=self.home)
        # The generation is part of the path, so the migrated file replaces the
        # old one as one removed and one new session.
        self.assertEqual((stats["files_new"], stats["files_removed"]), (1, 1))
        self.assertEqual(len(search(self.idx, "zebra")), 0)
        self.assertEqual(len(search(self.idx, "backoff")), 1)

        os.remove(v4 + ".zstd")
        self.idx.sync(["dsh"], home=self.home)
        # The live v3 log carries the session; the archived v4 copy is not used.
        self.assertEqual(len(search(self.idx, "zebra")), 1)
        self.assertEqual(len(search(self.idx, "backoff")), 0)
        self.assertTrue(os.path.exists(v3 + ".zstd"))


class PlaintextTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mnemo-dsh-plain-")
        self.home = os.path.join(self.root, "home")
        os.makedirs(self.home)
        self.path = write_session(self.home, ROWS, compressed=False)
        self.idx = Index(os.path.join(self.root, "index.db"))

    def tearDown(self):
        self.idx.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_plaintext_backend_is_read_without_zstd(self):
        self.idx.sync(["dsh"], home=self.home)
        self.assertEqual(len(search(self.idx, "backoff")), 1)
        self.assertEqual(DshSource().actual(self.path), self.path)


class V3ToolResultTest(unittest.TestCase):
    def test_nested_tool_result_becomes_a_tool_message(self):
        root = tempfile.mkdtemp(prefix="mnemo-dsh-v3-")
        try:
            home = os.path.join(root, "home")
            os.makedirs(home)
            rows = [
                header("session-old", "/home/alex/old", version=3),
                {"type": "tool/result", "seq": 1, "time": 1794545397628,
                 "data": {"turn": 1, "step": 1, "message": {
                     "role": "user",
                     "content": [{"type": "tool-result", "toolCallId": "c1", "content": [
                         {"type": "text", "text": "old logs nested the result"}]}]}},
                 "surfaceOp": "append"},
            ]
            path = write_session(home, rows, sid="session-old", cwd="/home/alex/old",
                                 version=3, compressed=False)
            sid, cwd, msgs = DshSource().parse(path)
            self.assertEqual((sid, cwd), ("session-old", "/home/alex/old"))
            self.assertEqual([(m.role, m.kind, m.text) for _, m in msgs],
                             [("tool", "tool_result", "old logs nested the result")])
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
