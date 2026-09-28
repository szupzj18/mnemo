import os
import sqlite3
import time

from .model import cjk_grams, first_ts, make_title
from .sources import SOURCES, get_sources

DEFAULT_DB_PATH = os.path.expanduser("~/.mnemo/index.db")

SCHEMA_VERSION = "2"

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
  path       TEXT PRIMARY KEY,
  source     TEXT NOT NULL,
  session_id TEXT,
  cwd        TEXT,
  mtime      REAL NOT NULL,
  size       INTEGER NOT NULL,
  title      TEXT,
  started_ts TEXT
);
CREATE TABLE IF NOT EXISTS file_ranges (
  path TEXT NOT NULL,
  lo   INTEGER NOT NULL,
  hi   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS messages USING fts5(
  body,
  text,
  grams,
  source     UNINDEXED,
  session_id UNINDEXED,
  cwd        UNINDEXED,
  ts         UNINDEXED,
  role       UNINDEXED,
  kind       UNINDEXED,
  path       UNINDEXED,
  lineno     UNINDEXED,
  envelope   UNINDEXED,
  tokenize = "unicode61"
);
"""

DEFAULT_KINDS = ("text", "summary", "tool_call", "tool_result")

# Searches sync first; back-to-back callers (a remote's forwarded `index` then
# `search`, an agent firing several queries) skip the re-stat within this window.
SYNC_MIN_INTERVAL = 2.0


class Index:
    def __init__(self, db_path=None):
        self.db_path = db_path or DEFAULT_DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.db = sqlite3.connect(self.db_path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        # MCP server, CLI and dashboard may sync concurrently; wait for the
        # writer instead of failing with "database is locked".
        self.db.execute("PRAGMA busy_timeout = 10000")
        self.db.executescript(SCHEMA)
        self._migration_pending = self._detect_pending()

    def _set_version(self):
        self.db.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
            (SCHEMA_VERSION,),
        )

    def _detect_pending(self):
        """Whether stored tables predate the current schema and need a rebuild.

        Runs at construction without dropping anything, so a read-only command
        like `status` keeps working on an old index until the next sync rebuilds
        it. A freshly created (empty) v2 schema is stamped current immediately.
        """
        row = self.db.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        if row and row["value"] == SCHEMA_VERSION:
            return False
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(files)")}
        if "title" in cols:  # SCHEMA just created the current tables empty
            self._set_version()
            return False
        return True

    def _apply_schema(self):
        """Drop and recreate tables for the current schema (full reparse next)."""
        self.db.execute("DROP TABLE IF EXISTS messages")
        self.db.execute("DROP TABLE IF EXISTS file_ranges")
        self.db.execute("DROP TABLE IF EXISTS files")
        self.db.executescript(SCHEMA)
        self._set_version()
        self._migration_pending = False

    def close(self):
        self.db.close()

    # ------------------------------------------------------------------ sync

    def sync(self, source_names=None, home=None, logger=None):
        log = logger or (lambda msg: None)
        if self._migration_pending:
            log("schema v%s: rebuilding index from session files ..." % SCHEMA_VERSION)
            self._apply_schema()
        sources = get_sources(source_names, home=home)
        stats = dict(files_new=0, files_updated=0, files_removed=0, messages=0)

        for source in sources:
            current = {}
            for path in source.files():
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                current[path] = (st.st_mtime, st.st_size)

            rows = self.db.execute(
                "SELECT path, mtime, size FROM files WHERE source = ?", (source.name,)
            ).fetchall()
            known = {r["path"]: (r["mtime"], r["size"]) for r in rows}

            for path, (mtime, size) in current.items():
                if path not in known:
                    stats["files_new"] += 1
                elif known[path] != (mtime, size):
                    stats["files_updated"] += 1
                else:
                    continue
                self._reindex_file(source, path, mtime, size, stats, log)

            gone = set(known) - set(current)
            for path in gone:
                self._drop_path(path)
                self.db.execute("DELETE FROM files WHERE path = ?", (path,))
                stats["files_removed"] += 1

        if stats["files_removed"]:
            # Safety net for FTS rows left without a covering file_ranges row
            # (interrupted reindex in older code): their hits would fail context.
            self.db.execute(
                "DELETE FROM messages WHERE rowid NOT IN"
                " (SELECT m.rowid FROM messages m"
                "  JOIN file_ranges fr ON m.rowid BETWEEN fr.lo AND fr.hi)"
            )

        self.db.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('last_sync', ?)",
            (str(time.time()),),
        )
        return stats

    def sync_if_stale(self, min_interval=SYNC_MIN_INTERVAL, logger=None):
        """Incremental sync unless one finished within min_interval seconds.

        Returns the sync stats, or None when skipped.
        """
        last = self.last_sync()
        if last is not None and 0 <= time.time() - last < min_interval:
            return None
        return self.sync(logger=logger)

    def rebuild(self, source_names=None, home=None, logger=None):
        """Drop the index and reparse every session file from scratch."""
        log = logger or (lambda msg: None)
        self._apply_schema()
        return self.sync(source_names, home=home, logger=log)

    def _reindex_file(self, source, path, mtime, size, stats, log):
        try:
            session_id, cwd, msgs = source.parse(path)
        except Exception as exc:  # corrupt or unexpected file: skip, don't crash sync
            log("skip %s: %s" % (path, exc))
            return
        self._drop_path(path)
        if not msgs:
            self.db.execute(
                "INSERT OR REPLACE INTO"
                " files(path, source, session_id, cwd, mtime, size, title, started_ts)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (path, source.name, session_id, cwd, mtime, size, "", ""),
            )
            return
        rows = []
        for lineno, msg in msgs:
            rows.append(
                (
                    msg.stored,
                    msg.text,
                    cjk_grams(msg.text),
                    source.name,
                    session_id,
                    cwd,
                    msg.ts,
                    msg.role,
                    msg.kind,
                    path,
                    lineno,
                    1 if msg.envelope else 0,
                )
            )
        self.db.execute("BEGIN")
        lo = self.db.execute("SELECT COALESCE(MAX(rowid), 0) + 1 FROM messages").fetchone()[0]
        self.db.executemany(
            "INSERT INTO messages(body, text, grams, source, session_id, cwd, ts,"
            " role, kind, path, lineno, envelope)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        hi = lo + len(rows) - 1
        self.db.execute(
            "INSERT INTO file_ranges(path, lo, hi) VALUES(?,?,?)", (path, lo, hi)
        )
        self.db.execute(
            "INSERT OR REPLACE INTO"
            " files(path, source, session_id, cwd, mtime, size, title, started_ts)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (
                path, source.name, session_id, cwd, mtime, size,
                make_title(msgs), first_ts(msgs),
            ),
        )
        self.db.execute("COMMIT")
        stats["messages"] += len(rows)

    def _drop_path(self, path):
        for r in self.db.execute(
            "SELECT lo, hi FROM file_ranges WHERE path = ?", (path,)
        ).fetchall():
            self.db.execute("DELETE FROM messages WHERE rowid BETWEEN ? AND ?", (r["lo"], r["hi"]))
        self.db.execute("DELETE FROM file_ranges WHERE path = ?", (path,))

    # ------------------------------------------------------------------ info

    def counts(self):
        out = {}
        for r in self.db.execute(
            "SELECT source, COUNT(*) AS files FROM files GROUP BY source"
        ):
            out[r["source"]] = {"files": r["files"], "messages": 0}
        for r in self.db.execute(
            "SELECT source, COUNT(*) AS n FROM messages GROUP BY source"
        ):
            out.setdefault(r["source"], {"files": 0, "messages": 0})["messages"] = r["n"]
        return out

    def last_sync(self):
        row = self.db.execute("SELECT value FROM meta WHERE key='last_sync'").fetchone()
        return float(row["value"]) if row else None
