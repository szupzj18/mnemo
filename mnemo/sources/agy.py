import collections
import datetime
import os
import sqlite3
from urllib.parse import quote, unquote, urlparse

from ..model import Msg, clip, strip_envelopes
from .base import Source

# Antigravity CLI (`agy`) keeps one SQLite database per conversation,
# ~/.gemini/antigravity-cli/conversations/<conversation id>.db, with one `steps`
# row per step. step_payload is a protobuf message without a published schema;
# the field numbers below were read off databases written by agy 1.3.6:
#
#   step              1 type, 4 status, 5 metadata {1 created_at {1 seconds}}
#   19  user input    {2 text, 3 items [{1 text}]}
#   20  planner reply {1 response, 3 thinking, 7 tool calls [{2 name, 3 JSON args}],
#                      8 response as shown}
#   30  checkpoint    {5 summary of the conversation so far}
#   31  tool error    {2 message}
#   114 notification  {1 text}
#   140 tool step     {2 result {1 output}}
#   trajectory_metadata_blob "main": {1 workspace {1 URI}, 7 workspace URI}
#
# The step's idx is its lineno: it is stable, so context and --raw reads work as
# they do for JSONL lines. Unknown step types and fields are skipped.
USER_INPUT, PLANNER_RESPONSE, CHECKPOINT, NOTIFICATION, TOOL = 14, 15, 23, 101, 132
# Also seen, and skipped on purpose: 17, API errors ("API error (attempt 1): UNAVAILABLE").
KNOWN_STEPS = {USER_INPUT, PLANNER_RESPONSE, 17, CHECKPOINT, NOTIFICATION, TOOL}

# The change key and identity of a database together with its write-ahead log.
_Stat = collections.namedtuple("_Stat", "st_dev st_ino st_size st_mtime st_mtime_ns st_atime_ns")


def _varint(data, i):
    value = shift = 0
    while True:
        if i >= len(data) or shift > 63:
            raise ValueError("truncated protobuf varint")
        b = data[i]
        i += 1
        value |= (b & 0x7F) << shift
        if b < 0x80:
            return value, i
        shift += 7


def _fields(data):
    """Protobuf wire format -> {field number: [int for varints, bytes otherwise]}."""
    out = {}
    i, n = 0, len(data)
    while i < n:
        key, i = _varint(data, i)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, i = _varint(data, i)
        elif wire == 2:
            size, i = _varint(data, i)
            value, i = data[i:i + size], i + size
        elif wire in (1, 5):
            size = 8 if wire == 1 else 4
            value, i = data[i:i + size], i + size
        else:
            raise ValueError("unsupported protobuf wire type %d" % wire)
        if number == 0 or i > n:
            raise ValueError("malformed protobuf field %d" % number)
        out.setdefault(number, []).append(value)
    return out


def _message(fields, number):
    """The first sub-message under number, decoded ({} when absent)."""
    values = fields.get(number)
    return _fields(values[0]) if values and isinstance(values[0], bytes) else {}


def _text(fields, number):
    values = fields.get(number)
    if values and isinstance(values[0], bytes):
        return values[0].decode("utf-8", "replace")
    return ""


def _iso(metadata):
    seconds = (_message(metadata, 1).get(1) or [0])[0]
    if not isinstance(seconds, int) or seconds <= 0:
        return ""
    try:
        when = datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return ""
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _uri_path(uri):
    return unquote(urlparse(uri).path) if uri.startswith("file://") else uri


class AgySource(Source):
    name = "agy"
    # Like Pi's: the config dir is the account, the folder below it holds the logs.
    CONFIG_DIR = ".gemini"
    LOG_DIRS = (os.path.join("antigravity-cli", "conversations"),)
    LOG_SUFFIX = ".db"
    # Steps are updated in place (status, results), often without the file growing.
    EXACT_MTIME = True

    def _connect(self, path):
        # Read-only, and quoted: "#", "?" and "%" in the path are URI syntax otherwise.
        return sqlite3.connect("file:%s?mode=ro" % quote(path), uri=True)

    def stat(self, path):
        """The database's stat with its write-ahead log folded into the change key.

        agy writes new steps to <id>.db-wal; the .db itself changes only when
        that log is checkpointed into it. An empty log holds nothing: agy leaves
        one beside every conversation and touches them all when it starts.
        """
        st = os.stat(path)
        try:
            wal = os.stat(path + "-wal")
        except OSError:
            return st
        if not wal.st_size:
            return st
        return _Stat(st.st_dev, st.st_ino, st.st_size + wal.st_size, max(st.st_mtime, wal.st_mtime),
                     max(st.st_mtime_ns, wal.st_mtime_ns), st.st_atime_ns)

    def snapshot(self, src, dest):
        """A consistent copy of a live database, its WAL included, as one file.

        Copying the bytes could catch agy mid-checkpoint. SQLite's backup reads a
        snapshot instead; switching the copy to a rollback journal makes it whole
        without a -wal beside it. Once agy has checkpointed (an idle conversation)
        the copy is as large as the .db, so the archive sees it as up to date.
        """
        try:
            con = self._connect(src)
            try:
                out = sqlite3.connect(dest)
                try:
                    con.backup(out)
                    out.execute("PRAGMA journal_mode=DELETE")
                finally:
                    out.close()
            finally:
                con.close()
        except sqlite3.Error as exc:
            # Not a database (yet): skip this copy, like an unreadable file.
            raise OSError("cannot copy %s: %s" % (src, exc))
        finally:
            for leftover in (dest + "-journal", dest + "-wal", dest + "-shm"):
                if os.path.exists(leftover):
                    os.remove(leftover)

    def parse(self, path, clip_text=True):
        clipf = clip if clip_text else (lambda t: t)
        con = self._connect(path)
        try:
            row = con.execute("SELECT cascade_id FROM trajectory_meta LIMIT 1").fetchone()
            sid = (row[0] if row else "") or os.path.splitext(os.path.basename(path))[0]
            row = con.execute("SELECT data FROM trajectory_metadata_blob WHERE id = 'main'").fetchone()
            meta = _fields(row[0]) if row and isinstance(row[0], bytes) else {}
            cwd = _uri_path(_text(meta, 7) or _text(_message(meta, 1), 1))
            msgs = []
            steps = unreadable = known = 0
            for idx, step_type, payload in con.execute(
                "SELECT idx, step_type, step_payload FROM steps ORDER BY idx"
            ):
                steps += 1
                known += step_type in KNOWN_STEPS
                try:
                    step = _fields(payload or b"")
                    ts = _iso(_message(step, 5))
                    msgs.extend((idx, m) for m in self._messages(step_type, step, ts, clipf))
                except ValueError:
                    unreadable += 1
            # Another format, not an empty conversation: say so rather than index nothing.
            if steps and unreadable == steps:
                raise ValueError("no readable steps (agy changed its step format?)")
            if steps and not known:
                raise ValueError("no known step types (agy renumbered them?)")
            return sid, cwd, msgs
        finally:
            con.close()

    @staticmethod
    def _messages(step_type, step, ts, clipf):
        if step_type == USER_INPUT:
            user = _message(step, 19)
            text = _text(user, 2) or "\n".join(
                t for t in (_text(_fields(item), 1) for item in user.get(3, [])
                            if isinstance(item, bytes)) if t)
            clean, stripped = strip_envelopes(text)
            if text.strip():
                yield Msg(ts, "user", "text", clipf(clean),
                          raw=clipf(text.strip()) if stripped else None, envelope=stripped)
        elif step_type == PLANNER_RESPONSE:
            reply = _message(step, 20)
            thinking = _text(reply, 3)
            if thinking:
                yield Msg(ts, "assistant", "reasoning", clipf(thinking))
            text = _text(reply, 8) or _text(reply, 1)
            if text:
                yield Msg(ts, "assistant", "text", clipf(text))
            for call in reply.get(7, []):
                if isinstance(call, bytes):
                    call = _fields(call)
                    yield Msg(ts, "assistant", "tool_call",
                              clipf("%s(%s)" % (_text(call, 2) or "tool", _text(call, 3))))
        elif step_type == TOOL:
            out = _text(_message(_message(step, 140), 2), 1) or _text(_message(step, 31), 2)
            if out:
                yield Msg(ts, "tool", "tool_result", clipf(out))
        elif step_type == CHECKPOINT:
            summary = _text(_message(step, 30), 5)
            if summary:
                yield Msg(ts, "assistant", "summary", clipf(summary))
        elif step_type == NOTIFICATION:
            # agy telling the agent a background task finished: kept verbatim,
            # left out of default search like Claude Code's <task-notification>.
            note = _text(_message(step, 114), 1).strip()
            if note:
                yield Msg(ts, "user", "text", "", raw=clipf(note), envelope=True)
