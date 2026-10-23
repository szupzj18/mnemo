import json
import unittest

from helpers import DemoHome

from mnemo import mcp_server


class ToolAnnotationsTest(unittest.TestCase):
    """MCP clients read tool annotations to decide whether to ask before a call. Codex skips its
    approval prompt only for readOnlyHint tools, and cancels the rest when approvals are off
    (`codex exec`), so the read tools must say so."""

    READ_TOOLS = {"search_sessions", "get_context", "get_session", "list_recent_sessions", "list_skills", "skill_usage"}

    def setUp(self):
        self.demo = DemoHome()
        self.demo.activate()
        self.addCleanup(self.demo.cleanup)
        self.addCleanup(self.demo.deactivate)
        self.tools = {t["name"]: t for t in mcp_server.build_tools()}

    def test_read_tools_are_marked_read_only(self):
        self.assertEqual(self.READ_TOOLS - set(self.tools), set())
        for name in self.READ_TOOLS:
            self.assertIs(self.tools[name]["annotations"]["readOnlyHint"], True, name)

    def test_reindex_is_not_marked_read_only(self):
        # It writes the local index, so a client should keep treating it as a write.
        self.assertIs(self.tools["reindex"]["annotations"]["readOnlyHint"], False)
        self.assertIs(self.tools["reindex"]["annotations"]["destructiveHint"], False)

    def test_every_tool_declares_annotations(self):
        for name, tool in self.tools.items():
            self.assertIn("annotations", tool, name)

    def test_annotations_survive_the_wire(self):
        listed = json.loads(json.dumps({"tools": list(self.tools.values())}))["tools"]
        self.assertTrue(all(t["annotations"]["readOnlyHint"] is (t["name"] in self.READ_TOOLS) for t in listed))


if __name__ == "__main__":
    unittest.main()


class DbOptionTest(unittest.TestCase):
    def test_server_and_tool_processes_use_the_given_index(self):
        import contextlib
        import io
        import os
        import subprocess
        import sys

        from helpers import REPO, DemoHome

        with contextlib.redirect_stdout(io.StringIO()):
            demo = DemoHome()
        try:
            db = os.path.join(demo.root, "elsewhere.db")
            env = dict(os.environ, HOME=demo.home, MNEMO_ARCHIVE="off")
            for k in ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID", "CODEX_SESSION_ID"):
                env.pop(k, None)
            requests = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                 "params": {"name": "list_recent_sessions", "arguments": {"limit": 3}}},
            ]
            p = subprocess.run([sys.executable, os.path.join(REPO, "bin", "mnemo"), "--db", db, "mcp"],
                               input="".join(json.dumps(r) + "\n" for r in requests),
                               capture_output=True, text=True, env=env, timeout=120)
            answers = {r["id"]: r for r in map(json.loads, p.stdout.splitlines())}
            rows = json.loads(answers[2]["result"]["content"][0]["text"])
            self.assertEqual(len(rows), 3, p.stderr)
            self.assertTrue(os.path.isfile(db))
            self.assertFalse(os.path.exists(os.path.join(demo.home, ".mnemo", "index.db")),
                             "nothing may open the default index when --db names another")
        finally:
            demo.cleanup()
