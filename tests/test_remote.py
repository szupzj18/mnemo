import unittest

import helpers  # noqa: F401  (puts the repo on sys.path)

from mnemo import remote


class RemoteTest(unittest.TestCase):
    def test_forwarded_search_is_pinned_to_the_remote_index(self):
        argv = remote.search_argv("retry budget", ["claude"], None, "relay", "2026-09-01", 20)
        self.assertEqual(argv[:6], ["search", "retry budget", "--json", "--limit", "20", "--host"])
        self.assertEqual(argv[6], remote.LOCAL)
        self.assertIn("--source", argv)
        # Newer local-only flags must never be forwarded to (possibly older) remotes.
        self.assertNotIn("--no-sync", argv)

    def test_all_kinds_is_forwarded_when_kinds_is_empty(self):
        self.assertIn("--all-kinds", remote.search_argv("x", None, [], None, None, 5))
        self.assertNotIn("--all-kinds", remote.search_argv("x", None, None, None, None, 5))

    def test_rrf_merges_by_rank_across_hosts(self):
        def hit(path, line):
            return {"path": path, "lineno": line}

        per_host = [
            ("local", [hit("a", 1), hit("b", 1)]),
            ("devbox", [hit("c", 1), hit("a", 1)]),
        ]
        merged = remote._rrf(per_host, 10)
        # Rank 1 on each host ties; ties break by host name, so devbox's first hit leads.
        self.assertEqual([h["path"] for h in merged], ["c", "a", "a", "b"])
        self.assertEqual(len(remote._rrf(per_host, 2)), 2)


if __name__ == "__main__":
    unittest.main()
