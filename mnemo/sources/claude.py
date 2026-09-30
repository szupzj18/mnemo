import json
import os

from ..model import CLAUDE_EXTRA_RULES, Msg, block_text, clip, norm_ts, strip_envelopes
from .base import Source, decode_cwd_dir


class ClaudeSource(Source):
    name = "claude"

    def root(self):
        return os.path.join(self.home, ".claude", "projects")

    def files(self):
        root = self.root()
        if not os.path.isdir(root):
            return
        for dirpath, _dirs, names in os.walk(root):
            for n in names:
                if n.endswith(".jsonl"):
                    yield os.path.join(dirpath, n)

    def parse(self, path, clip_text=True):
        sid = os.path.splitext(os.path.basename(path))[0]
        cwd = decode_cwd_dir(os.path.basename(os.path.dirname(path)))
        clipf = clip if clip_text else (lambda t: t)
        msgs = []
        for lineno, d in self.read_jsonl(path):
            t = d.get("type")
            if t in ("user", "assistant"):
                m = d.get("message")
                if not isinstance(m, dict):
                    continue
                sid = d.get("sessionId") or sid
                cwd = d.get("cwd") or cwd
                role = m.get("role") or t
                if role not in ("user", "assistant"):
                    continue
                ts = norm_ts(d.get("timestamp"))
                for kind, text in self._content(m.get("content")):
                    raw0 = None
                    stripped = False
                    if role == "user" and kind == "text":
                        raw0 = text
                        text, stripped = strip_envelopes(text, CLAUDE_EXTRA_RULES)
                    present = raw0.strip() if raw0 is not None else text
                    if present:
                        # Pure-envelope blocks are retained verbatim (raw0) with
                        # empty searchable text, so injected records are not lost.
                        msgs.append((
                            lineno,
                            Msg(
                                ts, role, kind, clipf(text),
                                raw=clipf(raw0.strip()) if stripped else None,
                                envelope=stripped,
                            ),
                        ))
            elif t == "summary":
                text = d.get("summary")
                if text:
                    msgs.append(
                        (lineno, Msg(norm_ts(d.get("timestamp")), "user", "summary", clipf(text)))
                    )
        return sid, cwd, msgs

    @staticmethod
    def _content(content):
        if isinstance(content, str):
            return [("text", content)]
        out = []
        if isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                ty = b.get("type")
                if ty == "text":
                    if b.get("text"):
                        out.append(("text", b["text"]))
                elif ty == "thinking":
                    if b.get("thinking"):
                        out.append(("reasoning", b["thinking"]))
                elif ty == "tool_use":
                    args = b.get("input", "")
                    if not isinstance(args, str):
                        args = json.dumps(args, ensure_ascii=False)
                    out.append(("tool_call", "%s(%s)" % (b.get("name", "tool"), args)))
                elif ty == "tool_result":
                    text = block_text(b.get("content"))
                    if text:
                        out.append(("tool_result", text))
        return out
