import contextlib
import json
import os
import re
import sqlite3
import time

from .model import cjk_grams, first_ts, make_title
from .sources import SOURCES, get_sources
from .sources.base import SourceUnavailable

DEFAULT_DB_PATH = os.path.expanduser("~/.mnemo/index.db")

SCHEMA_VERSION = "3"

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

class IndexTooNew(RuntimeError):
    """The index was written by a newer mnemo; this process must not write to it."""


def _schema_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# Searches sync first; back-to-back callers (a remote's forwarded `index` then
# `search`, an agent firing several queries) skip the re-stat within this window.
SYNC_MIN_INTERVAL = 2.0


SCHEMA_TABLES = set(re.findall(r"CREATE (?:VIRTUAL )?TABLE IF NOT EXISTS (\w+)", SCHEMA))


class Index:
    def __init__(self, db_path=None):
        self.db_path = db_path or DEFAULT_DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.db = sqlite3.connect(self.db_path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        # MCP server, CLI, dashboard and neighbors' relayed searches may sync
        # concurrently; wait for the writer instead of failing with "database is
        # locked". (Rollback journal, not WAL: `mnemo upgrade` swaps the file with
        # os.replace, which a leftover -wal file would corrupt.)
        self.db.execute("PRAGMA busy_timeout = 10000")
        have = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if not SCHEMA_TABLES <= have:  # new index, or tables an older mnemo did not have
            self._script(SCHEMA)
        self.stored_version = None
        self._migration_pending = self._detect_pending()

    @contextlib.contextmanager
    def _write(self):
        """One write transaction that takes the write lock up front.

        A deferred BEGIN (or a lone write statement) first reads, then upgrades
        to a write lock; when another writer is doing the same, SQLite refuses
        one of them at once ("database is locked") instead of letting
        busy_timeout wait. BEGIN IMMEDIATE queues for the lock instead, and a
        failure rolls back so no lock outlives it.
        """
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")

    def _script(self, script):
        """executescript in one IMMEDIATE transaction (it cannot run inside _write)."""
        try:
            self.db.executescript("BEGIN IMMEDIATE;\n" + script + "\nCOMMIT;")
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def _set_version(self):
        with self._write():
            self._stamp_version()

    def _stamp_version(self):
        self.db.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
        self.stored_version = SCHEMA_VERSION

    def _detect_pending(self):
        """Whether stored tables predate the current schema and need a rebuild.

        Runs at construction without dropping anything, so a read-only command
        like `status` keeps working on an old index until the next sync rebuilds
        it. A freshly created (empty) schema is stamped current immediately.
        """
        row = self.db.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        self.stored_version = row["value"] if row else None
        if row and row["value"] == SCHEMA_VERSION:
            return False
        if self.too_new:
            # Never re-stamp or rebuild an index a newer mnemo created; reads
            # still work, writes are refused in _check_writable().
            return False
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(files)")}
        if row is None and "title" in cols:  # SCHEMA just created the current tables empty
            self._set_version()
            return False
        return True

    @property
    def too_new(self):
        stored, ours = _schema_int(self.stored_version), _schema_int(SCHEMA_VERSION)
        return stored is not None and ours is not None and stored > ours

    def _check_writable(self):
        if self.too_new:
            raise IndexTooNew(
                "index %s has schema v%s, newer than this mnemo (v%s); "
                "restart this process or update mnemo before indexing"
                % (self.db_path, self.stored_version, SCHEMA_VERSION)
            )

    def incomplete_paths(self):
        """Files whose rows were written by an older mnemo into this schema.

        Pre-v2 writers insert files rows without a title (and messages without
        the v2 columns); v2 always writes a title, even an empty one.
        """
        paths = [r["path"] for r in self.db.execute("SELECT path FROM files WHERE title IS NULL")]
        from .sources.codex import ADAPTER_VERSION
        for row in self.db.execute(
            "SELECT f.path, f.mtime, f.size, fr.lo, fr.hi FROM files f"
            " LEFT JOIN file_ranges fr ON fr.path=f.path WHERE f.source='codex'"
        ):
            marker = self.db.execute("SELECT value FROM meta WHERE key=?",
                                     ("adapter:codex:" + row["path"],)).fetchone()
            expected = json.dumps([ADAPTER_VERSION, row["mtime"], row["size"], row["lo"], row["hi"]])
            if not marker or marker[0] != expected:
                paths.append(row["path"])
        return paths

    def _apply_schema(self):
        """Drop and recreate tables for the current schema (full reparse next)."""
        self._check_writable()
        self._script(
            "DROP TABLE IF EXISTS messages; DROP TABLE IF EXISTS file_ranges; DROP TABLE IF EXISTS files;\n"
            + SCHEMA
            + "\nINSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', '%s');" % SCHEMA_VERSION
        )
        self.stored_version = SCHEMA_VERSION
        self._migration_pending = False

    def close(self):
        self.db.close()

    # ------------------------------------------------------------------ sync

    def sync(self, source_names=None, home=None, logger=None):
        log = logger or (lambda msg: None)
        self._check_writable()
        if self._migration_pending:
            log("schema v%s: rebuilding index from session files ..." % SCHEMA_VERSION)
            self._apply_schema()
        sources = get_sources(source_names, home=home)
        stats = dict(files_new=0, files_updated=0, files_removed=0, messages=0)
        # Self-heal rows an older mnemo process wrote after the upgrade.
        stale = set(self.incomplete_paths())
        if stale:
            log("repairing %d file(s) written by an older mnemo" % len(stale))

        for source in sources:
            warnings = []
            def source_log(message):
                log(message)
                if message.startswith("skip "):
                    warnings.append(message)
            try:
                current = {path: (mtime, size) for path, mtime, size in source.records()}
            except SourceUnavailable as exc:
                source_log("skip %s: %s" % (source.name, exc))
                with self._write():
                    self.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)",
                                    ("sync_warnings:" + source.name, json.dumps(warnings)))
                continue

            rows = self.db.execute(
                "SELECT path, mtime, size FROM files WHERE source = ?", (source.name,)
            ).fetchall()
            known = {r["path"]: (r["mtime"], r["size"]) for r in rows}

            for path, (mtime, size) in current.items():
                if path not in known:
                    stats["files_new"] += 1
                elif known[path] != (mtime, size) or path in stale:
                    stats["files_updated"] += 1
                else:
                    continue
                self._reindex_file(source, path, mtime, size, stats, source_log)

            gone = set(known) - set(current)
            if gone:
                with self._write():
                    for path in gone:
                        self._drop_path(path)
                        self.db.execute("DELETE FROM files WHERE path = ?", (path,))
                        stats["files_removed"] += 1
            with self._write():
                self.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)",
                                ("sync_warnings:" + source.name, json.dumps(warnings)))

        with self._write():
            if stale:
                # Old-shape rows outside any file range (or left by an interrupted
                # write) are unreachable by the reindex above; drop them outright.
                self.db.execute("DELETE FROM messages WHERE text IS NULL")

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
        if not msgs:
            with self._write():
                self._drop_path(path)
                self.db.execute(
                    "INSERT OR REPLACE INTO"
                    " files(path, source, session_id, cwd, mtime, size, title, started_ts)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (path, source.name, session_id, cwd, mtime, size, "", ""),
                )
                self._stamp_adapter(source, path, mtime, size, None, None)
            return
        rows = []
        for lineno, msg in msgs:
            # body holds the verbatim message only when it differs from the
            # cleaned text (injected envelopes); otherwise NULL, and readers
            # fall back to text. Storing both doubled the index (schema v3).
            stored = msg.stored
            rows.append(
                (
                    stored if stored != msg.text else None,
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
        # Old rows out and new rows in, atomically: a crash between them would
        # otherwise leave the session unindexed with its files row unchanged.
        with self._write():
            self._drop_path(path)
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
            self._stamp_adapter(source, path, mtime, size, lo, hi)
        stats["messages"] += len(rows)

    def _drop_path(self, path):
        for r in self.db.execute(
            "SELECT lo, hi FROM file_ranges WHERE path = ?", (path,)
        ).fetchall():
            self.db.execute("DELETE FROM messages WHERE rowid BETWEEN ? AND ?", (r["lo"], r["hi"]))
        self.db.execute("DELETE FROM file_ranges WHERE path = ?", (path,))
        self.db.execute("DELETE FROM meta WHERE key=?", ("adapter:codex:" + path,))

    def _stamp_adapter(self, source, path, mtime, size, lo, hi):
        if source.name == "codex":
            from .sources.codex import ADAPTER_VERSION
            self.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (
                "adapter:codex:" + path, json.dumps([ADAPTER_VERSION, mtime, size, lo, hi])))

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
