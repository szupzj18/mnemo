import base64
import json

from .model import CJK_RUN, has_cjk
from .sources import SOURCES

DEFAULT_KINDS = ("text", "summary", "tool_call", "tool_result")


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
    params.append(limit)

    rows = [dict(r) for r in index.db.execute(search_sql(where), params).fetchall()]
    for r in rows:  # keep the documented JSON field name
        r["rank"] = r.pop("score")
    return rows


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
    return [dict(r) for r in index.db.execute(sql, params).fetchall()]


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
    sid, cwd, parsed = source.parse(path, clip_text=False)
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
