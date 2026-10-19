"""Antigravity CLI conversation databases for tests, from tests/fixtures/agy-conversations.json.

The tables are the ones agy 1.3.6 creates. Each step_payload is protobuf with
the field numbers mnemo/sources/agy.py reads, plus a few it must skip (ids,
status, the agent config agy stores with every prompt).
"""
import json
import os
import sqlite3

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "agy-conversations.json")
CONVERSATIONS = os.path.join(".gemini", "antigravity-cli", "conversations")

SCHEMA = """
CREATE TABLE `trajectory_meta` (`trajectory_id` text,`cascade_id` text,`trajectory_type` integer,`source` integer,PRIMARY KEY (`trajectory_id`));
CREATE TABLE `steps` (`idx` integer,`step_type` integer NOT NULL DEFAULT 0,`status` integer NOT NULL DEFAULT 0,`has_subtrajectory` numeric NOT NULL DEFAULT false,`metadata` blob,`error_details` blob,`permissions` blob,`task_details` blob,`render_info` blob,`step_payload` blob,`step_format` integer NOT NULL DEFAULT 0,PRIMARY KEY (`idx`));
CREATE INDEX `idx_steps_status` ON `steps`(`status`);
CREATE INDEX `idx_steps_step_type` ON `steps`(`step_type`);
CREATE TABLE `gen_metadata` (`idx` integer,`data` blob,`size` integer NOT NULL DEFAULT 0,PRIMARY KEY (`idx`));
CREATE TABLE `executor_metadata` (`idx` integer,`data` blob,PRIMARY KEY (`idx`));
CREATE TABLE `parent_references` (`idx` integer,`data` blob,PRIMARY KEY (`idx`));
CREATE TABLE `trajectory_metadata_blob` (`id` text DEFAULT "main",`data` blob,PRIMARY KEY (`id`));
CREATE TABLE `battle_mode_infos` (`idx` integer,`data` blob,PRIMARY KEY (`idx`));
"""

TYPES = {"user": 14, "planner": 15, "error": 17, "checkpoint": 23, "notification": 101, "tool": 132}


def conversations():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)["conversations"]


def varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def field(number, value):
    if isinstance(value, int):
        return varint(number << 3) + varint(value)
    if isinstance(value, str):
        value = value.encode("utf-8")
    return varint(number << 3 | 2) + varint(len(value)) + value


def message(*fields):
    return b"".join(fields)


def timestamp(seconds):
    return message(field(1, seconds), field(2, 250000000))


def tool_call(idx, call):
    return message(field(1, "call_%d" % idx), field(2, call["name"]),
                   field(3, json.dumps(call["args"], sort_keys=True)))


def payload(idx, step, seconds):
    """(step_type, step_payload) for one fixture step."""
    kind = next(k for k in TYPES if k in step)
    value = step[kind]
    meta = [field(1, timestamp(seconds)), field(3, 4), field(12, "exec-%04d" % idx),
            field(20, message(field(1, "trajectory"), field(2, idx)))]
    body = b""
    if kind == "user":
        body = field(19, message(field(2, value), field(3, message(field(1, value))),
                                 field(12, message(field(12, "file:///home/alex")))))
    elif kind == "planner":
        reply = [field(6, "message-%04d" % idx), field(12, 2)]
        if value.get("thinking"):
            reply.append(field(3, value["thinking"]))
        if value.get("text"):
            reply += [field(1, value["text"]), field(8, value["text"])]
        reply += [field(7, tool_call(idx, c)) for c in value.get("tool_calls", [])]
        body = field(20, message(*reply))
    elif kind == "tool":
        meta.append(field(4, message(field(1, "call_%d" % (idx - 1)), field(2, "tool"))))
        tool = [field(1, message(field(1, "toolSummary"), field(2, "synthetic")))]
        if "output" in value:
            tool.append(field(2, message(field(1, value["output"]), field(7, "file:///tmp/output.log"))))
        body = field(140, message(*tool))
        if "error" in value:
            body += field(31, message(field(2, value["error"]), field(3, value["error"])))
    elif kind == "notification":
        body = field(114, message(field(1, value), field(2, message(field(1, "Background task"))),
                                  field(3, "task_notification")))
    elif kind == "error":
        body = field(24, message(field(3, message(field(2, value)))))
    elif kind == "checkpoint":
        body = field(30, message(field(5, value), field(12, idx), field(19, "earlier request")))
    return TYPES[kind], message(field(1, TYPES[kind]), field(4, 3), field(5, message(*meta))) + body


def add_step(con, idx, step, seconds):
    step_type, blob = payload(idx, step, seconds)
    con.execute("INSERT INTO steps(idx, step_type, status, step_payload) VALUES (?,?,3,?)",
                (idx, step_type, blob))


def write(home, conv):
    """Write one conversation the way agy does (WAL journal); return its path."""
    folder = os.path.join(home, CONVERSATIONS)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, conv["id"] + ".db")
    con = sqlite3.connect(path)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
        con.execute("INSERT INTO trajectory_meta VALUES (?,?,4,17)", ("trajectory-" + conv["id"][:8], conv["id"]))
        workspace = conv["workspace"]
        con.execute("INSERT INTO trajectory_metadata_blob VALUES ('main', ?)", (message(
            field(1, message(field(1, workspace))), field(2, timestamp(conv["start"])),
            field(7, workspace), field(18, "default-cli-project")),))
        for idx, step in enumerate(conv["steps"]):
            add_step(con, idx, step, conv["start"] + step["at"])
        con.commit()
    finally:
        con.close()
    return path


def write_all(home):
    """Every fixture conversation under home; returns {conversation id: path}."""
    return {conv["id"]: write(home, conv) for conv in conversations()}


def live(path):
    """A writer that keeps new steps in the WAL, as a running agy does between checkpoints."""
    con = sqlite3.connect(path)
    con.execute("PRAGMA wal_autocheckpoint=0")
    return con
