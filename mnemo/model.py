import datetime
import re
from dataclasses import dataclass
from typing import Optional

MAX_TEXT = 20000

CJK_RUN = re.compile(
    r"[⺀-⻿㐀-䶿⼀-㈀-鿿＀-￯\U0001F200-\U0001FAFF]+"
)


@dataclass
class Msg:
    ts: str
    role: str  # user / assistant / tool
    kind: str  # text / reasoning / tool_call / tool_result / summary
    text: str  # searchable text with injected envelopes stripped
    raw: Optional[str] = None  # original text as found in the session file
    envelope: bool = False  # raw carried an injected envelope that text drops

    @property
    def stored(self):
        """Body column value: prefer the verbatim original when present."""
        return self.raw if self.raw is not None else self.text


def norm_ts(value):
    """Normalize an ISO timestamp to sortable UTC 'YYYY-MM-DDTHH:MM:SSZ'."""
    if not value:
        return ""
    t = value.strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        return value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def clip(text):
    if not isinstance(text, str):
        text = str(text)
    if len(text) > MAX_TEXT:
        return text[:MAX_TEXT] + " …[truncated]"
    return text


def cjk_grams(text):
    """Unigrams + bigrams of every CJK run for substring-ish FTS matching."""
    grams = []
    for m in CJK_RUN.finditer(text):
        run = m.group(0)
        grams.extend(list(run))
        grams.extend(run[i : i + 2] for i in range(len(run) - 1))
    return " ".join(grams)


def has_cjk(text):
    return bool(CJK_RUN.search(text))


# ------------------------------------------------------------------- envelopes
#
# Agents inject boilerplate into *user* messages: workspace instructions, plugin
# suggestions, ambient browser state, slash-command IO, approval-review wraps.
# It is real content and stays verbatim in the `body` column, but it must not
# pollute search or be mistaken for the session's task. Each source strips the
# well-delimited wrappers into the searchable `text` column; free-form user
# replies (e.g. <send_user_message_question_reply>) are never envelopes.

# Paired XML tags, possibly with attributes, shared by claude and codex.
_XML_ENVELOPE_TAGS = (
    "environment_context",
    "recommended_plugins",
    "in-app-browser-context",
    "local-command-caveat",
    "local-command-stdout",
    "local-command-stderr",
    "system-reminder",
    "turn_aborted",
    "skill",
    "command-name",
    "command-message",
    "command-args",
    "command-stdout",
    "command-stderr",
    # Codex guardian (approval review) prompts: the tool descriptions under review.
    "guardian_tool_descriptions",
)


def _xml_envelope(tag):
    return re.compile(r"<%s(?:\s[^>]*)?>.*?</%s>" % (tag, tag), re.S)


_XML_RULES = tuple(_xml_envelope(t) for t in _XML_ENVELOPE_TAGS)

# Source-specific, non-XML envelopes.
CODEX_EXTRA_PATTERNS = (
    # "# AGENTS.md instructions for <cwd>\n\n<INSTRUCTIONS>…</INSTRUCTIONS>"
    r"#\s*AGENTS\.md\s+instructions\b.*?</INSTRUCTIONS>",
    # Approval/assessment sub-rollouts: the whole user message is wrapped agent
    # history, bounded by a fixed opener and a trailing APPROVAL … END marker.
    r"The following is the Codex agent history\b.*?>>>\s*APPROVAL[A-Z ]*?END",
)
CODEX_EXTRA_RULES = tuple(re.compile(p, re.S) for p in CODEX_EXTRA_PATTERNS)

CLAUDE_EXTRA_PATTERNS = (
    # A skill's SKILL.md, injected as its own user message when the skill runs.
    # (Only this isMeta message: /loop prompts are isMeta too, and are the task.)
    r"\ABase directory for this skill:.*",
)
CLAUDE_EXTRA_RULES = tuple(re.compile(p, re.S) for p in CLAUDE_EXTRA_PATTERNS)


def strip_envelopes(text, extra_rules=()):
    """Return (clean, stripped_any). Does not mutate the caller's string."""
    if not text:
        return text, False
    count = 0
    for rx in _XML_RULES + tuple(extra_rules):
        text, n = rx.subn("", text)
        count += n
    return text.strip(), count > 0


# ---------------------------------------------------------------------- titles

# A searchable user message that nevertheless is not a human task prompt.
_TITLE_SKIP_PREFIXES = (
    "the following is the codex agent history",
    "# agents.md instructions",
    "<recommended_plugins",
    "<environment_context",
    "<in-app-browser-context",
    "<turn_aborted",
    "<skill",
    "<local-command-caveat",
    "<system-reminder",
    "fork started",
    "set model to",
)


def make_title(msgs, limit=140):
    """First substantive searchable user prompt of a session, on one line."""
    for _, m in msgs:
        if m.role != "user" or m.kind not in ("text", "summary"):
            continue
        s = m.text.strip()
        low = s.lower()
        if not s or any(low.startswith(p) for p in _TITLE_SKIP_PREFIXES):
            continue
        line = next((ln.strip() for ln in s.splitlines() if ln.strip()), "")
        if line:
            return re.sub(r"\s+", " ", line)[:limit]
    return ""


def first_ts(msgs):
    return msgs[0][1].ts if msgs else ""


def block_text(content):
    """Extract plain text from an Anthropic-style content field (str or blocks)."""
    if isinstance(content, str):
        return content
    parts = []
    if isinstance(content, list):
        for b in content:
            if isinstance(b, str):
                parts.append(b)
            elif isinstance(b, dict):
                if b.get("type") == "text" and isinstance(b.get("text"), str):
                    parts.append(b["text"])
                elif isinstance(b.get("content"), str):
                    parts.append(b["content"])
    return "\n".join(p for p in parts if p)
