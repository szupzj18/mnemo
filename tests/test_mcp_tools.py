import json
import unittest

from helpers import DemoHome

from mnemo import mcp_server


class ToolAnnotationsTest(unittest.TestCase):
    """MCP clients read tool annotations to decide whether to ask before a call. Codex skips its
    approval prompt only for readOnlyHint tools, and cancels the rest when approvals are off
    (`codex exec`), so the read tools must say so."""

    READ_TOOLS = {"search_sessions", "get_context", "get_session", "list_recent_sessions"}

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
