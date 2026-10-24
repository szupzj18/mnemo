import argparse
import datetime
import json
import os
import signal
import sys
import time

from . import __version__
from . import archive as archive_mod
from .index import DEFAULT_DB_PATH, SCHEMA_VERSION, Index, IndexTooNew
from . import remote as remote_mod
from .fingerprint import code_fingerprint
from .remote import LOCAL, RemoteError, fan_out_search, remote_context, remote_session
from .search import (CONTEXT_MAX_CHARS, DEFAULT_KINDS, archived_count, caller_session_ids, clip_bodies,
                     context_max_chars, get_context, get_session, origin, raw_context, raw_session, recent,
                     split_ids)
from .sources import SOURCES
from .sources.base import SourceUnavailable

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
    migrating = up.migratable(args.db)
    if migrating:
        print("migrating schema v%s to v%s beside the live index ..." % (up._stored_version(args.db), SCHEMA_VERSION))
    else:
        print("rebuilding schema v%s beside the live index ..." % SCHEMA_VERSION)
    try:
        if migrating:
            uses, counts = up.migrate(args.db, logger=log)
        else:
            stats, counts = up.rebuild(args.db, logger=log)
    except (up.UpgradeError, IndexTooNew) as exc:
        print("error: %s\nthe live index was not modified" % exc, file=sys.stderr)
        return 1
    files = sum(c["files"] for c in counts.values())
    msgs = sum(c["messages"] for c in counts.values())
    print("verified and swapped in: %d sessions, %d messages%s"
          % (files, msgs, " (%d skill uses found)" % uses if migrating else ""))

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
        return mcp_server.call_main(args.call, args.db)
    return mcp_server.run(args.db)


def cmd_doctor(args):
    from . import doctor

    if args.json:
        checks = doctor.run(full=args.full, offline=args.offline)
        print(json.dumps({"version": __version__, "checks": [c.to_dict() for c in checks]},
                         ensure_ascii=False, indent=2))
        return 1 if any(c.status == doctor.FAIL for c in checks) else 0
    encoding = (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "")
    live = color_enabled()
    style = doctor.Style(color=live, unicode=encoding.startswith("utf"))
    print(doctor.render_header(remote_mod.load_node()["name"], style) + "\n", flush=True)
    checks = []
    # Print each group as soon as it is checked; on a terminal, say what is running
    # meanwhile (devices can take seconds when one is slow or down).
    for group, run in doctor.plan(full=args.full, offline=args.offline):
        if live:
            sys.stdout.write("  " + style.paint("2", "checking %s..." % group.lower()))
            sys.stdout.flush()
        rows = run()
        if live:
            sys.stdout.write("\r\033[K")
        if rows:
            print(doctor.render_group(group, rows, style), flush=True)
        checks += rows
    print("\n" + doctor.render_summary(checks, style))
    return 1 if any(c.status == doctor.FAIL for c in checks) else 0

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
        + (", {files_archived} copied to the archive".format(**stats) if stats.get("files_archived") else "")
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
    coverage = [] if args.coverage else None
    if args.relay:
        return _relay_search(args, idx, kinds)
    excluded = split_ids(args.exclude_session)
    current = [] if args.include_current else caller_session_ids()
    excluded += [sid for sid in current if sid not in excluded]
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
            coverage=coverage,
            exclude_sessions=excluded,
        )
    except RemoteError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    for w in warnings:
        print("warning: %s" % w, file=sys.stderr)
    if args.json:
        result = hits
        if args.coverage:
            result = {"hits": hits, "coverage": coverage, "warnings": warnings, "excluded_sessions": excluded}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if not hits:
        print("no matches")
        return 1
    color = color_enabled()
    for i, h in enumerate(hits, 1):
        print(
            "%s%d.%s %s[%s/%s%s]%s %s/%s  %s%s%s  %s%s%s"
            % (
                BOLD, i, RESET,
                BOLD, h.get("host", LOCAL), h["source"], _account(h), RESET,
                h["role"], h["kind"],
                DIM, h["ts"][:16].replace("T", " "), RESET,
                DIM, h["cwd"], RESET,
            )
        )
        print("   " + _hl(h["snippet"], color))
        print("   %s→ %s:%d%s%s" % (DIM, h["path"], h["lineno"], "  (archived)" if h.get("archived") else "", RESET))
    return 0


def _account(row):
    """' .claude-work' for a session from an extra account; nothing for the default config dir."""
    account = row.get("account")
    cls = SOURCES.get(row.get("source"))
    if not account or (cls is not None and account == cls.CONFIG_DIR):
        return ""
    return " " + account


def _relay_search(args, idx, kinds):
    """Answer a search forwarded by a neighbor (protocol 2): an envelope, never an error."""
    node = remote_mod.load_node()
    visited = set(filter(None, (args.visited or "").split(",")))
    hits, warnings, coverage = [], [], []
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
                coverage=coverage,
            )
        except RemoteError as exc:
            warnings.append(str(exc))
    print(json.dumps({"node": {"id": node["id"], "name": node["name"]}, "hits": hits,
                      "warnings": warnings, "coverage": coverage}, ensure_ascii=False))
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
    if args.archive:
        node["archive"] = args.archive == "on"
    if args.name is not None or args.forward or args.archive:
        remote_mod.save_node(node)
    neighbors = [{"name": r["name"], "host": r["host"], "node_id": r.get("node_id"), "proto": r.get("proto")}
                 for r in remote_mod.load_remotes()]
    if args.json:
        print(json.dumps(dict(node, archive=archive_mod.enabled(), code=code_fingerprint(), neighbors=neighbors),
                         ensure_ascii=False, indent=2))
        return 0
    print("name     %s" % node["name"])
    print("id       %s" % node["id"])
    print("code     %s" % code_fingerprint())
    print("forward  %s%s" % ("on" if node["forward"] else "off",
                             "" if node["forward"] else "  (neighbors cannot search or read through this device)"))
    archiving = archive_mod.enabled()
    print("archive  %s  (%s%s)" % (
        "on" if archiving else "off",
        "session logs are copied to %s and stay searchable after their agent deletes them"
        % archive_mod.root_for(args.db) if archiving else "sessions leave the index when their agent deletes the log",
        "; set by %s" % archive_mod.ENV if archive_mod.overridden() else ""))
    for n in neighbors:
        print("neighbor %-16s %-24s %s" % (n["name"], n["host"], (n["node_id"] or "id not learned yet")[:12]))
    return 0


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
            try:
                reader = raw_session if args.raw else get_session
                sess = reader(idx, args.path, head=args.head, tail=args.tail, limit=args.limit,
                              cursor=args.cursor, anchor_line=args.anchor_line)
            finally:
                idx.close()
        else:
            sess = remote_session(
                host, args.path,
                head=args.head, tail=args.tail, raw=args.raw,
                limit=args.limit, cursor=args.cursor, anchor_line=args.anchor_line,
            )
    except (RemoteError, ValueError, OSError, SourceUnavailable) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    if sess is None:
        print("not in index; run `mnemo index`", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(sess, ensure_ascii=False, indent=2))
        return 0
    print("%s%s%s  source=%s%s  cwd=%s%s" % (
        BOLD, args.path, RESET, sess["source"],
        "  account=%s" % sess["account"] if sess.get("account") else "", sess["cwd"],
        "  RAW (full bodies from file)" if args.raw else ""))
    if sess.get("archived"):
        print("%sarchived: the original log is gone; reading the copy at %s%s" % (DIM, sess["archive_path"], RESET))
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
    if not args.relay:  # a neighbor's request: it clips (or not) on its own side
        clip_bodies(rows, context_max_chars(args.max_chars, args.full, args.raw), hint="--full")
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if host == LOCAL and rows:
        _archived_note(idx, args.path)
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
        for ln in text.splitlines():
            print("    " + ln)
        print()
    return 0


def _archived_note(idx, path):
    row = idx.db.execute("SELECT source FROM files WHERE path = ?", (path,)).fetchone()
    found = origin(idx, row["source"], path) if row else {}
    if found.get("archived"):
        print("%sarchived: the original log is gone; reading the copy at %s%s\n"
              % (DIM, found["archive_path"], RESET))


def _skill_devices(args, sub, default_host):
    from . import skills

    try:
        return skills.collect(sub, args.db, host=args.host or default_host, since=getattr(args, "since", None),
                              no_sync=getattr(args, "no_sync", False), today=args.today,
                              query=" ".join(getattr(args, "query", None) or []))
    except IndexTooNew as exc:
        return {}, [str(exc)]


def _tilde(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path


def cmd_skill_ls(args):
    from . import skills

    devices, warnings = _skill_devices(args, "ls", LOCAL)
    found = skills.issues(devices)
    if args.json:
        print(json.dumps({"devices": devices, "issues": found, "warnings": warnings}, ensure_ascii=False, indent=2))
        return 0
    for name, inv in sorted(devices.items()):
        print("%s%s%s · %d folders · %d skills" % (BOLD, name, RESET, len(inv["dirs"]), len(inv["skills"])))
        for d in inv["dirs"]:
            extra = []
            if d["also"]:
                extra.append("also " + ", ".join(d["also"]))
            if d["managed"]:
                extra.append("left alone: " + ", ".join(d["managed"]))
            print("  %-8s %-34s %3d%s" % (d["agent"], _tilde(d["path"]) if name == LOCAL else d["path"],
                                         d["skills"], ("  " + DIM + "; ".join(extra) + RESET) if extra else ""))
        names = {}
        for s in inv["skills"]:
            names.setdefault(s["name"], []).append(s)
        print()
        for skill, items in sorted(names.items()):
            where = "  ".join("%s:%s%s" % (s["account"], s["kind"], "@" + s["hash"][:7] if s.get("hash") else "")
                              for s in items)
            print("  %-34s %s" % (skill, where))
        print()
    for i in found:
        if i["issue"] == "dangling":
            print("%sdangling%s %-26s %s -> %s" % (YELLOW, RESET, i["skill"], ", ".join(i["where"]), i.get("target")))
        else:
            print("%sforked%s   %-26s %d versions: %s" % (YELLOW, RESET, i["skill"], i["versions"],
                                                         "; ".join("%s %s" % (h[:7], ", ".join(w))
                                                                   for h, w in sorted(i["hashes"].items()))))
    for w in warnings:
        print("warning: %s" % w, file=sys.stderr)
    return 0


def cmd_skill_search(args):
    from . import skills

    query = " ".join(args.query)
    devices, warnings = _skill_devices(args, "search", "all")
    hits = skills.search(devices, query, limit=args.limit)
    if args.json:
        print(json.dumps({"hits": hits, "devices": sorted(devices), "warnings": warnings}, ensure_ascii=False, indent=2))
        return 0
    for h in hits:
        print("%s%s%s  %s%s%s" % (BOLD, h["skill"], RESET, DIM, ", ".join(h["where"]), RESET))
        if h["desc"]:
            print("    " + (h["desc"][:157] + "..." if len(h["desc"]) > 160 else h["desc"]))
    if not hits:
        print("no installed skill matches %r" % query)
    for w in warnings:
        print("warning: %s" % w, file=sys.stderr)
    return 0


def cmd_skill_stats(args):
    from . import skills

    by = [b.strip() for b in (args.by or "").split(",") if b.strip()]
    bad = [b for b in by if b not in ("device", "agent")]
    if bad:
        print("error: --by takes device and/or agent, not %s" % ",".join(bad), file=sys.stderr)
        return 2
    try:
        skills.parse_since(args.since, datetime.date.today())
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    devices, warnings = _skill_devices(args, "stats", "all")
    out = skills.report(devices, by, args.since, args.unused, args.gaps, warnings)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    keys = [k for k in ("device", "agent") if k in by]
    print("%sskill uses since %s%s · devices: %s" % (DIM, out["first_day"], RESET, ", ".join(out["devices"]) or "none"))
    head = "".join("%-12s" % k for k in keys) + "%-32s %6s %6s %6s %6s %6s %7s  %s" % (
        "skill", "calls", "sess", "user", "tool", "read", "total", "last")
    print(head)
    shown = [r for r in out["rows"] if r["calls"]] if not args.all else out["rows"]
    for r in shown[:args.limit]:
        print("".join("%-12s" % r[k] for k in keys) + "%-32s %6d %6d %6d %6d %6d %7d  %s" % (
            r["skill"][:32], r["calls"], r["sessions"], r["user"], r["tool"], r["read"], r["total"], r["last"][5:]))
    if len(shown) > args.limit:
        print("%s... %d more (--limit)%s" % (DIM, len(shown) - args.limit, RESET))
    if args.unused:
        print("\nunused since %s (installed, no use on any device): %s"
              % (out["first_day"], " ".join(out["unused"]) or "none"))
    if args.gaps:
        print("\nused on one device, missing on another:")
        for g in out["gaps"] or []:
            print("  %-30s -> %-14s %s %s, %d sessions" % (g["skill"], g["to"], g["from"], g["agent"], g["sessions"]))
        if not out["gaps"]:
            print("  none")
    for w in warnings:
        print("warning: %s" % w, file=sys.stderr)
    return 0


_SOURCE_STATES = {
    "clone": "git clone would work",
    "cloned": "cloned",
    "pull": "git pull --ff-only would run",
    "pulled": "pulled (git pull --ff-only)",
    "stale": "used as it is",
    "ready": "used as it is",
    "current": "same content as this device's checkout; nothing to push",
    "push": "pushed from this device's checkout (rsync)",
    "missing": "not available",
    "foreign": "not used",
}


def _sync_setup(args):
    from . import skillsync

    try:
        manifest = skillsync.load_manifest(args.manifest or os.path.join(remote_mod.CONFIG_DIR, skillsync.MANIFEST))
        return manifest, skillsync.devices_for(args.device)
    except skillsync.SyncError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return None, None


def _each_device(devices, step):
    results, failed = {}, False
    for name, remote in devices:
        try:
            results[name] = step(name, remote)
        except RemoteError as exc:
            results[name] = {"device": name, "error": str(exc)}
        failed = failed or bool(results[name].get("error"))
    return results, failed


def _short(sha):
    return (sha or "?")[:7]


def _print_actions(actions, show_all):
    from .skillsync import ROOTS

    by_root, counts = {}, {}
    for a in actions:
        counts[a["action"]] = counts.get(a["action"], 0) + 1
        if show_all or a["action"] != "keep":
            by_root.setdefault(a.get("root") or "", []).append(a)
    for root, items in sorted(by_root.items()):
        print("  %s%s%s" % (DIM, ROOTS.get(root, root), RESET))
        for a in items:
            if a["action"] in ("link", "relink"):
                text = "-> " + _tilde(a["target"])
            elif a["action"] in ("adopt", "keep"):
                text = "already -> " + _tilde(a.get("detail") or a["target"])
            else:
                text = a.get("detail") or ""
            warn = a["action"] in ("conflict", "error")
            print("    %s%-8s%s %-40s %s" % (YELLOW if warn else "", a["action"], RESET if warn else "",
                                            a.get("name") or _tilde(a["link"]), text))
    print("  " + (" · ".join("%d %s" % (n, k) for k, n in sorted(counts.items())) or "nothing to do"))


def cmd_skill_sync(args):
    from . import skillsync

    manifest, devices = _sync_setup(args)
    if manifest is None:
        return 2
    results, failed = _each_device(
        devices, lambda name, remote: skillsync.sync_device(name, manifest, args.dry_run, remote=remote))
    if args.json:
        print(json.dumps({"dry_run": args.dry_run, "devices": results}, ensure_ascii=False, indent=2))
        return 1 if failed else 0
    for name, r in results.items():
        print("%s%s%s · %s" % (BOLD, name, RESET, "dry run, nothing changed" if args.dry_run else "sync"))
        if r.get("error"):
            print("  %serror%s %s" % (YELLOW, RESET, r["error"]))
            print()
            continue
        for st in r.get("prepared") or r["sources"]:
            state = _SOURCE_STATES[st["state"]]
            if args.dry_run and st["state"] == "push":
                state = "would push this device's checkout (rsync)"
            kind = {"git": "git", "copy": "copy without git history"}.get(st["kind"], "no checkout")
            print("  source %s  %s  %s @%s: %s%s" % (
                st["source"], _tilde(st["checkout"]) if name == LOCAL else st["checkout"], kind,
                _short(st["commit"]), state, ("; " + st["note"]) if st.get("note") else ""))
        _print_actions(r["actions"], args.all)
        for n in r.get("notes") or []:
            print("  %snote:%s %s" % (DIM, RESET, n))
        if not args.dry_run:
            print("  %s%s%s" % (DIM, "lock written" if r.get("written") else "lock unchanged", RESET))
        print()
    return 1 if failed else 0


def cmd_skill_rollback(args):
    from . import skillsync

    manifest, devices = _sync_setup(args)
    if manifest is None:
        return 2
    results, failed = _each_device(
        devices, lambda name, remote: skillsync.rollback_device(name, manifest, args.dry_run, remote=remote))
    if args.json:
        print(json.dumps({"dry_run": args.dry_run, "devices": results}, ensure_ascii=False, indent=2))
        return 1 if failed else 0
    for name, r in results.items():
        print("%s%s%s · %s" % (BOLD, name, RESET, "rollback, dry run, nothing changed" if args.dry_run else "rollback"))
        if r.get("error"):
            print("  %serror%s %s" % (YELLOW, RESET, r["error"]))
            print()
            continue
        print("  %sback to the lock saved as %s%s" % (DIM, r["lock"], RESET))
        for c in r["content"]:
            print("  source %s  %s -> %s: %s%s" % (c["source"], _short(c["from"]), _short(c["commit"]), c["action"],
                                                 ("; " + c["detail"]) if c.get("detail") else ""))
        _print_actions(r["actions"], args.all)
        for link, why in r.get("mismatches") or []:
            print("  %sdiffers from the lock%s %s: %s" % (YELLOW, RESET, _tilde(link), why))
        if "mismatches" in r and not r["mismatches"]:
            print("  %severy link matches the earlier lock%s" % (DIM, RESET))
        print()
    return 1 if failed else 0


def cmd_status(args):
    idx = Index(args.db)
    counts = idx.counts()
    last = idx.last_sync()
    remotes = remote_mod.load_remotes()
    files, size = idx.archive.usage()
    archive = {"enabled": archive_mod.enabled(), "path": idx.archive.root, "files": files, "bytes": size,
               "sessions_archived": archived_count(idx)}
    if args.json:
        print(json.dumps({
            "db": args.db,
            "last_sync": last,
            "sources": {
                name: counts.get(name, {"files": 0, "messages": 0}) for name in sorted(SOURCES)
            },
            "archive": archive,
            "remotes": remotes,
        }, ensure_ascii=False, indent=2))
        return 0
    for name in sorted(SOURCES):
        c = counts.get(name, {"files": 0, "messages": 0})
        print("%-8s %5d sessions  %8d messages" % (name, c["files"], c["messages"]))
    if last:
        print("last sync:", datetime.datetime.fromtimestamp(last).strftime("%Y-%m-%d %H:%M:%S"))
    print("db:       %s" % args.db)
    print("archive:  %s, %d files (%s) in %s; %d sessions kept after their logs were deleted" % (
        "on" if archive["enabled"] else "off", files, _human_size(size), idx.archive.root,
        archive["sessions_archived"]))
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
        print("%s%s%s  %s[%s%s]%s  %s%s%s" % (
            BOLD, ts, RESET, BOLD, r["source"], _account(r), RESET, DIM, r["cwd"] or "", RESET))
        print("   " + r["title"])
        print("   %s%d msgs -> %s%s%s" % (DIM, r["messages"] or 0, r["path"],
                                        "  (archived)" if r.get("archived") else "", RESET))
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

    sp = sub.add_parser("setup", help="connect Claude Code, Codex, OpenCode, Pi, Antigravity CLI and Grok CLI to mnemo"
                                      " (safe to re-run)")
    sp.add_argument("--agent", help="comma-separated subset of claude,codex,opencode,pi,agy,grok"
                                    " (default: every one detected)")
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
    sp.add_argument("--coverage", action="store_true", help="include device/index coverage in JSON results")
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
    sp.add_argument("--exclude-session", action="append", metavar="ID",
                    help="leave out this session id (repeatable or comma-separated)")
    sp.add_argument("--include-current", action="store_true",
                    help="keep the calling agent's own session, which is left out by default "
                         "when CLAUDE_CODE_SESSION_ID, CODEX_THREAD_ID or CODEX_SESSION_ID is set")
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
    sp.add_argument("--max-chars", type=int, metavar="N",
                    help="clip each message to N characters (default: %d; 0 = whole bodies)" % CONTEXT_MAX_CHARS)
    sp.add_argument("--full", action="store_true",
                    help="print whole message bodies instead of clipping each at %d characters"
                         " (implied by --raw)" % CONTEXT_MAX_CHARS)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_context)

    sp = sub.add_parser("session", help="dump all indexed messages of one session file")
    sp.add_argument("path")
    sp.add_argument("--host", help="device or route holding the session, from the search result (default: local)")
    sp.add_argument("--relay", action="store_true", help=argparse.SUPPRESS)
    sp.add_argument("--head", type=int, default=0, help="only first N messages")
    sp.add_argument("--tail", type=int, default=0, help="only last N messages")
    sp.add_argument("--limit", type=int, help="page size, 1–500 messages")
    sp.add_argument("--cursor", help="opaque cursor from a previous session page")
    sp.add_argument("--anchor-line", type=int, help="center the first page around this source line")
    sp.add_argument("--raw", action="store_true", help="read full bodies from the original session file (no 20k cap)")
    sp.add_argument("--show-envelope", action="store_true",
                    help="show verbatim bodies including stripped boilerplate instead of cleaned text")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_session)

    sp = sub.add_parser("node", help="this device's name, id and relay policy")
    sp.add_argument("--name", help="rename this device (how neighbors see it)")
    sp.add_argument("--forward", choices=("on", "off"),
                    help="relay searches and reads so neighbors can reach devices behind this one")
    sp.add_argument("--archive", choices=("on", "off"),
                    help="keep a copy of every session log beside the index, so sessions stay searchable "
                         "after Claude Code, Codex or Pi delete them (default: on)")
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

    sp = sub.add_parser("skill", help="agent skills on this and other devices: list, search, usage counts")
    ksub = sp.add_subparsers(dest="skill_cmd", required=True)
    for name, func, helptext, default in (
        ("ls", cmd_skill_ls, "skill folders of every agent, with links, dangling links and differing copies", "local"),
        ("search", cmd_skill_search, "installed skills whose name or description has every word", "all"),
        ("stats", cmd_skill_stats, "how often sessions used each skill", "all"),
    ):
        ksp = ksub.add_parser(name, help=helptext)
        if name == "search":
            ksp.add_argument("query", nargs="+")
            ksp.add_argument("--limit", type=int, default=20)
        if name == "stats":
            ksp.add_argument("--since", default="30d", help="Nd or YYYY-MM-DD (default: 30d)")
            ksp.add_argument("--by", default="device,agent", help="group by device and/or agent (default: device,agent)")
            ksp.add_argument("--unused", action="store_true", help="also list installed skills nobody used")
            ksp.add_argument("--gaps", action="store_true",
                             help="also list skills used in 3+ sessions on one device that another device lacks")
            ksp.add_argument("--all", action="store_true", help="also show skills used only before --since")
            ksp.add_argument("--limit", type=int, default=40, help="rows to print (default 40)")
            ksp.add_argument("--no-sync", action="store_true", help="skip the incremental index sync first")
        ksp.add_argument("--host", help="local, all, or comma-separated device names (default: %s)" % default)
        ksp.add_argument("--today", help=argparse.SUPPRESS)  # YYYY-MM-DD; fixes the window in tests
        ksp.add_argument("--json", action="store_true")
        ksp.set_defaults(func=func)
    for name, func, helptext in (
        ("sync", cmd_skill_sync, "link the skills ~/.mnemo/skills.toml names into ~/.claude/skills and "
                                 "~/.agents/skills, here or on devices"),
        ("rollback", cmd_skill_rollback, "go back to the lock before the last sync that changed something"),
    ):
        ksp = ksub.add_parser(name, help=helptext)
        ksp.add_argument("--dry-run", action="store_true", help="show what would change; change nothing")
        ksp.add_argument("--device", default="local", help="local (default), all, or comma-separated device names")
        ksp.add_argument("--manifest", help="manifest path (default: ~/.mnemo/skills.toml)")
        ksp.add_argument("--all", action="store_true", help="also list links that stay as they are")
        ksp.add_argument("--json", action="store_true")
        ksp.set_defaults(func=func)

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
