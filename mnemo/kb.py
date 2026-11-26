"""Knowledge base: cards distilled from sessions, and the ledger of what was distilled.

Experimental, behind a switch (MNEMO_KB=on|off, else `mnemo node --kb`, default
off). Design: docs/design/knowledge-base.md.

    <kb dir>/<card id>.md     one card: a header of fields, then a "# title" body
    <kb dir>/digest.jsonl     one line per distilled session range, append-only

The kb dir is $MNEMO_KB_DIR, else ~/.mnemo/kb. Cards and the ledger are source
data, written by agents (cards) and `mnemo digest done` (ledger); the index only
holds rows derived from the cards, via sources/kb.py.

mnemo never calls a model: `digest show` hands an agent the dense parts of a
session, and the agent writes the cards.
"""
import datetime
import json
import math
import os
import re

from .model import CJK_RUN

ENV = "MNEMO_KB"
DIR_ENV = "MNEMO_KB_DIR"
OFF = ("0", "off", "false", "no")
ON = ("1", "on", "true", "yes")
LEDGER = "digest.jsonl"
KIND = "knowledge"

TYPES = ("fact", "howto", "gotcha", "decision", "preference")
REQUIRED = ("type", "scope", "sources", "created", "verified")
OPTIONAL = ("expires", "superseded_by")
LISTS = ("scope", "sources")
CARD_ID = re.compile(r"[a-z0-9][a-z0-9-]*\Z")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
SCOPE = re.compile(r"(global|host:\S+|cwd:/.*)\Z")
# <source>@<host>:<path>[#<lineno>]
SOURCE_REF = re.compile(r"(?P<source>[a-z]+)@(?P<host>[^:\s]+):(?P<path>/[^#]*?)(?:#(?P<lineno>\d+))?\Z")


# ------------------------------------------------------------------ switch

def enabled():
    """MNEMO_KB=on|off for one command, else `mnemo node --kb`, else off."""
    value = os.environ.get(ENV, "").strip().lower()
    if value in OFF:
        return False
    if value in ON:
        return True
    return setting()


def overridden():
    return os.environ.get(ENV, "").strip().lower() in ON + OFF


def setting():
    """This device's saved kb setting (node.json), without creating the file."""
    from . import remote

    try:
        with open(remote._node_path(), encoding="utf-8") as f:
            return json.load(f).get("kb", False) is True
    except (OSError, ValueError, AttributeError):
        return False


def kb_dir(home=None):
    value = os.environ.get(DIR_ENV, "").strip()
    if value:
        return os.path.abspath(os.path.expanduser(value))
    return os.path.join(os.path.expanduser(home or "~"), ".mnemo", "kb")


def default_kinds(kinds):
    """The search kinds to use when the caller gave none: knowledge joins while the kb is on."""
    return list(kinds) + [KIND] if enabled() and KIND not in kinds else list(kinds)


# ------------------------------------------------------------------ cards

def split_card(text):
    """(header lines, body) of a card file; no header gives ([], text)."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return [], text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i], "\n".join(lines[i + 1:])
    return [], text


def parse_header(lines):
    """The card header: `key: value` lines, and list keys followed by `- item` lines.

    A strict YAML subset, so the standard library reads it. Returns (fields, errors).
    """
    fields, errors, current = {}, [], None
    for n, raw in enumerate(lines, 2):
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.lstrip().startswith("- "):
            if current is None:
                errors.append("line %d: list item outside a list field" % n)
            else:
                fields[current].append(line.lstrip()[2:].strip())
            continue
        key, sep, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if not sep or not key or " " in key:
            errors.append("line %d: expected `key: value`" % n)
            current = None
            continue
        if key in fields:
            errors.append("line %d: %s repeated" % (n, key))
        if key in LISTS:
            fields[key] = [value] if value else []
            current = key
        else:
            fields[key] = value
            current = None
    return fields, errors


def check_fields(fields):
    errors = []
    for key in REQUIRED:
        if not fields.get(key):
            errors.append("%s is required" % key)
    for key in fields:
        if key not in REQUIRED + OPTIONAL:
            errors.append("unknown field %s" % key)
    if fields.get("type") and fields["type"] not in TYPES:
        errors.append("type must be one of %s" % ", ".join(TYPES))
    for key in ("created", "verified", "expires"):
        if fields.get(key) and not DATE.match(fields[key]):
            errors.append("%s must be YYYY-MM-DD" % key)
    for s in fields.get("scope") or []:
        if not SCOPE.match(s):
            errors.append("scope %r must be global, host:<device> or cwd:<absolute path>" % s)
    for s in fields.get("sources") or []:
        if not SOURCE_REF.match(s):
            errors.append("source %r must be <source>@<host>:<path>[#<lineno>]" % s)
    if fields.get("superseded_by") and not CARD_ID.match(fields["superseded_by"]):
        errors.append("superseded_by must be a card id")
    return errors


def title_of(body):
    for line in body.split("\n"):
        if line.strip():
            return line[2:].strip() if line.startswith("# ") else None
    return None


def status(fields, today=None):
    """active, expired or superseded, derived from the header at read time."""
    if fields.get("superseded_by"):
        return "superseded"
    today = today or datetime.date.today().isoformat()
    if fields.get("expires") and fields["expires"] < today:
        return "expired"
    return "active"


def load_card(path, today=None):
    """Everything a reader needs about one card file; errors is empty for a valid card."""
    card_id = os.path.basename(path)[:-3]
    with open(path, encoding="utf-8") as f:
        text = f.read()
    header, body = split_card(text)
    fields, errors = parse_header(header) if header else ({}, ["no `---` header"])
    errors += check_fields(fields) if header else []
    if not CARD_ID.match(card_id):
        errors.append("file name must be <card id>.md, lowercase letters, digits and -")
    title = title_of(body)
    if title is None:
        errors.append("body must start with a `# title` line")
    return {
        "id": card_id,
        "path": path,
        "title": title or card_id,
        "type": fields.get("type"),
        "scope": fields.get("scope") or [],
        "sources": fields.get("sources") or [],
        "created": fields.get("created"),
        "verified": fields.get("verified"),
        "expires": fields.get("expires"),
        "superseded_by": fields.get("superseded_by"),
        "status": status(fields, today),
        "body": body.strip(),
        "text": text,
        "errors": errors,
    }


def card_paths(directory):
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return []
    return [os.path.join(directory, n) for n in names if n.endswith(".md") and not n.startswith(".")]


def cards(directory, today=None):
    out = []
    for path in card_paths(directory):
        try:
            out.append(load_card(path, today))
        except (OSError, UnicodeDecodeError) as exc:
            out.append({"id": os.path.basename(path)[:-3], "path": path, "errors": [str(exc)]})
    return out


def card_view(card):
    """The card fields a search hit carries."""
    keys = ("id", "title", "type", "status", "scope", "sources", "verified", "expires", "superseded_by")
    out = {k: card.get(k) for k in keys}
    if card.get("errors"):
        out["errors"] = card["errors"]
    return out


def first_cwd(scope):
    for s in scope:
        if s.startswith("cwd:"):
            return s[4:]
    return ""


def card_ts(fields_date):
    return (fields_date + "T00:00:00Z") if fields_date and DATE.match(fields_date) else ""


# ------------------------------------------------------------------ ledger

def ledger_path(directory):
    return os.path.join(directory, LEDGER)


def ledger(directory):
    """{(host, source, path): furthest entry} from digest.jsonl; bad lines are skipped."""
    out = {}
    try:
        f = open(ledger_path(directory), encoding="utf-8")
    except OSError:
        return out
    with f:
        for line in f:
            try:
                e = json.loads(line)
                key = (e["host"], e["source"], e["path"])
                upto = int(e["upto"])
            except (ValueError, KeyError, TypeError):
                continue
            if key not in out or upto > int(out[key]["upto"]):
                out[key] = e
    return out


def record(directory, host, source, path, upto, cards_):
    """Append one ledger line: path was distilled up to lineno upto, producing cards_."""
    os.makedirs(directory, exist_ok=True)
    entry = {
        "host": host, "source": source, "path": path, "upto": int(upto),
        "ts": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cards": list(cards_),
    }
    with open(ledger_path(directory), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


# ------------------------------------------------------------------ digest

USER_CHARS = 500
CALL_CHARS = 200
RESULT_CHARS = 160
TOOLS_PER_TURN = 4
ANSWER_CHARS = 800
LAST_ANSWER_CHARS = 2000


def _cut(text, n):
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + " …[%d chars]" % len(text)


def _under(path, directory):
    if not path:
        return False
    path, directory = os.path.realpath(path), os.path.realpath(directory)
    return path == directory or path.startswith(directory + os.sep)


def pending(index, directory, host, since=None, min_user=1, exclude_sessions=(), limit=50):
    """Sessions with messages past their ledger entry, newest activity first."""
    done = ledger(directory)
    rows = index.db.execute(
        "SELECT f.path, f.source, f.session_id, f.cwd, f.title, f.started_ts,"
        " (SELECT lineno FROM messages WHERE rowid = fr.hi) AS last_line,"
        " (SELECT ts FROM messages WHERE rowid = fr.hi) AS last_ts,"
        " (SELECT COUNT(*) FROM messages m WHERE m.rowid BETWEEN fr.lo AND fr.hi"
        "   AND m.role = 'user' AND m.kind = 'text' AND m.envelope = 0) AS user_msgs"
        " FROM files f JOIN file_ranges fr ON fr.path = f.path"
        " WHERE f.source <> 'kb'"
    ).fetchall()
    out = []
    for r in rows:
        if r["session_id"] in exclude_sessions or _under(r["cwd"], directory):
            continue
        if since and (r["last_ts"] or "") < since:
            continue
        if (r["user_msgs"] or 0) < min_user or r["last_line"] is None:
            continue
        entry = done.get((host, r["source"], r["path"]))
        upto = int(entry["upto"]) if entry else 0
        if r["last_line"] <= upto:
            continue
        out.append({
            "host": host, "source": r["source"], "path": r["path"], "cwd": r["cwd"],
            "title": r["title"], "started_ts": r["started_ts"], "last_ts": r["last_ts"],
            "from": upto + 1, "to": r["last_line"], "user_msgs": r["user_msgs"],
        })
    out.sort(key=lambda s: s["last_ts"] or "", reverse=True)
    return out[:limit]


def cards_citing(directory, host, source, path):
    prefix = "%s@%s:%s" % (source, host, path)
    return [c["id"] for c in cards(directory)
            if any(s == prefix or s.startswith(prefix + "#") for s in c.get("sources") or [])]


MAX_RELATED = 10
_ASCII_TERM = re.compile(r"[a-z0-9][a-z0-9_.:/-]{2,}")
_STOP = frozenset("""the and for with from that this not use are was you but can all any has have into its out
one two run see via when then than also only more most just like make made does did set get new old now how why
what who true false null none yes off on http https com www json md sh py""".split())


def _terms(text):
    """Distinctive ASCII words of a text: names, hosts, commands, paths, addresses."""
    out = set()
    for t in _ASCII_TERM.findall((text or "").lower()):
        t = t.strip(".:/-_")
        if len(t) >= 3 and t not in _STOP:
            out.add(t)
    return out


def _bigrams(text):
    out = set()
    for m in CJK_RUN.finditer(text or ""):
        run = m.group(0)
        out.update(run[i:i + 2] for i in range(len(run) - 1))
    return out


def _summary(body):
    lines = [l.strip() for l in body.split("\n")[1:] if l.strip() and not l.startswith("```")]
    return _cut(lines[0], 160) if lines else ""


_CODE_SPAN = re.compile(r"`([^`\n]+)`")


def _card_terms(card):
    """A card's distinctive words: from its title and its short `code` spans (one or two
    words: hosts, paths, addresses, option names). Prose and whole command lines are
    full of words every card shares."""
    names = [span for span in _CODE_SPAN.findall(card["body"]) if len(span.split()) <= 2]
    return _terms(card["title"] + "\n" + "\n".join(names))


def related_cards(directory, host, cwd, citing=(), text=""):
    """Live cards this session may confirm or contradict, most relevant first.

    A card is related when it is in scope here (global, this device, or a directory
    containing or inside the session's) and the session shares its distinctive
    words, each weighted by how rare it is among the cards (log N/df): a word in
    every card says nothing. Character pairs of the title add a little for CJK.
    Kept: two shared words at least, scoring three quarters of two words that only
    one card has (1.5 log N). Cards the session
    already cites are in `cards`.
    """
    words, pairs = _terms(text), _bigrams(text)
    live = [c for c in cards(directory) if not c.get("errors") and c["status"] != "superseded"]
    terms = {c["id"]: _card_terms(c) for c in live}
    df = {}
    for ts in terms.values():
        for t in ts:
            df[t] = df.get(t, 0) + 1
    n = float(len(live) + 1)
    floor = 1.5 * math.log(n)  # three quarters of two words found in one card only
    out = []
    for c in live:
        if c["id"] in citing:
            continue
        in_scope = False
        for sc in c["scope"]:
            if sc == "global" or sc == "host:" + host:
                in_scope = True
            elif sc.startswith("cwd:") and cwd and (_under(cwd, sc[4:]) or _under(sc[4:], cwd)):
                in_scope = True
        if not in_scope:
            continue
        shared = terms[c["id"]] & words
        score = sum(math.log(n / df[t]) for t in shared) + 0.2 * len(_bigrams(c["title"]) & pairs)
        if score < floor or len(shared) < 2:
            continue
        out.append({"id": c["id"], "title": c["title"], "type": c["type"], "status": c["status"],
                    "verified": c["verified"], "expires": c["expires"], "summary": _summary(c["body"]),
                    "shared": sorted(shared, key=lambda t: df[t])[:8], "score": round(score, 2)})
    out.sort(key=lambda c: -c["score"])
    return out[:MAX_RELATED]


def _turns_text(turns):
    parts = []
    for t in turns:
        parts.append(t["user"])
        parts.extend((x["call"] or "") + "\n" + (x["result"] or "") for x in t["tools"])
        if t["answer"]:
            parts.append(t["answer"]["text"])
    return "\n".join(parts)


def material(index, directory, host, path, start=None):
    """The dense parts of one session from lineno `start` on (default: past its ledger entry)."""
    meta = index.db.execute(
        "SELECT f.source, f.session_id, f.cwd, f.title, f.started_ts, fr.lo, fr.hi"
        " FROM files f JOIN file_ranges fr ON fr.path = f.path WHERE f.path = ?", (path,)
    ).fetchone()
    if meta is None:
        raise KeyError("not an indexed session: %s" % path)
    if start is None:
        entry = ledger(directory).get((host, meta["source"], path))
        start = int(entry["upto"]) + 1 if entry else 1
    rows = index.db.execute(
        "SELECT lineno, ts, role, kind, text FROM messages"
        " WHERE rowid BETWEEN ? AND ? AND lineno >= ? ORDER BY rowid",
        (meta["lo"], meta["hi"], start),
    ).fetchall()
    # One turn per user message: what was asked, the last tool calls made for it
    # (the ones that usually worked), and the answer that closed it. A session that
    # moves through several topics keeps the conclusion of each.
    turns, turn, call = [], None, None
    for r in rows:
        text = r["text"] or ""
        if r["role"] == "user" and r["kind"] == "text":
            if not text.strip():
                continue
            turn = {"lineno": r["lineno"], "ts": r["ts"], "user": _cut(text, USER_CHARS), "tools": [],
                    "tools_dropped": 0, "answer": None}
            turns.append(turn)
            call = None
            continue
        if turn is None:
            continue
        if r["kind"] == "tool_call":
            call = {"lineno": r["lineno"], "call": _cut(text, CALL_CHARS), "result": None}
            turn["tools"].append(call)
        elif r["kind"] == "tool_result":
            if call is not None and call["result"] is None:
                call["result"] = _cut(text, RESULT_CHARS)
            call = None
        elif r["role"] == "assistant" and r["kind"] == "text" and text.strip():
            turn["answer"] = {"lineno": r["lineno"], "text": text}
    dropped = 0
    for i, t in enumerate(turns):
        extra = max(0, len(t["tools"]) - TOOLS_PER_TURN)
        t["tools"], t["tools_dropped"] = t["tools"][extra:], extra
        dropped += extra
        if t["answer"]:
            limit = LAST_ANSWER_CHARS if i == len(turns) - 1 else ANSWER_CHARS
            t["answer"]["text"] = _cut(t["answer"]["text"], limit)
    citing = cards_citing(directory, host, meta["source"], path)
    return {
        "session": {
            "host": host, "source": meta["source"], "path": path, "cwd": meta["cwd"],
            "title": meta["title"], "started_ts": meta["started_ts"],
            "from": start, "to": rows[-1]["lineno"] if rows else start - 1,
            "last_ts": rows[-1]["ts"] if rows else None,
        },
        "turns": turns,
        "cards": citing,
        "related": related_cards(directory, host, meta["cwd"], citing, _turns_text(turns)),
        "clipped": {"tools": dropped},
    }
