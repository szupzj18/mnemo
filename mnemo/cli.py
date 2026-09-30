import argparse
import datetime
import json
import os
import signal
import sys
import time

from . import __version__
from .index import DEFAULT_DB_PATH, SCHEMA_VERSION, Index, IndexTooNew
from . import remote as remote_mod
from .fingerprint import code_fingerprint
from .remote import LOCAL, RemoteError, fan_out_search, remote_context, remote_session
from .search import DEFAULT_KINDS, get_context, get_session, raw_context, raw_session, recent
from .sources import SOURCES

BOLD = "\033[1m"
DIM = "\033[2m"
YELLOW = "\033[33m"
RESET = "\033[0m"
_ANSI = {"BOLD": BOLD, "DIM": DIM, "YELLOW": YELLOW, "RESET": RESET}


def color_enabled(stream=None):
    """Color only on a terminal, and never with NO_COLOR set (https://no-color.org) or TERM=dumb."""
    stream = stream or sys.stdout
    try:
        tty = stream.isatty()
    except (AttributeError, ValueError):
        tty = False
    return tty and not os.environ.get("NO_COLOR") and os.environ.get("TERM") != "dumb"


def _set_color(on):
    # The styles are module constants used across the commands; blank them once
    # so piped or redirected output carries no escape codes.
    for name, code in _ANSI.items():
        globals()[name] = code if on else ""


def _hl(text, use_color):
    if use_color:
        return text.replace("[[", YELLOW + BOLD).replace("]]", RESET)
    return text.replace("[[", "").replace("]]", "")


def _since(value):
    if not value:
        return None
    if len(value) == 10:
        value += "T00:00:00Z"
    elif "T" not in value:
        value += "T00:00:00Z"
    return value


def _human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.0f %s" % (n, unit) if unit == "B" else "%.1f %s" % (n, unit)
        n /= 1024.0


def cmd_upgrade(args):
    from . import upgrade as up

    if args.list:
        backups = up.list_backups(args.db)
        if not backups:
            print("no backups in %s" % up.backup_dir(args.db))
        for b in backups:
            print("%s  %s" % (_human_size(os.path.getsize(b)).rjust(9), b))
        return 0

    if args.restore is not None:
        try:
            source, safety = up.restore(args.db, args.restore or None)
        except up.UpgradeError as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 1
        print("restored %s" % source)
        if safety:
            print("the index it replaced is saved as %s" % safety)
        return 0

    log = (lambda m: print("  " + m, file=sys.stderr)) if args.verbose else (lambda m: None)
    if args.if_needed and up.schema_current(args.db):
        # Pushed new code, same index schema: an incremental sync is enough.
        try:
            stats = Index(args.db).sync()
        except IndexTooNew as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 1
        print("index schema v%s is current; synced %d new, %d updated sessions"
              % (SCHEMA_VERSION, stats["files_new"], stats["files_updated"]))
        _restart_links()
        return 0
    if not args.no_backup:
        dest = up.backup(args.db, keep=args.keep)
        if dest:
            print("backup: %s (%s)" % (dest, _human_size(os.path.getsize(dest))))
    print("rebuilding schema v%s beside the live index ..." % SCHEMA_VERSION)
    try:
        stats, counts = up.rebuild(args.db, logger=log)
    except (up.UpgradeError, IndexTooNew) as exc:
        print("error: %s\nthe live index was not modified" % exc, file=sys.stderr)
        return 1
    files = sum(c["files"] for c in counts.values())
    msgs = sum(c["messages"] for c in counts.values())
    print("verified and swapped in: %d sessions, %d messages" % (files, msgs))

    rc = 0
    sys.stdout.flush()  # keep stdout/stderr in order when piped
    _restart_links()  # before listing stale processes, so links are not among them
    stale = up.stale_processes()
    if stale:
        print("warning: %d mnemo process(es) still run the previous code and may write"
              " old-format rows until restarted (the next sync repairs them):" % len(stale), file=sys.stderr)
        for pid, started, cmd in stale:
            print("  pid %d  since %s  %s" % (pid, time.strftime("%m-%d %H:%M", time.localtime(started)), cmd[-60:]),
                  file=sys.stderr)
        print("  restart the agent sessions that own them (or: kill %s); MCP servers and dashboards"
              " started by this version follow later updates on their own"
              % " ".join(str(p) for p, _, _ in stale), file=sys.stderr)

    if args.if_needed or args.no_remotes:
        return rc
    if not args.remotes:
        sys.stdout.flush()
        print("devices: bringing every reachable device to this code (skip with --no-remotes) ...")
        results, warnings = remote_mod.upgrade_devices(logger=lambda m: print("  " + m))
        _print_device_upgrades(results, warnings)
        return rc
    for r in remote_mod.pushable(remote_mod.load_remotes()):
        print("%s: syncing code ..." % r["name"])
        try:
            # Code first, index untouched: the remote `upgrade` backs it up before rebuilding.
            remote_mod.install(r, logger=lambda m, n=r["name"]: print("  %s: %s" % (n, m)),
                               build_index=False)
            argv = ["upgrade", "--no-remotes"] + (["--no-backup"] if args.no_backup else [])
            out = remote_mod.remote_exec(r, argv, timeout=1800)
            for line in out.strip().splitlines():
                print("  %s: %s" % (r["name"], line))
        except RemoteError as exc:
            print("warning: %s: %s" % (r["name"], exc), file=sys.stderr)
            rc = 1
    return rc


def _restart_links():
    """Link services keep running the old code until restarted."""
    from . import service
    try:
        service.restart_installed(log=lambda m: print("links: " + m))
    except RemoteError as exc:
        print("warning: links: %s" % exc, file=sys.stderr)


def _print_device_upgrades(results, warnings):
    marks = {"updated": "+", "current": "=", "failed": "!"}
    for item in results:
        print("  %s %-28s %s" % (marks[item["status"]], item["route"],
                                 item.get("error") or item["status"]))
    if not results and not warnings:
        print("  no other devices")
    for w in warnings:
        print("warning: %s" % w, file=sys.stderr)


def cmd_remote_upgrade(args):
    visited = set(filter(None, (args.visited or "").split(",")))
    results, warnings = remote_mod.upgrade_devices(
        routes=args.routes or None, visited=visited, ttl=args.ttl, relayed=args.relay,
        logger=(lambda m: None) if args.json else (lambda m: print("  " + m)))
    if args.json:
        print(json.dumps({"node": remote_mod.load_node()["id"], "results": results, "warnings": warnings},
                         ensure_ascii=False))
        return 0
    _print_device_upgrades(results, warnings)
    return 1 if any(r["status"] == "failed" for r in results) else 0


def _print_links(items, as_json):
    if as_json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
        return
    if not items:
        print("no remotes registered")
    for it in items:
        since = time.strftime("%m-%d %H:%M", time.localtime(it["since"])) if it.get("since") else ""
        print("%-16s %-10s %-11s %s%s" % (it["remote"], "service" if it["installed"] else "-", it["state"], since,
                                          ("  " + it["error"]) if it.get("error") and it["state"] != "connected" else ""))


def cmd_link(args):
    from . import link, service

    if args.list:
        _print_links(service.status(), args.json)
        return 0
    if args.serve:
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # still unlink the socket
        try:
            return link.serve()
        except RemoteError as exc:
            print("error: %s" % exc, file=sys.stderr)
            return link.REFUSED
    if not args.remote:
        print("usage: mnemo link <remote> --allow-inbound", file=sys.stderr)
        return 2
    try:
        r = remote_mod.get_remote(args.remote)
    except RemoteError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    if args.uninstall:
        try:
            m = service.uninstall(r["name"])
        except RemoteError as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 1
        print("stopped linking to %s (%s); it can no longer search this device" % (r["name"], m))
        return 0
    if not args.allow_inbound:
        print("mnemo link lets %s search and read this device's sessions (and, if this device\n"
              "forwards, the devices behind it) without being able to connect here itself.\n"
              "It gets no shell: only search, context, session, status and node info.\n"
              "Re-run with --allow-inbound to confirm." % r["name"], file=sys.stderr)
        return 2
    node = remote_mod.load_node()
    if args.install:
        try:
            m = service.install(r["name"])
        except RemoteError as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 1
        note = {"launchd": "starts at login", "systemd": "starts with your user session "
                "(`loginctl enable-linger` keeps it up without one)",
                "background": "runs until reboot; re-run after one"}[m]
        print("linking to %s as %r in the background via %s; %s" % (r["name"], node["name"], m, note))
        print("log: %s   status: mnemo link --list   revoke: mnemo link %s --uninstall"
              % (service.log_path(r["name"]), r["name"]))
        return 0
    print("keeping a link open to %s as %r (Ctrl-C to stop)" % (r["name"], node["name"]))
    # Service managers stop us with SIGTERM: exit through the normal path so the
    # link's state is recorded as stopped and its SSH session closed.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        link.run(r, log=lambda m: print(time.strftime("%H:%M:%S ") + m, flush=True))
    except link.LinkRefused as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print()
    return 0


def cmd_mcp(args):
    from . import mcp_server

    if args.call is not None:
        return mcp_server.call_main(args.call)
    return mcp_server.run()


def cmd_doctor(args):
    from . import doctor

    checks = doctor.run(full=args.full, offline=args.offline)
    failed = any(c.status == doctor.FAIL for c in checks)
    if args.json:
        print(json.dumps({"version": __version__, "checks": [c.to_dict() for c in checks]},
                         ensure_ascii=False, indent=2))
        return 1 if failed else 0
    encoding = (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "")
    print(doctor.render(checks, node_name=remote_mod.load_node()["name"], color=color_enabled(),
                        unicode=encoding.startswith("utf")))
    return 1 if failed else 0


def cmd_setup(args):
    from . import setup as st

    agents = [a.strip() for a in args.agent.split(",")] if args.agent else None
    unknown = set(agents or []) - set(st.AGENTS)
    if unknown:
        print("error: unknown agent(s): %s (choose from %s)" % (", ".join(sorted(unknown)), ",".join(st.AGENTS)),
              file=sys.stderr)
        return 2
    steps = st.run(agents, dry_run=args.dry_run)
    failed = any(s.status == "failed" for s in steps)
    if args.json:
        print(json.dumps({"dry_run": args.dry_run, "steps": [
            {"agent": s.agent, "action": s.action, "status": s.status, "detail": s.detail} for s in steps]},
            ensure_ascii=False, indent=2))
        return 1 if failed else 0
    encoding = (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "")
    print(st.render(steps, dry_run=args.dry_run, color=color_enabled(),
                    unicode=encoding.startswith("utf")))
    return 1 if failed else 0

def cmd_index(args):
    idx = Index(args.db)
    names = args.source.split(",") if args.source else None
    log = (lambda m: print(m, file=sys.stderr)) if args.verbose else None
    try:
        if getattr(args, "rebuild", False):
            stats = idx.rebuild(names, logger=log)
        else:
            stats = idx.sync(names, logger=log)
    except IndexTooNew as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    print(
        "indexed: +{files_new} new, {files_updated} updated, {files_removed} removed,"
        " {messages} messages".format(**stats)
    )
    return 0


def cmd_search(args):
    idx = Index(args.db)
    if args.all_kinds:
        kinds = []
    elif args.kind:
        kinds = args.kind.split(",")
    else:
        kinds = DEFAULT_KINDS
    hosts = [h.strip() for h in args.host.split(",")] if args.host else None
    if args.relay:
        return _relay_search(args, idx, kinds)
    try:
        hits, warnings = fan_out_search(
            idx,
            " ".join(args.query),
            sources=args.source.split(",") if args.source else None,
            kinds=kinds,
            cwd=args.cwd,
            since=_since(args.since),
            limit=args.limit,
            hosts=hosts,
            sync_local=not args.no_sync,
            include_injected=args.include_injected,
        )
    except RemoteError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    for w in warnings:
        print("warning: %s" % w, file=sys.stderr)
    if args.json:
        print(json.dumps(hits, ensure_ascii=False, indent=2))
        return 0
    if not hits:
        print("no matches")
        return 1
    color = color_enabled()
    for i, h in enumerate(hits, 1):
        print(
            "%s%d.%s %s[%s/%s]%s %s/%s  %s%s%s  %s%s%s"
            % (
                BOLD, i, RESET,
                BOLD, h.get("host", LOCAL), h["source"], RESET,
                h["role"], h["kind"],
                DIM, h["ts"][:16].replace("T", " "), RESET,
                DIM, h["cwd"], RESET,
            )
        )
        print("   " + _hl(h["snippet"], color))
        print("   %s→ %s:%d%s" % (DIM, h["path"], h["lineno"], RESET))
    return 0


def _relay_search(args, idx, kinds):
    """Answer a search forwarded by a neighbor (protocol 2): an envelope, never an error."""
    node = remote_mod.load_node()
    visited = set(filter(None, (args.visited or "").split(",")))
    hits, warnings = [], []
    if node["id"] not in visited:
        relay = node["forward"] and args.ttl > 0
        try:
            hits, warnings = fan_out_search(
                idx,
                " ".join(args.query),
                sources=args.source.split(",") if args.source else None,
                kinds=kinds,
                cwd=args.cwd,
                since=_since(args.since),
                limit=args.limit,
                hosts=None if relay else [LOCAL],
                sync_local=not args.no_sync,
                include_injected=args.include_injected,
                visited=visited,
                ttl=args.ttl,
            )
        except RemoteError as exc:
            warnings.append(str(exc))
    print(json.dumps({"node": {"id": node["id"], "name": node["name"]}, "hits": hits,
                      "warnings": warnings}, ensure_ascii=False))
    return 0


def _check_forward(args, host):
    """A read passing through this device on its way elsewhere needs forwarding enabled."""
    if host != LOCAL and getattr(args, "relay", False) and not remote_mod.load_node()["forward"]:
        raise RemoteError("%s does not forward reads to other devices (enable with `mnemo node --forward on`)"
                          % remote_mod.load_node()["name"])


def cmd_node(args):
    if args.probe:
        visited = set(filter(None, (args.visited or "").split(",")))
        print(json.dumps(remote_mod.probe_topology(visited, args.ttl, relayed=args.relay),
                         ensure_ascii=False))
        return 0
    node = remote_mod.load_node()
    if args.name is not None:
        try:
            node["name"] = remote_mod.check_node_name(args.name)
        except RemoteError as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 1
    if args.forward:
        node["forward"] = args.forward == "on"
    if args.name is not None or args.forward:
        remote_mod.save_node(node)
    neighbors = [{"name": r["name"], "host": r["host"], "node_id": r.get("node_id"), "proto": r.get("proto")}
                 for r in remote_mod.load_remotes()]
    if args.json:
        print(json.dumps(dict(node, code=code_fingerprint(), neighbors=neighbors), ensure_ascii=False, indent=2))
        return 0
    print("name     %s" % node["name"])
    print("id       %s" % node["id"])
    print("code     %s" % code_fingerprint())
    print("forward  %s%s" % ("on" if node["forward"] else "off",
                             "" if node["forward"] else "  (neighbors cannot search or read through this device)"))
    for n in neighbors:
        print("neighbor %-16s %-24s %s" % (n["name"], n["host"], (n["node_id"] or "id not learned yet")[:12]))
    return 0


def _slice(messages, head, tail):
    if head:
        messages = messages[:head]
    elif tail:
        messages = messages[-tail:]
    return messages


def _print_messages(path, rows, full_text=False, show_envelope=False):
    for r in rows:
        env = " env" if r.get("envelope") else ""
        print("%s%s:%d%s  %s%s/%s%s%s" % (
            BOLD, path, r["lineno"], RESET, DIM, r["role"], r["kind"], env, RESET))
        if show_envelope and r.get("body"):
            text = r["body"]
        else:
            text = r["text"]
        if not full_text and len(text) > 2000:
            text = text[:2000] + " …[truncated]"
        for ln in text.splitlines():
            print("    " + ln)
        print()


def cmd_session(args):
    host = args.host or LOCAL
    try:
        _check_forward(args, host)
        if host == LOCAL:
            idx = Index(args.db)
            sess = raw_session(idx, args.path) if args.raw else get_session(idx, args.path)
        else:
            sess = remote_session(
                host, args.path,
                head=args.head, tail=args.tail, raw=args.raw,
            )
    except RemoteError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    if sess is None:
        print("not in index; run `mnemo index`", file=sys.stderr)
        return 1
    if host == LOCAL:
        sess["messages"] = _slice(sess["messages"], args.head, args.tail)
    if args.json:
        print(json.dumps(sess, ensure_ascii=False, indent=2))
        return 0
    print("%s%s%s  source=%s  cwd=%s%s" % (
        BOLD, args.path, RESET, sess["source"], sess["cwd"],
        "  RAW (full bodies from file)" if args.raw else ""))
    print("%s%s → %s  %d messages (showing %d)%s\n" % (
        DIM, (sess["started_at"] or "")[:16].replace("T", " "),
        (sess["ended_at"] or "")[:16].replace("T", " "),
        sess["count"], len(sess["messages"]), RESET))
    _print_messages(args.path, sess["messages"], full_text=args.raw,
                    show_envelope=args.show_envelope)
    return 0


def cmd_context(args):
    host = args.host or LOCAL
    try:
        _check_forward(args, host)
    except RemoteError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    if host == LOCAL:
        idx = Index(args.db)
        rows = raw_context(idx, args.path, args.line, args.before, args.after) if args.raw \
            else get_context(idx, args.path, args.line, args.before, args.after)
    else:
        try:
            rows = remote_context(
                host, args.path, args.line,
                args.before, args.after, raw=args.raw,
            )
        except RemoteError as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 1
    if rows is None:
        print("not in index; run `mnemo index`", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    color = color_enabled()
    for r in rows:
        mark = ">" if r["hit"] else " "
        line = "%s %s %s:%s %s %s" % (
            mark,
            r["ts"][11:16],
            r["role"],
            r["kind"],
            r["path"] if False else "",
            "",
        )
        env = " env" if r.get("envelope") else ""
        print("%s%s:%d%s  %s%s/%s%s%s" % (
            BOLD if r["hit"] else DIM, args.path, r["lineno"], RESET,
            DIM, r["role"], r["kind"], env, RESET))
        text = (r.get("body") or r["text"]) if args.show_envelope else r["text"]
        if len(text) > 2000:
            text = text[:2000] + " …[truncated]"
        for ln in text.splitlines():
            print("    " + ln)
        print()
    return 0


def cmd_status(args):
    idx = Index(args.db)
    counts = idx.counts()
    last = idx.last_sync()
    remotes = remote_mod.load_remotes()
    if args.json:
        print(json.dumps({
            "db": args.db,
            "last_sync": last,
            "sources": {
                name: counts.get(name, {"files": 0, "messages": 0}) for name in sorted(SOURCES)
            },
            "remotes": remotes,
        }, ensure_ascii=False, indent=2))
        return 0
    for name in sorted(SOURCES):
        c = counts.get(name, {"files": 0, "messages": 0})
        print("%-8s %5d sessions  %8d messages" % (name, c["files"], c["messages"]))
    if last:
        print("last sync:", datetime.datetime.fromtimestamp(last).strftime("%Y-%m-%d %H:%M:%S"))
    print("db:       %s" % args.db)
    for r in remotes:
        print("remote:   %s → %s (%s)" % (r["name"], r["host"], r["bin"]))
    return 0


def cmd_recent(args):
    idx = Index(args.db)
    if not args.no_sync:
        try:
            idx.sync_if_stale()
        except Exception as exc:
            print("warning: index not refreshed (%s)" % exc, file=sys.stderr)
    sources = args.source.split(",") if args.source else None
    rows = recent(
        idx, sources=sources, cwd=args.cwd,
        since=_since(args.since), limit=args.limit,
    )
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("no sessions")
        return 0
    for r in rows:
        ts = (r["started_ts"] or "")[:16].replace("T", " ")
        print("%s%s%s  %s[%s]%s  %s%s%s" % (
            BOLD, ts, RESET, BOLD, r["source"], RESET, DIM, r["cwd"] or "", RESET))
        print("   " + r["title"])
        print("   %s%d msgs -> %s%s" % (DIM, r["messages"] or 0, r["path"], RESET))
    return 0


def cmd_remote_add(args):
    remote = {"name": args.name, "host": args.ssh_host or args.name,
              "bin": args.bin or "~/mnemo/bin/mnemo"}
    try:
        if any(r["name"] == remote["name"] for r in remote_mod.load_remotes()):
            raise RemoteError("remote %r already registered; remove it first" % remote["name"])
        print("connecting to %s ..." % remote["host"], file=sys.stderr)
        remote_mod.ping(remote)
        print("installing code and building index ...", file=sys.stderr)
        remote_mod.install(remote, logger=lambda m: print(m, file=sys.stderr))
        remote_mod.add_remote(remote["name"], remote["host"], remote["bin"])
    except RemoteError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    print("added remote %s (%s)" % (remote["name"], remote["host"]))
    return 0


def cmd_remote_list(args):
    remotes = remote_mod.load_remotes()
    if not remotes:
        print("no remotes; add one with: mnemo remote add <name> [ssh-host]")
        return 0
    for r in remotes:
        print("%-16s %-32s %s" % (r["name"], r["host"], r["bin"]))
    return 0


def cmd_remote_remove(args):
    try:
        remote_mod.remove_remote(args.name)
    except RemoteError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    print("removed remote %s" % args.name)
    return 0


def cmd_dashboard(args):
    from .dashboard import DEFAULT_PORT, serve
    serve(port=args.port or DEFAULT_PORT, open_browser=not args.no_open)
    return 0


def cmd_remote_update(args):
    try:
        remotes = remote_mod.load_remotes()
        if args.name:
            remotes = [r for r in remotes if r["name"] == args.name]
            if not remotes:
                raise RemoteError("no remote named %r" % args.name)
        else:
            remotes = remote_mod.pushable(remotes)
        for r in remotes:
            print("updating %s ..." % r["name"], file=sys.stderr)
            remote_mod.install(r, logger=lambda m: print("  %s" % m, file=sys.stderr))
            print("updated %s" % r["name"])
    except RemoteError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    return 0


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(prog="mnemo", description="search across agent sessions on this machine and remote devices")
    p.add_argument("--db", default=DEFAULT_DB_PATH, help=argparse.SUPPRESS)
    p.add_argument("--version", action="version", version="mnemo " + __version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("index", help="incrementally index local sessions")
    sp.add_argument("--source", help="comma-separated: %s" % ",".join(SOURCES))
    sp.add_argument("--rebuild", action="store_true",
                    help="drop the index and reparse every session from scratch")
    sp.add_argument("-v", "--verbose", action="store_true")
    sp.set_defaults(func=cmd_index)

    sp = sub.add_parser("doctor", help="check the index, agents, devices and links, and say how to fix what is off")
    sp.add_argument("--full", action="store_true", help="also verify the index file (takes a few seconds)")
    sp.add_argument("--offline", action="store_true", help="skip the check for a newer mnemo on PyPI")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("setup", help="connect Claude Code, Codex, OpenCode and Pi to mnemo (safe to re-run)")
    sp.add_argument("--agent", help="comma-separated subset of claude,codex,opencode,pi (default: every one detected)")
    sp.add_argument("--dry-run", action="store_true", help="show what would change without changing anything")
    sp.add_argument("--json", action="store_true", help="machine-readable steps")
    sp.set_defaults(func=cmd_setup)

    sp = sub.add_parser(
        "upgrade",
        help="back up the index, rebuild it beside the live one, verify, then swap it in",
    )
    sp.add_argument("--no-backup", action="store_true", help="skip the pre-upgrade backup")
    sp.add_argument("--keep", type=int, default=3, help="backups to keep (default 3)")
    sp.add_argument("--remotes", action="store_true",
                    help="sync code to every registered device and rebuild its index, even if current")
    sp.add_argument("--no-remotes", action="store_true",
                    help="leave other devices alone (default: update those running different code)")
    # Run on a device that was just sent new code: rebuild only if the schema changed.
    sp.add_argument("--if-needed", action="store_true", help=argparse.SUPPRESS)
    sp.add_argument("--restore", nargs="?", const="", metavar="BACKUP",
                    help="replace the index with a backup (default: the newest)")
    sp.add_argument("--list", action="store_true", help="list backups")
    sp.add_argument("-v", "--verbose", action="store_true")
    sp.set_defaults(func=cmd_upgrade)

    sp = sub.add_parser("search", aliases=["query"], help="full-text search")
    sp.add_argument("query", nargs="+")
    sp.add_argument("--source", help="comma-separated source filter")
    sp.add_argument("--kind", help="comma-separated kinds: text,summary,tool_call,tool_result,reasoning")
    sp.add_argument("--all-kinds", action="store_true")
    sp.add_argument("--cwd", help="substring match on working directory")
    sp.add_argument("--since", help="YYYY-MM-DD")
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--host", help="comma-separated devices or routes, e.g. local,devbox-109/devbox-126 "
                                   "(default: everything reachable)")
    # Protocol 2, used between devices: answer a neighbor's forwarded search.
    sp.add_argument("--relay", action="store_true", help=argparse.SUPPRESS)
    sp.add_argument("--visited", help=argparse.SUPPRESS)
    sp.add_argument("--ttl", type=int, default=0, help=argparse.SUPPRESS)
    sp.add_argument("--no-sync", action="store_true",
                    help="skip the incremental local index sync before searching")
    sp.add_argument("--include-injected", action="store_true",
                    help="also match boilerplate/envelope bodies that are hidden by default")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("context", help="show messages around a hit")
    sp.add_argument("path")
    sp.add_argument("line", type=int)
    sp.add_argument("--before", type=int, default=4)
    sp.add_argument("--after", type=int, default=8)
    sp.add_argument("--host", help="device or route holding the hit, from the search result (default: local)")
    sp.add_argument("--relay", action="store_true", help=argparse.SUPPRESS)
    sp.add_argument("--raw", action="store_true", help="read full bodies from the original session file (no 20k cap)")
    sp.add_argument("--show-envelope", action="store_true",
                    help="show verbatim bodies including stripped boilerplate instead of cleaned text")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_context)

    sp = sub.add_parser("session", help="dump all indexed messages of one session file")
    sp.add_argument("path")
    sp.add_argument("--host", help="device or route holding the session, from the search result (default: local)")
    sp.add_argument("--relay", action="store_true", help=argparse.SUPPRESS)
    sp.add_argument("--head", type=int, default=0, help="only first N messages")
    sp.add_argument("--tail", type=int, default=0, help="only last N messages")
    sp.add_argument("--raw", action="store_true", help="read full bodies from the original session file (no 20k cap)")
    sp.add_argument("--show-envelope", action="store_true",
                    help="show verbatim bodies including stripped boilerplate instead of cleaned text")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_session)

    sp = sub.add_parser("node", help="this device's name, id and relay policy")
    sp.add_argument("--name", help="rename this device (how neighbors see it)")
    sp.add_argument("--forward", choices=("on", "off"),
                    help="relay searches and reads so neighbors can reach devices behind this one")
    sp.add_argument("--json", action="store_true")
    # Protocol 2, used between devices and by the dashboard: map the reachable network.
    sp.add_argument("--probe", action="store_true", help=argparse.SUPPRESS)
    sp.add_argument("--relay", action="store_true", help=argparse.SUPPRESS)
    sp.add_argument("--visited", help=argparse.SUPPRESS)
    sp.add_argument("--ttl", type=int, default=remote_mod.DEFAULT_TTL, help=argparse.SUPPRESS)
    sp.set_defaults(func=cmd_node)

    sp = sub.add_parser("link", help="let a remote you can reach search this device back over your SSH session")
    sp.add_argument("remote", nargs="?", help="a registered remote, e.g. devbox-109")
    sp.add_argument("--allow-inbound", action="store_true",
                    help="confirm that the remote may search and read this device's sessions")
    sp.add_argument("--install", action="store_true",
                    help="run the link as a background service that starts at login (launchd/systemd)")
    sp.add_argument("--uninstall", action="store_true", help="stop and remove the link service (revoke access)")
    sp.add_argument("--list", action="store_true", help="show link services and their state")
    sp.add_argument("--json", action="store_true")
    # Run on the remote end of a link, over the SSH session it opened.
    sp.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    sp.set_defaults(func=cmd_link)

    sp = sub.add_parser("status", help="index stats")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("recent", help="most recently started sessions with their first task")
    sp.add_argument("--source", help="comma-separated subset of: %s" % ",".join(SOURCES))
    sp.add_argument("--cwd", help="substring match on working directory")
    sp.add_argument("--since", help="YYYY-MM-DD")
    sp.add_argument("--limit", type=int, default=25)
    sp.add_argument("--no-sync", action="store_true", help="skip the incremental sync first")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_recent)

    sp = sub.add_parser("remote", help="manage remote devices")
    rsub = sp.add_subparsers(dest="remote_cmd", required=True)

    rsp = rsub.add_parser("add", help="install mnemo on a device over SSH and register it")
    rsp.add_argument("name", help="local name for the device, e.g. devbox-109")
    rsp.add_argument("ssh_host", nargs="?", help="SSH host alias (defaults to name)")
    rsp.add_argument("--bin", help="remote mnemo launcher path (default: ~/mnemo/bin/mnemo)")
    rsp.set_defaults(func=cmd_remote_add)

    rsp = rsub.add_parser("list", help="list registered devices")
    rsp.set_defaults(func=cmd_remote_list)

    rsp = rsub.add_parser("remove", help="unregister a device (does not touch files on it)")
    rsp.add_argument("name")
    rsp.set_defaults(func=cmd_remote_remove)

    rsp = rsub.add_parser("upgrade", help="bring devices running other code to this device's code, "
                                          "through relays too (default: every reachable device)")
    rsp.add_argument("routes", nargs="*", metavar="ROUTE", help="only these devices, e.g. devbox-109/devbox-126")
    rsp.add_argument("--json", action="store_true")
    rsp.add_argument("--relay", action="store_true", help=argparse.SUPPRESS)
    rsp.add_argument("--visited", help=argparse.SUPPRESS)
    rsp.add_argument("--ttl", type=int, default=remote_mod.DEFAULT_TTL, help=argparse.SUPPRESS)
    rsp.set_defaults(func=cmd_remote_upgrade)

    rsp = rsub.add_parser("update", help="re-sync code and re-index one device or all remotes")
    rsp.add_argument("name", nargs="?", help="device name (default: all)")
    rsp.set_defaults(func=cmd_remote_update)

    sp = sub.add_parser("mcp", help="run MCP stdio server")
    # One tool call in a fresh process; the server runs every call this way.
    sp.add_argument("--call", metavar="TOOL", help=argparse.SUPPRESS)
    sp.set_defaults(func=cmd_mcp)

    sp = sub.add_parser("dashboard", help="open the local web admin panel")
    sp.add_argument("--port", type=int, default=7787)
    sp.add_argument("--no-open", action="store_true", help="do not open a browser")
    sp.set_defaults(func=cmd_dashboard)

    _set_color(color_enabled())
    args = p.parse_args(argv)
    return args.func(args)
