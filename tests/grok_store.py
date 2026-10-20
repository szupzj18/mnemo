"""Writes tests/fixtures/grok-sessions.json as Grok CLI session logs (grok 1.0.62's format).

Each session becomes ~/.grok/sessions/<URL-encoded cwd>/<id>/updates.jsonl, one
session/update notification per line, with the files grok keeps beside it.
"""
import json
import os
from urllib.parse import quote

from helpers import REPO

FIXTURE = os.path.join(REPO, "tests", "fixtures", "grok-sessions.json")


def sessions():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)["sessions"]


def _bytes(value):
    # grok logs some outputs (GrepSearch's stdout) as a list of byte values.
    if isinstance(value, str) and value.startswith("bytes:"):
        return list(value[len("bytes:"):].encode("utf-8"))
    if isinstance(value, dict):
        return {k: _bytes(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_bytes(v) for v in value]
    return value


def line(session, entry, n=0):
    """One updates.jsonl line for a fixture entry ({at, update, prompt?, method?})."""
    when = session["start"] + entry.get("at", 0)
    meta = {"eventId": "%s-%d" % (session["id"][:8], n)}
    # Older grok lines carry only the line's own timestamp, in seconds.
    if not session.get("seconds_only"):
        meta["agentTimestampMs"] = when * 1000 + 250
    if entry.get("prompt"):
        meta.update(promptId=entry["prompt"], turnStartMs=when * 1000, updateType="chunk")
    return json.dumps({"timestamp": when, "method": entry.get("method", "session/update"),
                       "params": {"sessionId": session["id"], "update": _bytes(entry["update"]), "_meta": meta}},
                      ensure_ascii=False) + "\n"


def write(home, session, grok_dir=".grok"):
    """grok_dir is the grok home: relative to home, or absolute (a $GROK_HOME elsewhere)."""
    path = os.path.join(home, grok_dir, "sessions", quote(session["cwd"], safe=""), session["id"], "updates.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(line(session, e, n) for n, e in enumerate(session["updates"]))
    # Files grok keeps beside the log; none of them is a session of its own.
    with open(os.path.join(os.path.dirname(path), "summary.json"), "w") as f:
        json.dump({"info": {"id": session["id"], "cwd": session["cwd"]}, "generated_title": "not read"}, f)
    with open(os.path.join(os.path.dirname(path), "chat_history.jsonl"), "w") as f:
        f.write(json.dumps({"type": "system", "content": "not read"}) + "\n")
    with open(os.path.join(os.path.dirname(os.path.dirname(path)), "prompt_history.jsonl"), "w") as f:
        f.write(json.dumps({"prompt": "not read"}) + "\n")
    return path


def write_all(home):
    """{session id: updates.jsonl path} for every fixture session, plus grok's search index."""
    paths = {s["id"]: write(home, s) for s in sessions()}
    open(os.path.join(home, ".grok", "sessions", "session_search.sqlite"), "wb").close()
    return paths


def append(path, session, update, at=600, prompt=None):
    with open(path, "a", encoding="utf-8") as f:
        f.write(line(session, {"at": at, "update": update, "prompt": prompt}, 999))
