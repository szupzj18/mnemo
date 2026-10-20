import datetime
import json
import os
from urllib.parse import unquote

from ..model import Msg, clip, strip_envelopes
from .base import Source

# Grok CLI (`grok`, "Grok Build") keeps one folder per session,
# ~/.grok/sessions/<URL-encoded cwd>/<session id>/ ($GROK_HOME moves ~/.grok).
# Its docs name updates.jsonl in that folder the authoritative conversation log,
# appended to as the session runs; chat_history.jsonl beside it is the model's
# context (no timestamps, synthetic reminders), and the rest is metadata. Each
# line of updates.jsonl is an Agent Client Protocol session update. The shapes
# below were read off logs written by grok 1.0.62:
#
#   {"timestamp": <unix seconds>, "method": "session/update" (or "_x.ai/session/update"),
#    "params": {"sessionId", "_meta": {"agentTimestampMs", "promptId", ...},
#               "update": {"sessionUpdate": <kind>, ...}}}
#
#   user_message_chunk   content {type "text", text, _meta {displayText}}; text may carry
#                        a <system-reminder> ahead of what was typed (displayText)
#   agent_thought_chunk  content {text}, the reasoning summary
#   agent_message_chunk  content {text}
#   tool_call            toolCallId, rawInput {...}, _meta {"x.ai/tool" {name}}; a search
#                        the server runs has rawInput {variant "WebSearch", backend}
#   tool_call_update     status "completed" | "failed", content [{content {text}}] as shown
#                        (grep shows only "Found N matches"), rawOutput {type "Bash"
#                        {output_for_prompt}, "GrepSearch" {stdout as a list of bytes},
#                        "ReadFile" {FileContent {content}}, "ListDir" {Content {content}
#                        | NotFound}, ...}; one without a status restates the input
#   session_recap        summary: grok's recap of the session so far
#   turn_completed, hook_execution, current_mode_update, scheduled_task_created: skipped
#
# Every message was one line. Consecutive agent chunks of one prompt are joined
# all the same, in case a later grok logs a reply as it streams. The line number
# is the lineno. Unknown kinds and fields are skipped.
LOG_NAME = "updates.jsonl"
AGENT_CHUNKS = {"agent_message_chunk": "text", "agent_thought_chunk": "reasoning"}
KNOWN = set(AGENT_CHUNKS) | {"user_message_chunk", "tool_call", "tool_call_update", "session_recap",
                             "turn_completed", "hook_execution", "current_mode_update",
                             "scheduled_task_created"}
FINISHED = ("completed", "failed")
# rawOutput fields that hold a tool's output, best first.
OUTPUT_KEYS = ("output_for_prompt", "content", "stdout", "output", "stderr", "error", "message")


def _iso(ms, seconds):
    try:
        value = float(ms) / 1000.0 if ms else float(seconds)
        if value <= 0:
            return ""
        return datetime.datetime.fromtimestamp(value, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _dict(value):
    return value if isinstance(value, dict) else {}


def _text(value):
    """A string, or bytes logged as a list of integers (GrepSearch's stdout)."""
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value and all(isinstance(b, int) and 0 <= b < 256 for b in value):
        return bytes(value).decode("utf-8", "replace")
    return ""


def _output(raw):
    """A tool's output from its rawOutput: a text field, or one inside a variant
    such as {"FileContent": {"content": ...}} or {"NotFound": "..."}."""
    if not isinstance(raw, dict):
        return _text(raw)
    for key in OUTPUT_KEYS:
        text = _text(raw.get(key))
        if text:
            return text
    for key, value in raw.items():
        if key[:1].isupper():
            text = _output(value)
            if text:
                return text
    return ""


def _result(update):
    raw = update.get("rawOutput")
    text = _output(raw)
    if not text:
        blocks = update.get("content") if isinstance(update.get("content"), list) else []
        text = "\n".join(t for t in (_text(_dict(_dict(b).get("content")).get("text")) for b in blocks) if t)
    if text or not raw:
        return text
    # Structured results, such as a server-side search's {"action": {"query", "sources"}}.
    if isinstance(raw, dict) and isinstance(raw.get("action"), dict):
        raw = raw["action"]
    return json.dumps(raw, ensure_ascii=False)


def _listdir(path):
    try:
        return sorted(os.listdir(path))
    except OSError:
        return []


class GrokSource(Source):
    name = "grok"
    # ~/.grok, one ~/.grok-<name> per extra home, and $GROK_HOME.
    CONFIG_DIR = ".grok"
    CONFIG_ENV = "GROK_HOME"
    LOG_DIRS = ("sessions",)
    MULTI_ACCOUNT = True

    def walk(self, root):
        """<root>/<encoded cwd>/<session id>/updates.jsonl: nothing deeper (compaction
        checkpoints) and none of the folders' other files."""
        for group in _listdir(root):
            for session in _listdir(os.path.join(root, group)):
                path = os.path.join(root, group, session, LOG_NAME)
                if os.path.isfile(path):
                    yield path

    def session_name(self, path):
        # Every log is called updates.jsonl; the folder is named after the session.
        return os.path.basename(os.path.dirname(path))

    def parse(self, path, clip_text=True):
        clipf = clip if clip_text else (lambda t: t)
        folder = os.path.dirname(path)
        sid = os.path.basename(folder)
        cwd = self._cwd(os.path.dirname(folder))
        msgs = []
        tools = {}  # toolCallId -> [name, call indexed, result indexed]
        run = None  # agent chunks to join: [lineno, kind, ts, promptId, texts]
        records = known = 0
        for lineno, d in self.read_jsonl(path):
            params = _dict(d.get("params")) if isinstance(d, dict) else {}
            update = params.get("update")
            if not isinstance(update, dict):
                continue
            records += 1
            kind = update.get("sessionUpdate")
            known += kind in KNOWN
            sid = params.get("sessionId") or sid
            meta = _dict(params.get("_meta"))
            ts = _iso(meta.get("agentTimestampMs"), d.get("timestamp"))
            if kind in AGENT_CHUNKS:
                text = _text(_dict(update.get("content")).get("text"))
                if run and run[1] == kind and run[3] == meta.get("promptId"):
                    run[4].append(text)
                    continue
                self._flush(run, msgs, clipf)
                run = [lineno, kind, ts, meta.get("promptId"), [text]]
                continue
            self._flush(run, msgs, clipf)
            run = None
            if kind == "user_message_chunk":
                msg = self._user(_dict(update.get("content")), ts, clipf)
                if msg:
                    msgs.append((lineno, msg))
            elif kind in ("tool_call", "tool_call_update"):
                msgs.extend((lineno, m) for m in self._tool(update, tools, ts, clipf))
            elif kind == "session_recap":
                summary = _text(update.get("summary")).strip()
                if summary:
                    msgs.append((lineno, Msg(ts, "assistant", "summary", clipf(summary))))
        self._flush(run, msgs, clipf)
        # Another format, not an empty session: say so rather than index nothing.
        if records and not known:
            raise ValueError("no known session updates (grok changed its log format?)")
        return sid, cwd, msgs

    @staticmethod
    def _cwd(group):
        """The group folder's name decoded, or the .cwd file grok writes in it when
        the encoded path would be too long for a folder name (per its docs)."""
        try:
            with open(os.path.join(group, ".cwd"), encoding="utf-8", errors="replace") as f:
                cwd = f.read().strip()
            if cwd:
                return cwd
        except OSError:
            pass
        return unquote(os.path.basename(group))

    @staticmethod
    def _flush(run, msgs, clipf):
        if run:
            lineno, kind, ts, _, texts = run
            text = "".join(texts)
            if text.strip():
                msgs.append((lineno, Msg(ts, "assistant", AGENT_CHUNKS[kind], clipf(text))))

    @staticmethod
    def _user(content, ts, clipf):
        text = _text(content.get("text"))
        if not text.strip():
            return None
        clean, stripped = strip_envelopes(text)
        # What was typed, when grok put something ahead of it (a scheduled task's reminder).
        shown = _text(_dict(content.get("_meta")).get("displayText")).strip()
        if shown and shown != clean and shown in text:
            clean, stripped = shown, True
        return Msg(ts, "user", "text", clipf(clean), raw=clipf(text.strip()) if stripped else None,
                   envelope=stripped)

    @staticmethod
    def _tool(update, tools, ts, clipf):
        """A call once its input shows up (or at its result, if it never does), and
        the result once it has finished."""
        state = tools.setdefault(update.get("toolCallId") or "", [None, False, False])
        raw_in = update.get("rawInput")
        if not state[0]:
            state[0] = (_dict(_dict(update.get("_meta")).get("x.ai/tool")).get("name")
                        or _dict(raw_in).get("variant") or update.get("title"))
        finished = update.get("status") in FINISHED
        if not state[1] and (raw_in is not None or finished):
            state[1] = True
            args = raw_in if isinstance(raw_in, str) or raw_in is None else json.dumps(raw_in, ensure_ascii=False)
            yield Msg(ts, "assistant", "tool_call", clipf("%s(%s)" % (state[0] or "tool", args or "")))
        if finished and not state[2]:
            state[2] = True
            text = _result(update)
            if text:
                yield Msg(ts, "tool", "tool_result", clipf(text))
