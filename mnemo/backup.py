"""Automatic and scheduled backups of the mnemo SQLite index.

Reuses upgrade.backup() (SQLite online backup API) so snapshots are consistent
even while other mnemo processes are reading or writing. Supports:

- On-demand snapshots (`mnemo backup`), with custom --dest and --keep.
- Periodic auto-backup after `mnemo index` (configured in node.json under
  `backup.auto`, off by default, never on the search hot path).
- OS-scheduled backups via launchd (macOS) or systemd user timers (Linux).
"""
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import time

from . import link
from . import remote as remote_mod
from . import upgrade as up
from .index import DEFAULT_DB_PATH
from .remote import RemoteError

DEFAULT_EVERY = "daily"
DEFAULT_KEEP = up.BACKUP_KEEP
MIN_INTERVAL_SEC = 60

LAUNCHD_LABEL = "dev.mnemo.backup"
SYSTEMD_UNIT = "mnemo-backup"

_NAMED_INTERVALS = {
    "hourly": 3600,
    "daily": 86400,
    "weekly": 7 * 86400,
}
_UNIT_SECONDS = {
    "m": 60,
    "h": 3600,
    "d": 86400,
    "w": 7 * 86400,
}


def parse_interval(value):
    """Parse 'daily', 'weekly', 'hourly', or '<N><m|h|d|w>' into seconds."""
    if value is None:
        raise ValueError("interval cannot be empty")
    raw = str(value).strip().lower()
    if raw in _NAMED_INTERVALS:
        return _NAMED_INTERVALS[raw]
    m = re.match(r"^(\d+)([mhdw])$", raw)
    if not m:
        raise ValueError(
            "invalid interval %r (use hourly, daily, weekly, or <N><m|h|d|w> such as 12h or 7d)" % value
        )
    secs = int(m.group(1)) * _UNIT_SECONDS[m.group(2)]
    if secs < MIN_INTERVAL_SEC:
        raise ValueError("interval must be at least 1m (60 seconds)")
    return secs


def normalize_keep(value):
    """Validate --keep (must be >= 1)."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ValueError("keep must be a positive integer")
    if n < 1:
        raise ValueError("keep must be at least 1")
    return n


def resolve_dest(db_path, dest=None):
    """Return the resolved backup directory for db_path."""
    if dest:
        return os.path.abspath(os.path.expanduser(dest))
    return up.backup_dir(db_path)


# --------------------------------------------------------------------- config

def load_config():
    """Return the backup settings stored in node.json, filled with defaults."""
    node = remote_mod.load_node()
    raw = node.get("backup") if isinstance(node.get("backup"), dict) else {}
    every = raw.get("every") or DEFAULT_EVERY
    try:
        parse_interval(every)
    except ValueError:
        every = DEFAULT_EVERY
    try:
        keep = normalize_keep(raw.get("keep", DEFAULT_KEEP))
    except ValueError:
        keep = DEFAULT_KEEP
    dest = raw.get("dest") or None
    return {
        "auto": bool(raw.get("auto", False)),
        "every": every,
        "keep": keep,
        "dest": dest,
    }


def save_config(auto=None, every=None, keep=None, dest=None, clear_dest=False):
    """Update backup settings in node.json and return the saved config."""
    cfg = load_config()
    if auto is not None:
        cfg["auto"] = bool(auto)
    if every is not None:
        parse_interval(every)
        cfg["every"] = str(every).strip().lower()
    if keep is not None:
        cfg["keep"] = normalize_keep(keep)
    if clear_dest:
        cfg["dest"] = None
    elif dest is not None:
        cfg["dest"] = os.path.abspath(os.path.expanduser(dest))
    node = remote_mod.load_node()
    node["backup"] = cfg
    remote_mod.save_node(node)
    return cfg


# -------------------------------------------------------------------- backups

def latest_backup(db_path, dest=None):
    """Newest backup path in dest (or default backups dir), or None."""
    items = up.list_backups(db_path, dest_dir=dest)
    return items[0] if items else None


def is_due(db_path, interval_sec, dest=None, now=None):
    """True when no backup exists yet or the newest backup is at least interval_sec old."""
    newest = latest_backup(db_path, dest=dest)
    if not newest:
        return True
    try:
        mtime = os.path.getmtime(newest)
    except OSError:
        return True
    current = time.time() if now is None else now
    return (current - mtime) >= interval_sec


def run_backup(db_path, keep=None, dest=None, label=None, if_due=False, every=None, now=None):
    """Create a backup snapshot (or skip if --if-due and a fresh backup already exists)."""
    cfg = load_config()
    eff_keep = normalize_keep(keep if keep is not None else cfg["keep"])
    eff_dest = resolve_dest(db_path, dest if dest is not None else cfg["dest"])
    eff_every = every if every is not None else cfg["every"]
    interval_sec = parse_interval(eff_every)

    if not os.path.isfile(db_path):
        return {
            "created": None,
            "size": 0,
            "skipped": True,
            "reason": "no index at %s" % db_path,
            "dest": eff_dest,
            "kept": len(up.list_backups(db_path, dest_dir=eff_dest)),
        }

    if if_due and not is_due(db_path, interval_sec, dest=eff_dest, now=now):
        newest = latest_backup(db_path, dest=eff_dest)
        return {
            "created": None,
            "size": os.path.getsize(newest) if newest and os.path.isfile(newest) else 0,
            "skipped": True,
            "reason": "latest backup is newer than %s" % eff_every,
            "latest": newest,
            "dest": eff_dest,
            "kept": len(up.list_backups(db_path, dest_dir=eff_dest)),
        }

    created = up.backup(db_path, keep=eff_keep, label=label, dest_dir=eff_dest)
    size = os.path.getsize(created) if created and os.path.isfile(created) else 0
    return {
        "created": created,
        "size": size,
        "skipped": False,
        "reason": None,
        "dest": eff_dest,
        "kept": len(up.list_backups(db_path, dest_dir=eff_dest)),
    }


def prune(db_path, keep=None, dest=None):
    """Remove backups beyond the newest `keep`. Returns removed paths."""
    cfg = load_config()
    eff_keep = normalize_keep(keep if keep is not None else cfg["keep"])
    eff_dest = resolve_dest(db_path, dest if dest is not None else cfg["dest"])
    removed = []
    for old in up.list_backups(db_path, dest_dir=eff_dest)[eff_keep:]:
        try:
            os.remove(old)
            removed.append(old)
        except OSError:
            pass
    return removed


def maybe_auto_backup(db_path, now=None):
    """Called after `mnemo index`: take a backup when backup.auto is on and due."""
    cfg = load_config()
    if not cfg["auto"]:
        return None
    return run_backup(db_path, keep=cfg["keep"], dest=cfg["dest"], label="auto",
                      if_due=True, every=cfg["every"], now=now)


# --------------------------------------------------------------- OS scheduler

def manager():
    forced = os.environ.get("MNEMO_SERVICE_MANAGER")
    if forced:
        return forced
    if sys.platform == "darwin":
        return "launchd"
    if shutil.which("systemctl") and _run(["systemctl", "--user", "show-environment"], check=False) == 0:
        return "systemd"
    return "none"


def _run(argv, check=True):
    """Run a service-manager command; returns its exit code (tests replace this)."""
    p = subprocess.run(argv, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RemoteError("%s failed: %s" % (" ".join(argv[:3]), (p.stderr or p.stdout).strip()[:200]))
    return p.returncode


def _home(*parts):
    return os.path.join(os.path.expanduser("~"), *parts)


def log_path():
    return os.path.join(remote_mod.CONFIG_DIR, "logs", "backup.log")


def backup_argv(db_path=None):
    argv = list(link.self_argv())
    if db_path and os.path.abspath(db_path) != os.path.abspath(DEFAULT_DB_PATH):
        argv += ["--db", os.path.abspath(db_path)]
    argv += ["backup", "--if-due", "--label", "scheduled"]
    return argv


# -------------------------------------------------------------------- launchd

def _plist_path():
    return _home("Library", "LaunchAgents", LAUNCHD_LABEL + ".plist")


def _domain():
    return "gui/%d" % os.getuid()


def launchd_plist(interval_sec, db_path=None):
    return {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": backup_argv(db_path),
        "StartInterval": int(interval_sec),
        "RunAtLoad": True,
        "StandardOutPath": log_path(),
        "StandardErrorPath": log_path(),
    }


def _launchd_install(interval_sec, db_path=None):
    path = _plist_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        plistlib.dump(launchd_plist(interval_sec, db_path), f)
    _run(["launchctl", "bootout", "%s/%s" % (_domain(), LAUNCHD_LABEL)], check=False)
    _run(["launchctl", "bootstrap", _domain(), path])


def _launchd_uninstall():
    _run(["launchctl", "bootout", "%s/%s" % (_domain(), LAUNCHD_LABEL)], check=False)
    _remove(_plist_path())


# -------------------------------------------------------------------- systemd

def _service_path():
    return _home(".config", "systemd", "user", SYSTEMD_UNIT + ".service")


def _timer_path():
    return _home(".config", "systemd", "user", SYSTEMD_UNIT + ".timer")


def systemd_service(db_path=None):
    return "\n".join([
        "[Unit]",
        "Description=mnemo index backup",
        "",
        "[Service]",
        "Type=oneshot",
        "ExecStart=%s" % " ".join(shlex.quote(a) for a in backup_argv(db_path)),
        "StandardOutput=append:%s" % log_path(),
        "StandardError=append:%s" % log_path(),
        "",
    ])


def systemd_timer(interval_sec):
    return "\n".join([
        "[Unit]",
        "Description=Run mnemo index backup periodically",
        "",
        "[Timer]",
        "OnBootSec=5m",
        "OnUnitActiveSec=%ds" % int(interval_sec),
        "Persistent=true",
        "",
        "[Install]",
        "WantedBy=timers.target",
        "",
    ])


def _systemd_install(interval_sec, db_path=None):
    spath = _service_path()
    tpath = _timer_path()
    os.makedirs(os.path.dirname(spath), exist_ok=True)
    with open(spath, "w", encoding="utf-8") as f:
        f.write(systemd_service(db_path))
    with open(tpath, "w", encoding="utf-8") as f:
        f.write(systemd_timer(interval_sec))
    _run(["systemctl", "--user", "daemon-reload"])
    _run(["systemctl", "--user", "enable", "--now", SYSTEMD_UNIT + ".timer"])


def _systemd_uninstall():
    _run(["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT + ".timer"], check=False)
    _remove(_timer_path())
    _remove(_service_path())
    _run(["systemctl", "--user", "daemon-reload"], check=False)


# ------------------------------------------------------------------------ api

def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def schedule_installed():
    m = manager()
    if m == "launchd":
        return os.path.exists(_plist_path())
    if m == "systemd":
        return os.path.exists(_timer_path())
    return False


def install_schedule(every=None, keep=None, dest=None, db_path=None):
    """Install a periodic OS backup schedule (launchd or systemd timer)."""
    cfg = save_config(every=every, keep=keep, dest=dest)
    interval_sec = parse_interval(cfg["every"])
    os.makedirs(os.path.dirname(log_path()), exist_ok=True)
    m = manager()
    if m == "launchd":
        _launchd_install(interval_sec, db_path=db_path)
    elif m == "systemd":
        _systemd_install(interval_sec, db_path=db_path)
    else:
        raise RemoteError(
            "no supported scheduler found (need macOS launchd or Linux systemd --user); "
            "use `mnemo backup --auto on` or add `mnemo backup --if-due` to cron"
        )
    return m, cfg


def uninstall_schedule():
    """Remove the OS backup schedule."""
    m = manager()
    if m == "launchd":
        _launchd_uninstall()
    elif m == "systemd":
        _systemd_uninstall()
    else:
        raise RemoteError("no supported scheduler found (%s)" % m)
    return m


def status(db_path):
    """Return backup configuration, scheduler state and existing snapshots."""
    cfg = load_config()
    dest = resolve_dest(db_path, cfg["dest"])
    files = up.list_backups(db_path, dest_dir=dest)
    total_bytes = 0
    for p in files:
        try:
            total_bytes += os.path.getsize(p)
        except OSError:
            pass
    latest = files[0] if files else None
    latest_mtime = None
    if latest:
        try:
            latest_mtime = os.path.getmtime(latest)
        except OSError:
            pass
    return {
        "auto": cfg["auto"],
        "every": cfg["every"],
        "keep": cfg["keep"],
        "dest": dest,
        "schedule_installed": schedule_installed(),
        "schedule_manager": manager(),
        "log": log_path(),
        "count": len(files),
        "bytes": total_bytes,
        "latest": latest,
        "latest_mtime": latest_mtime,
    }
