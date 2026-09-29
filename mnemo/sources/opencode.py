import datetime
import json
import os
import sqlite3

from ..model import Msg, clip, strip_envelopes
from .base import Source

# OpenCode keeps every session in one SQLite database, so a session is
# addressed as "<database path>::<session id>" and reports its own change key
# through Source.records() (see base.py) instead of os.stat on a file.
SCHEME = "::"


def _iso(ms):
    if not ms:
        return ""
    return datetime.datetime.fromtimestamp(
        float(ms) / 1000.0, datetime.timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


class OpenCodeSource(Source):
    name = "opencode"

    def db_path(self):
        return os.path.join(self.home, ".local", "share", "opencode", "opencode.db")

    def _connect(self, path):
        con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
        con.row_factory = sqlite3.Row
        return con

    def records(self):
        """Yield (path, mtime, size) for every OpenCode session.

        The change key is the newest of the session row and its parts, so a new
        or updated part reindexes only its own session, not the whole database.
        """
        path = self.db_path()
        if not os.path.isfile(path):
            return
        con = self._connect(path)
        try:
            parts = {r["session_id"]: (r["n"], r["newest"] or 0) for r in con.execute(
                "SELECT session_id, COUNT(*) AS n, MAX(time_updated) AS newest"
                " FROM part GROUP BY session_id")}
            sessions = con.execute(
                "SELECT id, time_updated FROM session_v2").fetchall()
        except sqlite3.Error:
            return  # an unreadable database is skipped like a corrupt session file
        finally:
            con.close()
        for s in sessions:
            count, newest = parts.get(s["id"], (0, 0))
            mtime = max(s["time_updated"] or 0, newest) / 1000.0
            yield path + SCHEME + s["id"], mtime, count

    def files(self):
        for path, _mtime, _size in self.records():
            yield path

    def parse(self, path, clip_text=True):
        db, _, session_id = path.partition(SCHEME)
        clipf = clip if clip_text else (lambda t: t)
        con = self._connect(db)
        try:
            row = con.execute(
                "SELECT directory FROM session_v2 WHERE id = ?", (session_id,)).fetchone()
            cwd = row["directory"] if row else ""
            msgs = []
            # part.rowid doubles as lineno: it is stable across parses, so
            # context windows and --raw reads work like they do for JSONL files.
            for r in con.execute(
                "SELECT p.rowid AS rid, p.time_created AS pts, p.data AS pdata,"
                " m.time_created AS mts, m.data AS mdata"
                " FROM part p JOIN message m ON m.id = p.message_id"
                " WHERE p.session_id = ? ORDER BY p.time_created, p.rowid",
                (session_id,),
            ):
                try:
                    data = json.loads(r["pdata"])
                    message = json.loads(r["mdata"])
                except ValueError:
                    continue
                role = message.get("role") or "assistant"
                ts = _iso(r["mts"] or r["pts"])
                rid = r["rid"]
                kind = data.get("type")
                if kind == "text":
                    text = data.get("text") or ""
                    raw0 = None
                    stripped = False
                    if role == "user":
                        raw0 = text
                        text, stripped = strip_envelopes(text)
                    present = raw0.strip() if raw0 is not None else text
                    if present:
                        # Pure-envelope blocks stay verbatim with empty
                        # searchable text, like the other sources.
                        msgs.append((rid, Msg(
                            ts, role, "text", clipf(text),
                            raw=clipf(raw0.strip()) if stripped else None,
                            envelope=stripped,
                        )))
                elif kind == "reasoning":
                    text = data.get("text") or ""
                    if text:
                        msgs.append((rid, Msg(ts, "assistant", "reasoning", clipf(text))))
                elif kind == "tool":
                    state = data.get("state") or {}
                    args = state.get("input")
                    if args is not None:
                        if not isinstance(args, str):
                            args = json.dumps(args, ensure_ascii=False)
                        msgs.append((rid, Msg(ts, "assistant", "tool_call",
                                              clipf("%s(%s)" % (data.get("tool") or "tool", args)))))
                    out = state.get("output") or state.get("error")
                    if out:
                        if not isinstance(out, str):
                            out = json.dumps(out, ensure_ascii=False)
                        msgs.append((rid, Msg(ts, "tool", "tool_result", clipf(out))))
                elif kind == "patch":
                    files = data.get("files") or []
                    if files:
                        msgs.append((rid, Msg(ts, "tool", "tool_result",
                                              "patched: " + ", ".join(map(str, files)))))
            return session_id, cwd, msgs
        finally:
            con.close()
