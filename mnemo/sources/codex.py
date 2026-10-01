import json
import hashlib
import os
import shutil
from collections import Counter

from ..model import Msg, clip, norm_ts, strip_envelopes, CODEX_EXTRA_RULES
from .base import Source, SourceUnavailable

ADAPTER_VERSION = "3"


def message_key(turn, msg):
    return turn, msg.role, msg.kind, hashlib.sha256(msg.stored.encode("utf-8")).digest()


def bound_message(msg):
    msg.text = clip(msg.text)
    if msg.raw is not None:
        msg.raw = clip(msg.raw)

SKIP_ROLES = {"developer", "system"}


class CodexSource(Source):
    name = "codex"

    def roots(self):
        base = os.path.join(self.home, ".codex")
        return [
            os.path.join(base, "sessions"),
            os.path.join(base, "archived_sessions"),
        ]

    def files(self):
        for root in self.roots():
            if not os.path.isdir(root):
                continue
            for dirpath, _dirs, names in os.walk(root):
                for n in names:
                    if n.endswith(".jsonl"):
                        yield os.path.join(dirpath, n)
                    elif n.endswith(".jsonl.zst") and n[:-4] not in names:
                        yield os.path.join(dirpath, n[:-4])

    def records(self):
        paths = list(self.files())
        if any(not os.path.exists(p) for p in paths) and not shutil.which("zstd"):
            raise SourceUnavailable("compressed Codex logs require the optional zstd executable; index retained")
        for path in paths:
            actual = path if os.path.exists(path) else path + ".zst"
            try:
                stat = os.stat(actual)
            except OSError:
                continue
            yield path, stat.st_mtime, stat.st_size

    def parse(self, path, clip_text=True):
        sid = ""
        cwd = ""
        # Compare untruncated representations before clipping; different long outputs
        # can share their first 20k characters.
        clipf = lambda t: t
        msgs = []
        completed = []
        raw_keys = Counter()
        raw_ids = set()
        turn = ""
        guardian = False
        for lineno, d in self.read_jsonl(path):
            t = d.get("type")
            ts = norm_ts(d.get("timestamp"))
            p = d.get("payload") or {}
            if t == "turn_context":
                turn = p.get("turn_id") or turn
            if t == "event_msg":
                if p.get("type") == "turn_started":
                    turn = p.get("turn_id") or str(lineno)
                elif p.get("type") == "item_completed":
                    for msg in self._completed(p.get("item") or {}, ts, guardian):
                        scope = p.get("turn_id") or turn
                        key = message_key(scope, msg)
                        if clip_text:
                            bound_message(msg)
                        completed.append((lineno, scope, (p.get("item") or {}).get("id"), key, msg))
                continue
            if t == "session_meta":
                p = d.get("payload") or {}
                sid = p.get("session_id") or p.get("id") or sid
                cwd = p.get("cwd") or cwd
                # An approval review Codex runs on its own before a risky command:
                # every "user" message is generated (the history under review).
                src = p.get("source")
                guardian = guardian or (isinstance(src, dict) and isinstance(src.get("subagent"), dict)
                                        and src["subagent"].get("other") == "guardian")
                continue
            if t != "response_item":
                continue
            p = d.get("payload")
            if not isinstance(p, dict):
                continue
            pt = p.get("type")
            before = len(msgs)
            if pt in ("message", "agent_message"):
                role = "assistant" if pt == "agent_message" else p.get("role")
                if role in SKIP_ROLES:
                    continue
                if role not in ("user", "assistant"):
                    continue
                raw0 = self._message_text(p.get("content"))
                if role == "user" and guardian:
                    clean, stripped = "", bool(raw0.strip())
                elif role == "user":
                    clean, stripped = strip_envelopes(raw0, CODEX_EXTRA_RULES)
                else:
                    clean, stripped = raw0.strip(), False
                if raw0.strip():
                    # Pure-envelope messages (clean == "") are kept verbatim so
                    # nothing is lost; their empty searchable text just never
                    # matches a query.
                    msgs.append((
                        lineno,
                        Msg(
                            ts, role, "text", clipf(clean),
                            raw=clipf(raw0.strip()) if stripped else None,
                            envelope=stripped,
                        ),
                    ))
            elif pt == "reasoning":
                text = self._reasoning_text(p)
                if text:
                    msgs.append((lineno, Msg(ts, "assistant", "reasoning", clipf(text))))
            elif pt in ("function_call", "custom_tool_call"):
                args = p.get("arguments", "")
                if not isinstance(args, str):
                    args = json.dumps(args, ensure_ascii=False)
                name = p.get("name") or pt
                msgs.append((lineno, Msg(ts, "assistant", "tool_call", clipf("%s(%s)" % (name, args)))))
            elif pt in ("function_call_output", "custom_tool_call_output"):
                out = p.get("output", "")
                if not isinstance(out, str):
                    out = json.dumps(out, ensure_ascii=False)
                if out:
                    msgs.append((lineno, Msg(ts, "tool", "tool_result", clipf(out))))
            for _, msg in msgs[before:]:
                raw_keys[message_key(turn, msg)] += 1
                item_id = p.get("id") or p.get("call_id")
                if item_id:
                    raw_ids.add((turn, item_id, msg.kind))
                if clip_text:
                    bound_message(msg)
        # Paginated logs retain both model response items and completed display items.
        # Consume duplicates within each turn, preserving genuinely repeated messages.
        for lineno, scope, item_id, key, msg in completed:
            if item_id and (scope, item_id, msg.kind) in raw_ids:
                if raw_keys[key]:
                    raw_keys[key] -= 1
            elif raw_keys[key]:
                raw_keys[key] -= 1
            else:
                msgs.append((lineno, msg))
        msgs.sort(key=lambda pair: pair[0])
        return sid, cwd, msgs

    @classmethod
    def _completed(cls, item, ts, guardian):
        kind = item.get("type")
        if kind in ("UserMessage", "AgentMessage"):
            text = cls._message_text(item.get("content"))
            role = "user" if kind == "UserMessage" else "assistant"
            clean, stripped = ("", bool(text.strip())) if guardian and role == "user" else (
                strip_envelopes(text, CODEX_EXTRA_RULES) if role == "user" else (text.strip(), False))
            if text.strip():
                yield Msg(ts, role, "text", clean, raw=text.strip() if stripped else None, envelope=stripped)
        elif kind == "Plan":
            text = item.get("text") or ""
            if text:
                yield Msg(ts, "assistant", "summary", text)
        elif kind == "Reasoning":
            text = "\n".join(item.get("summary_text") or [])
            if text:
                yield Msg(ts, "assistant", "reasoning", text)
        elif kind == "FunctionCallOutput":
            output = item.get("output") or ""
            text = cls._message_text(output) if isinstance(output, list) else output
            if text:
                yield Msg(ts, "tool", "tool_result", text)
        elif kind == "CommandExecution":
            command = item.get("command") or []
            yield Msg(ts, "assistant", "tool_call", "exec_command(%s)" % json.dumps(command, ensure_ascii=False))
            output = item.get("aggregated_output")
            if output is None:
                output = "\n".join(v for v in (item.get("stdout"), item.get("stderr")) if v)
            if output:
                yield Msg(ts, "tool", "tool_result", output)
        elif kind == "McpToolCall":
            name = "%s/%s" % (item.get("server", ""), item.get("tool", ""))
            yield Msg(ts, "assistant", "tool_call", "%s(%s)" %
                      (name, json.dumps(item.get("arguments"), ensure_ascii=False)))
            result = item.get("result") or item.get("error")
            if result:
                yield Msg(ts, "tool", "tool_result", json.dumps(result, ensure_ascii=False))

    @staticmethod
    def _message_text(content):
        parts = []
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and isinstance(b.get("text"), str):
                    parts.append(b["text"])
                elif isinstance(b, str):
                    parts.append(b)
        elif isinstance(content, str):
            parts.append(content)
        return "\n".join(p for p in parts if p)

    @staticmethod
    def _reasoning_text(p):
        if p.get("encrypted_content") and not p.get("summary"):
            return ""
        summary = p.get("summary")
        if isinstance(summary, str):
            return summary
        if isinstance(summary, list):
            parts = []
            for item in summary:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item, str):
                    parts.append(item)
            return "\n".join(parts)
        if isinstance(p.get("text"), str):
            return p["text"]
        return ""
