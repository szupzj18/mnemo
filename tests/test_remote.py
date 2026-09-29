import concurrent.futures
import contextlib
import io
import unittest

from helpers import DemoHome

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


    def test_parallel_learning_loses_no_update(self):
        # Neighbors are searched in parallel and each thread records what it learned.
        with contextlib.redirect_stdout(io.StringIO()):
            demo = DemoHome()
        demo.activate()
        try:
            names = ["dev-%d" % i for i in range(16)]
            remote.save_remotes([{"name": n, "host": n} for n in names])
            for _ in range(5):
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(names)) as pool:
                    list(pool.map(lambda n: remote.update_remote(n, node_id="id-" + n), names))
                learned = {r["name"]: r.get("node_id") for r in remote.load_remotes()}
                self.assertEqual(learned, {n: "id-" + n for n in names})
                remote.save_remotes([{"name": n, "host": n} for n in names])
        finally:
            demo.deactivate()
            demo.cleanup()


if __name__ == "__main__":
    unittest.main()
