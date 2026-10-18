import base64
import json
import os

from .model import CJK_RUN, has_cjk
from .sources import SOURCES

DEFAULT_KINDS = ("text", "summary", "tool_call", "tool_result")

# Session ids the calling agent exports to the commands it runs. Claude Code sets
# CLAUDE_CODE_SESSION_ID for shell commands and MCP servers; Codex sets
# CODEX_THREAD_ID (this thread) and CODEX_SESSION_ID (its root session) for shell
# commands, and sends the same pair as threadId/sessionId in an MCP call's _meta.
CALLER_SESSION_ENV = ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID", "CODEX_SESSION_ID")
CALLER_SESSION_META = ("threadId", "sessionId")


def caller_session_ids(env=None, meta=None):
    """Ids of the session running this search, so its own messages can be left out."""
    env = os.environ if env is None else env
    found = [env.get(k) for k in CALLER_SESSION_ENV]
    if isinstance(meta, dict):
        found += [meta.get(k) for k in CALLER_SESSION_META]
    return split_ids(v for v in found if isinstance(v, str))


def split_ids(values):
    """Flatten comma-separated id lists, dropping blanks and duplicates, keeping order."""
    out = []
    for value in values or ():
        for sid in str(value).split(","):
            sid = sid.strip()
            if sid and sid not in out:
                out.append(sid)
    return out


def _phrase(token):
    return '"' + token.replace('"', '""') + '"'


def _cjk_match(token):
    parts = []
    pos = 0
    for m in CJK_RUN.finditer(token):
        if m.start() > pos:
            seg = token[pos : m.start()].strip().lower()
            if seg:
                parts.append(_phrase(seg))
        run = m.group(0)
        if len(run) == 1:
            parts.append(_phrase(run))
        else:
            bigrams = " AND ".join(_phrase(run[i : i + 2]) for i in range(len(run) - 1))
            parts.append("(" + bigrams + ")")
        pos = m.end()
    if pos < len(token):
        seg = token[pos:].strip().lower()
        if seg:
            parts.append(_phrase(seg))
    return " AND ".join(parts)


def build_match(query, include_injected=False):
    groups = []
    for tok in query.split():
        alts = ["text : %s*" % _phrase(tok)]
        if has_cjk(tok):
            grams_q = _cjk_match(tok)
            if grams_q:
                alts.append("grams : (%s)" % grams_q)
        if include_injected:
            # Also match the verbatim bodies, including stripped boilerplate.
            alts.append("body : %s*" % _phrase(tok))
        groups.append("(" + " OR ".join(alts) + ")")
    return " AND ".join(groups)


def search_sql(where):
    """The SQL behind search(); separate so tests can check its query plan."""
    return (
        "SELECT path, lineno, source, session_id, cwd, ts, role, kind, "
        "CAST(envelope AS INTEGER) AS envelope, "
        "snippet(messages, 1, '[[', ']]', ' … ', 18) AS snippet, "
        # Not "AS rank": an alias named rank shadows FTS5's hidden rank column,
        # so ORDER BY rank would sort the expression through a temp B-tree
        # instead of letting FTS5 stream rows in rank order. Measured on a
        # 151k-message index, a 66k-match query: 85 ms -> 38 ms.
        "bm25(messages) AS score "
        "FROM messages WHERE " + " AND ".join(where) + " ORDER BY rank LIMIT ?"
    )


def search(
    index,
    query,
    sources=None,
    kinds=None,
    cwd=None,
    since=None,
    limit=20,
    include_injected=False,
    exclude_sessions=None,
):
    where = ["messages MATCH ?"]
    params = [build_match(query, include_injected=include_injected)]
    if sources:
        where.append("source IN (%s)" % ",".join("?" * len(sources)))
        params.extend(sources)
    if kinds is None:
        kinds = DEFAULT_KINDS
    if kinds:
        where.append("kind IN (%s)" % ",".join("?" * len(kinds)))
        params.extend(kinds)
    if cwd:
        where.append("cwd LIKE ?")
        params.append("%" + cwd.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    if since:
        where.append("ts >= ?")
        params.append(since)
    if exclude_sessions:
        where.append("session_id NOT IN (%s)" % ",".join("?" * len(exclude_sessions)))
        params.extend(exclude_sessions)
    params.append(limit)

    rows = [dict(r) for r in index.db.execute(search_sql(where), params).fetchall()]
    for r in rows:  # keep the documented JSON field name
        r["rank"] = r.pop("score")
    return annotate(index, rows)


def _source(name, cache):
    if name not in cache:
        cls = SOURCES.get(name)
        cache[name] = cls() if cls else None
    return cache[name]


def origin(index, source_name, path, cache=None):
    """Which account a session file came from, and whether only its archived copy is left.

    {"account": ".claude-work" or None, "archived": bool, "archive_path": copy (archived only)}
    """
    source = _source(source_name, {} if cache is None else cache)
    out = {"account": None, "archived": False}
    if source is None or not source.ARCHIVABLE:
        return out
    out["account"] = source.account(path)
    if not source.exists(path):
        copy = index.archive.locate(source, path)
        if copy:
            out["archived"] = True
            out["archive_path"] = source.actual(copy)
    return out


def annotate(index, rows):
    """Add account and archived to rows that carry source and path (hits, recent sessions)."""
    cache, seen = {}, {}
    for r in rows:
        key = (r["source"], r["path"])
        if key not in seen:
            seen[key] = origin(index, r["source"], r["path"], cache)
        r["account"] = seen[key]["account"]
        r["archived"] = seen[key]["archived"]
    return rows


def archived_count(index):
    """Indexed sessions whose original log is gone, read from the archive instead."""
    cache = {}
    return sum(1 for r in index.db.execute("SELECT source, path FROM files")
               if origin(index, r["source"], r["path"], cache)["archived"])


def read_path(index, source, path):
    """The file to read a session from: the original, or its archived copy once it is gone."""
    if source.ARCHIVABLE and not source.exists(path):
        copy = index.archive.locate(source, path)
        if copy:
            return copy
    return path


def _view_row(r, hit=None):
    """Normalized message: cleaned text by default, verbatim body kept aside."""
    clean = r["text"] or ""
    body = r["body"] or ""
    out = {
        "lineno": r["lineno"],
        "ts": r["ts"],
        "role": r["role"],
        "kind": r["kind"],
        "text": clean or body,  # pure-envelope rows still render their body
        "envelope": int(r["envelope"] or 0),
    }
    if out["envelope"] and body and clean:
        out["body"] = body  # mixed message: original available on request
    if hit is not None:
        out["hit"] = hit
    return out


# Default per-message cap for context windows. Claude Code cuts Read lines at the
# same length; on a real index it clips ~1% of text messages and ~44% of tool results.
CONTEXT_MAX_CHARS = 2000


def context_max_chars(max_chars=None, full=False, raw=False):
    """Clip length for a context read: explicit max_chars wins, full/raw mean whole bodies."""
    if max_chars is not None:
        return int(max_chars) or None  # 0 means whole bodies
    if full or raw:
        return None
    return CONTEXT_MAX_CHARS


def clip_bodies(rows, max_chars, hint="full=true"):
    """Shorten each message's text to max_chars, saying how long it was and how to get it all.

    Clipped rows get clipped: true plus chars (and body_chars) with the original length.
    The hit itself is clipped too: its full body stays one re-read away.
    """
    if not max_chars or rows is None:
        return rows
    for r in rows:
        for key in ("text", "body"):
            value = r.get(key)
            if isinstance(value, str) and len(value) > max_chars:
                r[key] = value[:max_chars] + " … [clipped: %d of %d chars; %s for the whole message]" % (
                    max_chars, len(value), hint)
                r["clipped"] = True
                r["chars" if key == "text" else "body_chars"] = len(value)
    return rows


def recent(index, sources=None, cwd=None, since=None, limit=25):
    """Most recently started sessions with their first human task as title."""
    where = ["f.title IS NOT NULL AND f.title <> ''"]
    params = []
    if sources:
        where.append("f.source IN (%s)" % ",".join("?" * len(sources)))
        params.extend(sources)
    if cwd:
        where.append("f.cwd LIKE ?")
        params.append("%" + cwd.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    if since:
        where.append("f.started_ts >= ?")
        params.append(since)
    params.append(limit)
    sql = (
        "SELECT f.source, f.cwd, f.started_ts, f.title, f.session_id, f.path, "
        "(fr.hi - fr.lo + 1) AS messages "
        "FROM files f LEFT JOIN file_ranges fr ON fr.path = f.path "
        "WHERE " + " AND ".join(where) +
        " ORDER BY f.started_ts DESC LIMIT ?"
    )
    return annotate(index, [dict(r) for r in index.db.execute(sql, params).fetchall()])


def session_selection(count, signature, head=None, tail=None, limit=None, cursor=None, anchor=None):
    """Validate a read and resolve its message offset; cursors reject reindexed files."""
    if head and tail:
        raise ValueError("head and tail are mutually exclusive")
    if any(v is not None and int(v) < 0 for v in (head, tail)):
        raise ValueError("head/tail must be nonnegative")
    if (head or tail) and (limit is not None or cursor or anchor is not None):
        raise ValueError("head/tail cannot be combined with pagination")
    if (cursor or anchor is not None) and limit is None:
        raise ValueError("limit is required with cursor/anchor_line")
    if cursor and anchor is not None:
        raise ValueError("cursor and anchor_line are mutually exclusive")
    if limit is not None and not 1 <= int(limit) <= 500:
        raise ValueError("limit must be between 1 and 500")
    size = int(limit) if limit is not None else int(head or tail or count)
    offset = max(0, count - size) if tail else 0
    if anchor is not None:
        offset = max(0, int(anchor) - size // 2)
    if cursor:
        try:
            decoded = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
            if decoded["signature"] != signature:
                raise ValueError("session changed; restart pagination")
            offset = decoded["offset"]
            if type(offset) is not int or not 0 <= offset < count:
                raise ValueError("invalid session cursor offset")
        except (KeyError, TypeError, UnicodeError, json.JSONDecodeError, base64.binascii.Error):
            raise ValueError("invalid session cursor")
    return min(offset, count), size


def session_page(offset, size, count, signature):
    def encode(position):
        payload = json.dumps({"signature": signature, "offset": position}, separators=(",", ":"))
        return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")
    return {
        "offset": offset, "limit": size,
        "next_cursor": encode(offset + size) if offset + size < count else None,
        "previous_cursor": encode(max(0, offset - size)) if offset > 0 else None,
    }


def get_session(index, path, head=None, tail=None, limit=None, cursor=None, anchor_line=None):
    # Sync can replace the file's rowid range between SELECTs. Metadata, cursor
    # validation and bodies must refer to the same SQLite snapshot.
    owns_transaction = not index.db.in_transaction
    if owns_transaction:
        index.db.execute("BEGIN")
    try:
        return _get_session(index, path, head, tail, limit, cursor, anchor_line)
    finally:
        if owns_transaction:
            index.db.execute("ROLLBACK")


def _get_session(index, path, head, tail, limit, cursor, anchor_line):
    rng = index.db.execute("SELECT lo, hi FROM file_ranges WHERE path = ?", (path,)).fetchone()
    if not rng:
        return None
    lo, hi = rng["lo"], rng["hi"]
    meta = index.db.execute(
        "SELECT source, session_id, cwd, mtime, size FROM files WHERE path = ?", (path,)
    ).fetchone()
    count = hi - lo + 1
    signature = [path, lo, hi, meta["mtime"], meta["size"]]
    anchor = None
    if anchor_line is not None:
        row = index.db.execute(
            "SELECT rowid FROM messages WHERE rowid BETWEEN ? AND ? AND lineno >= ? ORDER BY rowid LIMIT 1",
            (lo, hi, int(anchor_line)),
        ).fetchone()
        anchor = row[0] - lo if row else count - 1
    offset, size = session_selection(count, signature, head, tail, limit, cursor, anchor)
    span = index.db.execute(
        "SELECT MIN(ts) AS started_at, MAX(ts) AS ended_at FROM messages WHERE rowid BETWEEN ? AND ?",
        (lo, hi),
    ).fetchone()
    messages = [_view_row(r) for r in index.db.execute(
        "SELECT lineno, ts, role, kind, body, text, envelope FROM messages"
        " WHERE rowid BETWEEN ? AND ? ORDER BY rowid",
        (lo + offset, min(hi, lo + offset + size - 1)),
    )]
    result = {
        "path": path, "source": meta["source"], "session_id": meta["session_id"], "cwd": meta["cwd"],
        "started_at": span["started_at"], "ended_at": span["ended_at"], "count": count, "messages": messages,
    }
    result.update(origin(index, meta["source"], path))
    if limit is not None:
        result["page"] = session_page(offset, size, count, signature)
    return result


def _source_for(index, path):
    row = index.db.execute(
        "SELECT source FROM files WHERE path = ?", (path,)
    ).fetchone()
    if not row:
        return None
    cls = SOURCES.get(row["source"])
    return cls() if cls else None


def raw_session(index, path, head=None, tail=None, limit=None, cursor=None, anchor_line=None):
    """Read the full session straight from its JSONL file, no 20k cap."""
    source = _source_for(index, path)
    if source is None:
        return None
    sid, cwd, parsed = source.parse(read_path(index, source, path), clip_text=False)
    messages = [
        {
            "lineno": lineno,
            "ts": m.ts,
            "role": m.role,
            "kind": m.kind,
            "text": m.stored,
            "envelope": int(m.envelope),
        }
        for lineno, m in parsed
    ]
    result = {
        "path": path,
        "source": source.name,
        "session_id": sid,
        "cwd": cwd,
        "started_at": messages[0]["ts"] if messages else "",
        "ended_at": messages[-1]["ts"] if messages else "",
        "count": len(messages),
        "messages": messages,
        "raw": True,
    }
    result.update(origin(index, source.name, path))
    meta = index.db.execute("SELECT mtime, size FROM files WHERE path=?", (path,)).fetchone()
    signature = [path, "raw", meta["mtime"], meta["size"]]
    anchor = None
    if anchor_line is not None:
        anchor = next((i for i, m in enumerate(messages) if m["lineno"] >= int(anchor_line)), len(messages) - 1)
    offset, size = session_selection(len(messages), signature, head, tail, limit, cursor, anchor)
    result["messages"] = messages[offset:offset + size]
    if limit is not None:
        result["page"] = session_page(offset, size, len(messages), signature)
    return result


def raw_context(index, path, line, before=4, after=8):
    sess = raw_session(index, path)
    if sess is None:
        return None
    messages = sess["messages"]
    hits = [i for i, m in enumerate(messages) if m["lineno"] == line]
    if not hits:
        return []
    lo = max(0, hits[0] - before)
    hi = min(len(messages), hits[-1] + 1 + after)
    hit_set = set(hits)
    window = messages[lo:hi]
    for offset, m in enumerate(window):
        m["hit"] = (lo + offset) in hit_set
    return window


def get_context(index, path, line, before=4, after=8, home=None):
    rng = index.db.execute(
        "SELECT lo, hi FROM file_ranges WHERE path = ?", (path,)
    ).fetchone()
    if not rng:
        return None
    lo, hi = rng["lo"], rng["hi"]

    hit_rows = [
        r[0]
        for r in index.db.execute(
            "SELECT rowid FROM messages WHERE rowid BETWEEN ? AND ? AND lineno = ?"
            " ORDER BY rowid",
            (lo, hi, line),
        ).fetchall()
    ]
    if not hit_rows:
        return []
    first_hit, last_hit = hit_rows[0], hit_rows[-1]

    win = index.db.execute(
        "SELECT"
        "  (SELECT MIN(rowid) FROM (SELECT rowid FROM messages"
        "    WHERE rowid BETWEEN ? AND ? ORDER BY rowid DESC LIMIT ?)) AS win_lo,"
        " (SELECT MAX(rowid) FROM (SELECT rowid FROM messages"
        "    WHERE rowid BETWEEN ? AND ? ORDER BY rowid ASC LIMIT ?)) AS win_hi",
        (lo, first_hit - 1, before, last_hit + 1, hi, after),
    ).fetchone()
    win_lo = win["win_lo"] if win["win_lo"] is not None else first_hit
    win_hi = win["win_hi"] if win["win_hi"] is not None else last_hit

    out = []
    for r in index.db.execute(
        "SELECT rowid AS rid, lineno, ts, role, kind, body, text, envelope"
        " FROM messages"
        " WHERE rowid BETWEEN ? AND ? ORDER BY rowid",
        (win_lo, win_hi),
    ).fetchall():
        out.append(_view_row(r, hit=first_hit <= r["rid"] <= last_hit))
    return out
