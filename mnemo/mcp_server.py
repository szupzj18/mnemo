import json
import subprocess
import sys

from . import __version__
from .index import Index, IndexTooNew
from . import remote as remote_mod
from .remote import LOCAL, fan_out_search, remote_context, remote_session
from .search import DEFAULT_KINDS, get_context, get_session, raw_context, raw_session, recent


# Tool annotations tell an MCP client whether a call needs the user's approval. Codex skips its
# approval prompt for readOnlyHint tools, which is what lets an unattended run (`codex exec`,
# where approvals are off and a prompt would cancel the call) search past sessions.
READ_ONLY = {"readOnlyHint": True, "idempotentHint": True}
WRITES_LOCAL_INDEX = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True}


def build_tools():
    names = [LOCAL] + [r["name"] for r in remote_mod.load_remotes()]
    host_desc = ("comma-separated devices to search; available: %s, or a route such as "
                 "neighbor/device from earlier results (default: everything reachable)" % ",".join(names))
    return [
        {
            "name": "search_sessions",
            "annotations": READ_ONLY,
            "description": (
                "Full-text search across coding-agent sessions (claude, codex, opencode, pi) on this machine"
                " and registered remote devices. Matches user prompts, assistant replies, summaries,"
                " tool calls and tool results. Each hit gives host, source, cwd, timestamp, a snippet,"
                " and file:line for get_context (pass the hit's host to get_context); pass the hit's path alone to get_session for the whole session file."
                " Supports English (prefix) and Chinese (substring via bigrams)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "keywords, whitespace-separated (all must match)"},
                    "source": {"type": "string", "description": "comma-separated subset of: claude,codex,opencode,pi"},
                    "kinds": {
                        "type": "string",
                        "description": "comma-separated subset of: text,summary,tool_call,tool_result,reasoning (default: text,summary,tool_call,tool_result)",
                    },
                    "cwd": {"type": "string", "description": "only sessions whose working directory contains this substring"},
                    "since": {"type": "string", "description": "YYYY-MM-DD"},
                    "host": {"type": "string", "description": host_desc},
                    "limit": {"type": "integer", "description": "max hits (default 20)"},
                    "include_injected": {
                        "type": "boolean",
                        "description": "also match injected boilerplate/envelope bodies (AGENTS.md, plugin suggestions, approval-review wraps) hidden from search by default",
                    },
                },
                "required": ["query"],
            },
        },
        {
            "name": "get_context",
            "annotations": READ_ONLY,
            "description": (
                "Fetch surrounding messages of a search hit (same normalized format) so a hit can be read in context."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "line": {"type": "integer"},
                    "host": {"type": "string", "description": "device or route holding the hit, copied from the search result (default: local)"},
                    "before": {"type": "integer", "description": "messages before the hit (default 4)"},
                    "after": {"type": "integer", "description": "messages after the hit (default 8)"},
                    "raw": {"type": "boolean", "description": "read full untruncated bodies from the original session file"},
                },
                "required": ["path", "line"],
            },
        },
        {
            "name": "get_session",
            "annotations": READ_ONLY,
            "description": (
                "Fetch every indexed message of the whole session file that a search hit belongs to"
                " (same normalized format), ordered by time. Use after get_context when you need the"
                " full conversation rather than a window around one line. Message bodies are capped"
                " at 20k characters in the index; pass raw=true to read the original session file with"
                " full bodies. head/tail select only the first/last N messages."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "host": {"type": "string", "description": "device or route holding the session, copied from the search result (default: local)"},
                    "head": {"type": "integer", "description": "only return the first N messages"},
                    "tail": {"type": "integer", "description": "only return the last N messages"},
                    "raw": {"type": "boolean", "description": "read full untruncated bodies straight from the original session JSONL"},
                },
                "required": ["path"],
            },
        },
        {
            "name": "list_recent_sessions",
            "annotations": READ_ONLY,
            "description": (
                "List coding-agent sessions most recently started, newest first, each with its first"
                " human task as the title, source, cwd, start time and message count. Use this to answer"
                " 'what have I been working on lately' or to find a session without knowing a keyword."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "comma-separated subset of: claude,codex,opencode,pi"},
                    "cwd": {"type": "string", "description": "only sessions whose working directory contains this substring"},
                    "since": {"type": "string", "description": "YYYY-MM-DD"},
                    "limit": {"type": "integer", "description": "max sessions (default 25)"},
                },
            },
        },
        {
            "name": "reindex",
            "annotations": WRITES_LOCAL_INDEX,
            "description": "Incrementally scan local session files for new/changed sessions. Cheap when nothing changed.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "comma-separated subset of: claude,codex,opencode,pi"},
                },
            },
        },
    ]


def _text_result(obj):
    return {"content": [{"type": "text", "text": json.dumps(obj, ensure_ascii=False, indent=2)}]}


def _since(value):
    if not value:
        return None
    if len(value) == 10:
        return value + "T00:00:00Z"
    return value


def handle_call(name, args, index):
    args = args or {}
    if name == "search_sessions":
        query = args.get("query", "").strip()
        if not query:
            raise ValueError("query is required")
        sources = args.get("source")
        kinds = args.get("kinds")
        host = args.get("host")
        hosts = [h.strip() for h in host.split(",")] if host else None
        hits, warnings = fan_out_search(
            index,
            query,
            sources=sources.split(",") if sources else None,
            kinds=kinds.split(",") if kinds else list(DEFAULT_KINDS),
            cwd=args.get("cwd"),
            since=_since(args.get("since")),
            limit=min(int(args.get("limit", 20)), 100),
            hosts=hosts,
            include_injected=bool(args.get("include_injected")),
        )
        text = json.dumps(hits, ensure_ascii=False, indent=2)
        if warnings:
            text += "\n\nunreachable devices (excluded from results):\n" + "\n".join(
                "- " + w for w in warnings
            )
        return {"content": [{"type": "text", "text": text}]}
    if name == "get_context":
        host = args.get("host") or LOCAL
        raw = bool(args.get("raw"))
        before, after = int(args.get("before", 4)), int(args.get("after", 8))
        if host == LOCAL:
            rows = raw_context(index, args["path"], int(args["line"]), before, after) if raw \
                else get_context(index, args["path"], int(args["line"]), before, after)
        else:
            rows = remote_context(
                host,
                args["path"],
                int(args["line"]),
                before,
                after,
                raw=raw,
            )
        if rows is None:
            raise ValueError("path not in index; run reindex")
        return _text_result(rows)
    if name == "get_session":
        host = args.get("host") or LOCAL
        raw = bool(args.get("raw"))
        head, tail = args.get("head"), args.get("tail")
        if host == LOCAL:
            sess = raw_session(index, args["path"]) if raw else get_session(index, args["path"])
        else:
            sess = remote_session(
                host, args["path"],
                head=head, tail=tail, raw=raw,
            )
        if sess is None:
            raise ValueError("path not in index; run reindex")
        if host == LOCAL and (head or tail):
            sess["messages"] = sess["messages"][:head] if head else sess["messages"][-tail:]
        return _text_result(sess)
    if name == "list_recent_sessions":
        sources = args.get("source")
        rows = recent(
            index,
            sources=sources.split(",") if sources else None,
            cwd=args.get("cwd"),
            since=_since(args.get("since")),
            limit=min(int(args.get("limit", 25)), 100),
        )
        return _text_result(rows)
    if name == "reindex":
        sources = args.get("source")
        try:
            stats = index.sync(sources.split(",") if sources else None)
        except IndexTooNew as exc:
            raise ValueError(str(exc))
        return _text_result(stats)
    raise ValueError("unknown tool: %s" % name)


def call_fresh(name, args, timeout=900):
    """Run one tool call in a new process, so it always uses the code on disk now.

    This server lives as long as the agent session that started it; answering in
    process would keep serving the code it started with through every upgrade.
    """
    from .link import self_argv

    try:
        p = subprocess.run(self_argv() + ["mcp", "--call", name or ""], input=json.dumps(args or {}),
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return _error("%s timed out after %ds" % (name, timeout))
    try:
        return json.loads(p.stdout)
    except ValueError:
        err = (p.stderr or "").strip().splitlines()
        return _error(err[-1] if err else "tool process exited with %d" % p.returncode)


def call_main(name):
    """`mnemo mcp --call NAME`: one tool call, arguments as JSON on stdin, result on stdout."""
    try:
        args = json.loads(sys.stdin.read() or "{}")
    except ValueError as exc:
        args, result = None, _error("bad arguments: %s" % exc)
    if args is not None:
        index = Index()
        try:
            result = handle_call(name, args, index)
        except Exception as exc:
            result = _error(str(exc))
        finally:
            index.close()
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    return 0


def _error(text):
    return {"content": [{"type": "text", "text": "error: %s" % text}], "isError": True}


def run():
    from . import live

    live.mark("mcp")
    sys.stderr.write("mnemo mcp: indexing sessions...\n")
    index = Index()
    try:
        stats = index.sync(logger=lambda m: sys.stderr.write(m + "\n"))
    except IndexTooNew as exc:
        # Keep serving reads; searches report the same warning per call.
        stats = {"error": str(exc)}
    index.close()  # tool calls open their own, in their own process
    sys.stderr.write("mnemo mcp: ready (%s)\n" % json.dumps(stats))
    tools = build_tools()

    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except ValueError:
            continue
        method = req.get("method")
        rid = req.get("id")

        if method == "initialize":
            resp = {
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "protocolVersion": (req.get("params") or {}).get("protocolVersion", "2024-11-05"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "mnemo", "version": __version__},
                },
            }
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            resp = {"jsonrpc": "2.0", "id": rid, "result": {"tools": tools}}
        elif method == "tools/call":
            params = req.get("params") or {}
            result = call_fresh(params.get("name"), params.get("arguments"))
            resp = {"jsonrpc": "2.0", "id": rid, "result": result}
        elif method == "ping":
            resp = {"jsonrpc": "2.0", "id": rid, "result": {}}
        else:
            if rid is None:
                continue
            resp = {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": -32601, "message": "method not found: %s" % method},
            }
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    run()
