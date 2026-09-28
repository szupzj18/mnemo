"""Safe index upgrades: back up, rebuild beside the live index, verify, swap.

The live index is never modified in place. A rebuild goes into a temporary
file next to it and replaces it with an atomic rename only after it verifies,
so a failed or interrupted upgrade leaves the old index untouched. Backups use
SQLite's online backup API, which yields a consistent copy even while other
mnemo processes are reading or writing.
"""
import glob
import os
import re
import sqlite3
import subprocess
import time

from .index import SCHEMA_VERSION, Index

BACKUP_KEEP = 3


class UpgradeError(RuntimeError):
    pass


def backup_dir(db_path):
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "backups")


def _copy_db(src_path, dst_path):
    """Consistent copy of a live SQLite database via the backup API."""
    tmp = dst_path + ".tmp"
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(tmp)
    try:
        src.execute("PRAGMA busy_timeout = 10000")
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    os.replace(tmp, dst_path)


def _stored_version(db_path):
    db = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    try:
        row = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        return row[0] if row else "1"
    except sqlite3.Error:
        return "unknown"
    finally:
        db.close()


def backup(db_path, keep=BACKUP_KEEP, label=None):
    """Snapshot db_path into backups/; keep the newest `keep`. Returns the path or None."""
    if not os.path.isfile(db_path):
        return None
    folder = backup_dir(db_path)
    os.makedirs(folder, exist_ok=True)
    name = "index-v%s-%s%s.db" % (
        _stored_version(db_path), time.strftime("%Y%m%d-%H%M%S"), "-" + label if label else "")
    dest = os.path.join(folder, name)
    _copy_db(db_path, dest)
    for old in list_backups(db_path)[keep:]:
        os.remove(old)
    return dest


def list_backups(db_path):
    """Backups, newest first."""
    paths = glob.glob(os.path.join(backup_dir(db_path), "index-*.db"))
    return sorted(paths, key=os.path.getmtime, reverse=True)


def verify(idx):
    """Problems that would make an index unfit to swap in (empty list = OK)."""
    problems = []
    if idx.stored_version != SCHEMA_VERSION:
        problems.append("schema is v%s, expected v%s" % (idx.stored_version, SCHEMA_VERSION))
    incomplete = idx.incomplete_paths()
    if incomplete:
        problems.append("%d file(s) with incomplete rows" % len(incomplete))
    missing = idx.db.execute("SELECT COUNT(*) FROM messages WHERE text IS NULL").fetchone()[0]
    if missing:
        problems.append("%d message(s) without searchable text" % missing)
    return problems


def rebuild(db_path, home=None, logger=lambda m: None):
    """Build a fresh index beside db_path, verify it, then atomically replace db_path."""
    tmp = "%s.rebuild-%d" % (db_path, os.getpid())
    for leftover in (tmp, tmp + "-journal"):
        if os.path.exists(leftover):
            os.remove(leftover)
    idx = Index(tmp)
    try:
        stats = idx.sync(home=home, logger=logger)
        problems = verify(idx)
        counts = idx.counts()
    finally:
        idx.close()
    if problems:
        os.remove(tmp)
        raise UpgradeError("rebuilt index failed verification: " + "; ".join(problems))
    os.replace(tmp, db_path)
    return stats, counts


def restore(db_path, source=None):
    """Replace db_path with a backup (default: the newest). The current index is backed up first."""
    backups = list_backups(db_path)
    source = source or (backups[0] if backups else None)
    if not source or not os.path.isfile(source):
        raise UpgradeError("no backup to restore")
    safety = backup(db_path, keep=BACKUP_KEEP + 1, label="pre-restore")
    tmp = db_path + ".restore"
    _copy_db(source, tmp)
    os.replace(tmp, db_path)
    return source, safety


# ------------------------------------------------------------ stale writers

_LSTART_FORMATS = ("%a %b %d %H:%M:%S %Y", "%a %d %b %H:%M:%S %Y")


def _parse_lstart(text):
    text = " ".join(text.split())
    for fmt in _LSTART_FORMATS:
        try:
            return time.mktime(time.strptime(text, fmt))
        except ValueError:
            continue
    return None


def parse_ps(output):
    """[(pid, started_epoch, args)] from `ps -eo pid=,lstart=,args=` output."""
    rows = []
    for line in output.splitlines():
        m = re.match(r"\s*(\d+)\s+(\w{3}\s+\w{3}\s+\d+\s+[\d:]{8}\s+\d{4}|\w{3}\s+\d+\s+\w{3}\s+[\d:]{8}\s+\d{4})\s+(.*)$", line)
        if not m:
            continue
        started = _parse_lstart(m.group(2))
        if started is not None:
            rows.append((int(m.group(1)), started, m.group(3)))
    return rows


def code_mtime():
    """Newest modification time of the installed mnemo package."""
    here = os.path.dirname(os.path.abspath(__file__))
    return max(os.path.getmtime(p) for p in glob.glob(os.path.join(here, "**", "*.py"), recursive=True))


def stale_processes(since=None):
    """Long-running mnemo processes (MCP servers, dashboards) started before `since`.

    They still run the old code and write rows in the old shape until restarted.
    """
    since = since or code_mtime()
    try:
        out = subprocess.run(
            ["ps", "-eo", "pid=,lstart=,args="], capture_output=True, text=True,
            env=dict(os.environ, LC_ALL="C"), timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    me = {os.getpid(), os.getppid()}
    return [
        (pid, started, args) for pid, started, args in parse_ps(out)
        if pid not in me and started < since
        and re.search(r"\bmnemo\b.*\b(mcp|dashboard)\b", args)
    ]
