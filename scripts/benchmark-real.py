#!/usr/bin/env python3
"""Measure Mnemo on this machine's real session history; print aggregates only.

Builds a throwaway index from your sessions (the live one is not touched),
then times indexing, sync, MCP start-up, searches and session reads. The
output holds counts, sizes and timings, never session text or paths, so it
can be published:

  python3 scripts/benchmark-real.py --json /tmp/real.json

Query terms are generic and set with --query (repeatable); each is run with
--host local --no-sync so a timing is the search alone, Python start-up
included (as an agent's CLI call would pay it).
"""
import argparse
import json
import os
import platform
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from mnemo import __version__  # noqa: E402
from mnemo.index import Index  # noqa: E402
from mnemo.search import get_context, get_session  # noqa: E402
from mnemo.sources import get_sources  # noqa: E402

MNEMO = [sys.executable, os.path.join(REPO, "bin", "mnemo")]
QUERIES = [
    ("English, one word", "error"),
    ("English, two words", "retry timeout"),
    ("Chinese, two words", "索引 同步"),
    ("Mixed Chinese + English", "mnemo 搜索"),
]


def median_ms(fn, repeats):
    times = []
    for _ in range(repeats):
        t = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t) * 1000)
    return round(statistics.median(times), 1)


def tokens(text):
    """Same heuristic as the historical numbers: CJK ~0.75 tok/char, other ~0.28."""
    cjk = sum(1 for ch in text if "㐀" <= ch <= "鿿")
    return int(cjk * 0.75 + (len(text) - cjk) * 0.28)


def run(argv, db, **kw):
    return subprocess.run(MNEMO + ["--db", db] + argv, capture_output=True, text=True, check=True, **kw)


def git_describe():
    try:
        return subprocess.run(["git", "-C", REPO, "describe", "--tags", "--long", "--dirty"],
                              capture_output=True, text=True, timeout=10).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repeats", type=int, default=5)
    p.add_argument("--query", action="append", metavar="LABEL=TERMS",
                   help="replace the default queries, e.g. --query 'English, one word=error'")
    p.add_argument("--json", help="also write the results here")
    args = p.parse_args()
    queries = [tuple(q.split("=", 1)) for q in args.query] if args.query else QUERIES

    out = {"measured": time.strftime("%Y-%m-%d"), "mnemo": __version__, "revision": git_describe(),
           "platform": platform.platform(terse=True), "python": platform.python_version(),
           "sqlite": sqlite3.sqlite_version}

    # Corpus: what the sources see on disk (bytes of session files).
    corpus = {}
    for src in get_sources():
        try:
            recs = list(src.records())
        except Exception:  # an unreadable source is reported as empty
            recs = []
        sized = src.name != "opencode"  # one database: its "size" is a part count, not bytes
        corpus[src.name] = {"sessions": len(recs), "bytes": sum(r[2] for r in recs) if sized else None}
    out["corpus"] = corpus

    tmp = tempfile.mkdtemp(prefix="mnemo-bench-")
    db = os.path.join(tmp, "index.db")
    try:
        t = time.perf_counter()
        run(["index"], db)
        out["full_build_s"] = round(time.perf_counter() - t, 1)
        idx = Index(db)
        counts = idx.counts()
        out["messages"] = {k: v["messages"] for k, v in counts.items()}
        out["index_bytes"] = os.path.getsize(db)
        out["incremental_sync_ms"] = median_ms(lambda: run(["index"], db), args.repeats)

        def mcp_ready():
            # --db: without it the server syncs the live index, with this checkout's code.
            proc = subprocess.Popen(MNEMO + ["--db", db, "mcp"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE, text=True)
            for line in proc.stderr:
                if "ready" in line:
                    break
            proc.stdin.close()
            proc.wait(30)
        out["mcp_ready_ms"] = median_ms(mcp_ready, args.repeats)

        out["queries"] = []
        for label, terms in queries:
            ms = median_ms(lambda: run(["search", terms, "--host", "local", "--no-sync", "--json", "--limit", "20"], db),
                           args.repeats)
            row = {"label": label, "terms": terms, "ms": ms}
            for limit in (10, 20):
                text = run(["search", terms, "--host", "local", "--no-sync", "--json", "--limit", str(limit)], db).stdout
                row["hits_%d" % limit] = len(json.loads(text))
                row["tokens_%d" % limit] = tokens(text)
            out["queries"].append(row)

        # The largest session by indexed messages: context and paged reads.
        path, lo, hi = idx.db.execute(
            "SELECT path, lo, hi FROM file_ranges ORDER BY hi - lo DESC LIMIT 1").fetchone()
        source = idx.db.execute("SELECT source FROM files WHERE path = ?", (path,)).fetchone()[0]
        mid = idx.db.execute("SELECT lineno FROM messages WHERE rowid = ?", ((lo + hi) // 2,)).fetchone()[0]
        reparse = next(s for s in get_sources() if s.name == source)
        largest = {"source": source, "messages": hi - lo + 1,
                   "context_ms": median_ms(lambda: get_context(idx, path, mid), 9),
                   "reparse_ms": median_ms(lambda: reparse.parse(path), 3)}
        full = get_session(idx, path)
        largest["full_read_ms"] = median_ms(lambda: get_session(idx, path), args.repeats)
        largest["full_read_bytes"] = len(json.dumps(full, ensure_ascii=False).encode("utf-8"))
        page = get_session(idx, path, limit=181, anchor_line=mid)
        largest["first_page_ms"] = median_ms(lambda: get_session(idx, path, limit=181, anchor_line=mid), args.repeats)
        largest["first_page_bytes"] = len(json.dumps(page, ensure_ascii=False).encode("utf-8"))
        out["largest_session"] = largest
        idx.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(json.dumps(out, ensure_ascii=False, indent=2))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
