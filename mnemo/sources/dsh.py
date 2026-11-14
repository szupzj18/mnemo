"""DeepSeek Harness (DSH) sessions.

DSH keeps one log per session under ``$DSH_HOME/sessions`` (default
``~/.dsh/sessions``), grouped by a human-readable project key and then by
session id::

    ~/.dsh/sessions/--Users-me-app--/session-<uuid>/session.v4.jsonl.zstd

Each file starts with a ``session`` header (id, cwd) and continues with
append-only events. The current format version is 4; older sessions may still
live as ``session.v3.jsonl.zstd`` until DSH opens them, so the highest
generation in a session directory is the authoritative one. The backend
compresses logs with Zstandard by default (``.jsonl.zstd``); a plaintext
backend writes ``.jsonl`` instead.

Only the model-visible transcript is indexed. DSH also records injected
context as user messages (workspace instructions, the skill catalog, time and
runtime snapshots, approval notices); those stay verbatim in ``raw`` with
empty searchable text, like the other agents' injected envelopes. Assistant
messages carry text, reasoning and tool-call blocks; tool results are their own
events, and a tool call is logged once inside the assistant message (the
parallel ``tool/call`` event restates it). Compaction rewrites the model
surface with ``surfaceOp: {op: replace, ...}``; the append-origin events stay
the human transcript, so replacements are skipped.
"""
import datetime
import json
import os
import re

from ..model import Msg, block_text, clip, norm_ts, strip_envelopes
from .base import Source, SourceUnavailable, find_zstd

# session.v<generation>.jsonl[.zstd]; the backend writes exactly one encoding.
_GENERATION = re.compile(r"^session\.v(\d+)\.jsonl(\.zstd)?$")


class DshSource(Source):
    name = "dsh"
    # ~/.dsh, or $DSH_HOME.
    CONFIG_DIR = ".dsh"
    CONFIG_ENV = "DSH_HOME"
    LOG_DIRS = ("sessions",)
    # One file per session, but the filename is the format generation and the
    # session id lives in the directory above it.
    LOG_SUFFIX = ".jsonl"
    # DSH appends one Zstandard frame per write to a live log.
    PARTIAL_TAIL = True

    def walk(self, root):
        """Yield the authoritative generation of every session, by its logical path.

        A compressed ``session.v4.jsonl.zstd`` is yielded as ``session.v4.jsonl``
        so the archive can keep its own ``.zstd`` copy and reads resolve the
        sibling through ``actual()``.
        """
        try:
            projects = sorted(os.listdir(root))
        except OSError:
            return
        for project in projects:
            project_dir = os.path.join(root, project)
            if not os.path.isdir(project_dir):
                continue
            try:
                sessions = sorted(os.listdir(project_dir))
            except OSError:
                continue
            for session in sessions:
                session_dir = os.path.join(project_dir, session)
                if not os.path.isdir(session_dir):
                    continue
                try:
                    names = os.listdir(session_dir)
                except OSError:
                    continue
                best = None
                for name in names:
                    match = _GENERATION.match(name)
                    if not match:
                        continue
                    version = int(match.group(1))
                    compressed = bool(match.group(2))
                    if best is None or (version, compressed) > best:
                        best = (version, compressed)
                if best is not None:
                    yield os.path.join(session_dir, "session.v%d.jsonl" % best[0])

    def actual(self, path):
        """The file on disk behind a logical generation path (compressed by default)."""
        if path.endswith(".zstd"):
            return path
        compressed = path + ".zstd"
        if os.path.exists(compressed):
            return compressed
        return path

    def records(self):
        # Every DSH log is compressed by default, so a missing decompressor skips
        # the whole source with one warning instead of one skipped file per sync.
        zstd = find_zstd()
        for record in super(DshSource, self).records():
            if not zstd and self.actual(record[0]) != record[0]:
                raise SourceUnavailable("compressed DeepSeek Harness logs require the optional zstd executable")
            yield record

    def session_name(self, path):
        """A session is named by its directory (the encoded session id), not by
        the generation filename every session shares."""
        return os.path.basename(os.path.dirname(path))

    def parse(self, path, clip_text=True):
        sid = os.path.basename(os.path.dirname(path))
        cwd = ""
        clipf = clip if clip_text else (lambda t: t)
        msgs = []
        for lineno, d in self.read_jsonl(path):
            t = d.get("type")
            if t == "session":
                sid = d.get("id") or sid
                cwd = d.get("cwd") or cwd
                continue
            if t == "compaction/summary":
                text = _summary_text(d.get("data") or {})
                if text:
                    msgs.append((lineno, Msg(_ts(d), "user", "summary", clipf(text))))
                continue
            if t not in ("user/message", "assistant/message", "tool/result"):
                continue
            # A replacement node is model-only; the append-origin transcript of
            # the conversation it shadowed is still in the log.
            op = d.get("surfaceOp")
            if isinstance(op, dict):
                continue
            data = d.get("data") or {}
            if not isinstance(data, dict):
                continue
            if t == "user/message":
                msg, role = data, "user"
            else:
                msg, role = data.get("message"), ("assistant" if t == "assistant/message" else "tool")
            if not isinstance(msg, dict):
                continue
            ts = _ts(d)
            source_kind = _source_kind(msg)
            injected = role == "user" and source_kind != "user"
            for kind, text in _content(msg.get("content"), role):
                if not text or not text.strip():
                    continue
                if injected:
                    # Kept verbatim for session/raw reads, hidden from search.
                    msgs.append((lineno, Msg(ts, role, kind, "", raw=clipf(text), envelope=True)))
                    continue
                raw = None
                stripped = False
                if role == "user" and kind == "text":
                    raw = text
                    text, stripped = strip_envelopes(text)
                present = raw.strip() if raw is not None else text.strip()
                if not present:
                    continue
                msgs.append((
                    lineno,
                    Msg(ts, role, kind, clipf(text.strip()),
                        raw=clipf(raw.strip()) if stripped else None, envelope=stripped),
                ))
        return sid, cwd, msgs


def _source_kind(message):
    source = message.get("source")
    if isinstance(source, dict):
        kind = source.get("kind")
        if isinstance(kind, str):
            return kind
    return "user"


def _content(content, role):
    """[(kind, text)] for one DSH message's content blocks.

    V4 tool results are a ``text`` block on a role-``tool`` message; a v3 log
    keeps the same result as a ``tool-result`` block whose ``content`` nests
    text, so both shapes go through here.
    """
    if isinstance(content, str):
        return [("tool_result" if role == "tool" else "text", content)]
    out = []
    if not isinstance(content, list):
        return out
    for block in content:
        if isinstance(block, str):
            out.append(("tool_result" if role == "tool" else "text", block))
            continue
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            text = block.get("text")
            if isinstance(text, str) and text:
                out.append(("tool_result" if role == "tool" else "text", text))
        elif kind == "reasoning":
            text = block.get("text") or block.get("thinking")
            if isinstance(text, str) and text:
                out.append(("reasoning", text))
        elif kind in ("tool-call", "tool-call-delta", "toolCall", "tool_use"):
            name = block.get("name") or "tool"
            args = block.get("arguments", block.get("input", ""))
            if not isinstance(args, str):
                args = json.dumps(args, ensure_ascii=False)
            out.append(("tool_call", "%s(%s)" % (name, args)))
        elif kind == "tool-result":
            text = _result_text(block.get("content"))
            if text:
                out.append(("tool_result", text))
        # Attachments (image, file) carry no searchable text.
    return out


def _result_text(content):
    """Plain text out of a tool result's content (a string, blocks or nested result)."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if content.get("type") == "text" and isinstance(content.get("text"), str):
            return content["text"]
        text = _result_text(content.get("content"))
        if text:
            return text
        return content["text"] if isinstance(content.get("text"), str) else ""
    parts = []
    if isinstance(content, list):
        for item in content:
            text = _result_text(item)
            if text:
                parts.append(text)
    return "\n".join(p for p in parts if p)


def _summary_text(data):
    summary = data.get("summary")
    if isinstance(summary, str):
        return summary
    if isinstance(summary, dict):
        text = block_text(summary.get("content"))
        return text or (summary.get("text") if isinstance(summary.get("text"), str) else "")
    return ""


def _ts(event):
    """The event's epoch-millisecond ``time`` as sortable UTC, else an ISO field."""
    value = event.get("time")
    if isinstance(value, (int, float)) and value > 0:
        try:
            when = datetime.datetime.fromtimestamp(value / 1000.0, datetime.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return ""
        return when.strftime("%Y-%m-%dT%H:%M:%SZ")
    return norm_ts(event.get("timestamp"))
