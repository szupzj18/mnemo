"""Keep `mnemo link` running as a per-user background service.

launchd on macOS, a systemd user unit on Linux, and elsewhere (or with
MNEMO_SERVICE_MANAGER=background) a detached process that does not survive a
reboot. Each installed link is one service, named after the remote.
"""
import os
import plistlib
import re
import shlex
import shutil
import signal
import subprocess
import sys

from . import link
from . import remote as remote_mod
from .remote import RemoteError

LAUNCHD_PREFIX = "dev.mnemo.link."
SYSTEMD_PREFIX = "mnemo-link-"


def manager():
    forced = os.environ.get("MNEMO_SERVICE_MANAGER")
    if forced:
        return forced
    if sys.platform == "darwin":
        return "launchd"
    if shutil.which("systemctl") and _run(["systemctl", "--user", "show-environment"], check=False) == 0:
        return "systemd"
    return "background"


def _run(argv, check=True):
    """Run a service-manager command; returns its exit code (tests replace this)."""
    p = subprocess.run(argv, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RemoteError("%s failed: %s" % (" ".join(argv[:3]), (p.stderr or p.stdout).strip()[:200]))
    return p.returncode


def _check_name(name):
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*$", name or ""):
        raise RemoteError("cannot run a link service for remote %r: use letters, digits, '.', '_' or '-'" % name)
    return name


def link_argv(name):
    return link.self_argv() + ["link", name, "--allow-inbound"]


def log_path(name):
    return os.path.join(remote_mod.CONFIG_DIR, "logs", "link-%s.log" % name)


def _home(*parts):
    return os.path.join(os.path.expanduser("~"), *parts)


# -------------------------------------------------------------------- launchd

def _label(name):
    return LAUNCHD_PREFIX + name


def _plist_path(name):
    return _home("Library", "LaunchAgents", _label(name) + ".plist")


def _domain():
    return "gui/%d" % os.getuid()


def launchd_plist(name):
    return {
        "Label": _label(name),
        "ProgramArguments": link_argv(name),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 60,
        "StandardOutPath": log_path(name),
        "StandardErrorPath": log_path(name),
    }


def _launchd_install(name):
    path = _plist_path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        plistlib.dump(launchd_plist(name), f)
    _run(["launchctl", "bootout", "%s/%s" % (_domain(), _label(name))], check=False)
    _run(["launchctl", "bootstrap", _domain(), path])
    # Loaded mid-session, launchd defers RunAtLoad ("speculative" spawn); start it now.
    _run(["launchctl", "kickstart", "%s/%s" % (_domain(), _label(name))])


def _launchd_uninstall(name):
    _run(["launchctl", "bootout", "%s/%s" % (_domain(), _label(name))], check=False)
    _remove(_plist_path(name))


def _launchd_restart(name):
    _run(["launchctl", "kickstart", "-k", "%s/%s" % (_domain(), _label(name))])


# -------------------------------------------------------------------- systemd

def _unit(name):
    return SYSTEMD_PREFIX + name + ".service"


def _unit_path(name):
    return _home(".config", "systemd", "user", _unit(name))


def systemd_unit(name):
    return "\n".join([
        "[Unit]",
        "Description=mnemo link: let %s search this device" % name,
        "After=network-online.target",
        "",
        "[Service]",
        "ExecStart=%s" % " ".join(shlex.quote(a) for a in link_argv(name)),
        "Restart=always",
        "RestartSec=60",
        "StandardOutput=append:%s" % log_path(name),
        "StandardError=append:%s" % log_path(name),
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ])


def _systemd_install(name):
    path = _unit_path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(systemd_unit(name))
    _run(["systemctl", "--user", "daemon-reload"])
    _run(["systemctl", "--user", "enable", "--now", _unit(name)])


def _systemd_uninstall(name):
    _run(["systemctl", "--user", "disable", "--now", _unit(name)], check=False)
    _remove(_unit_path(name))
    _run(["systemctl", "--user", "daemon-reload"], check=False)


def _systemd_restart(name):
    _run(["systemctl", "--user", "restart", _unit(name)])


# ----------------------------------------------------------------- background

def _pid_path(name):
    return os.path.join(remote_mod.CONFIG_DIR, "links", "out-%s.pid" % name)


def _background_install(name):
    _background_uninstall(name)
    with open(log_path(name), "a") as log:
        p = subprocess.Popen(link_argv(name), stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                             start_new_session=True)
    os.makedirs(os.path.dirname(_pid_path(name)), exist_ok=True)
    with open(_pid_path(name), "w") as f:
        f.write(str(p.pid))


def _background_pid(name):
    try:
        with open(_pid_path(name)) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _background_uninstall(name):
    pid = _background_pid(name)
    if pid and link._alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    _remove(_pid_path(name))


# ---------------------------------------------------------------------- api

_BACKENDS = {
    "launchd": (_launchd_install, _launchd_uninstall, _launchd_restart, _plist_path),
    "systemd": (_systemd_install, _systemd_uninstall, _systemd_restart, _unit_path),
    "background": (_background_install, _background_uninstall, _background_install, _pid_path),
}


def _backend():
    m = manager()
    if m not in _BACKENDS:
        raise RemoteError("unknown MNEMO_SERVICE_MANAGER %r (launchd, systemd or background)" % m)
    return m, _BACKENDS[m]


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def installed(name):
    return os.path.exists(_backend()[1][3](name))


def install(name):
    """Start linking to `name` now and on every login. Returns the manager used."""
    remote_mod.get_remote(_check_name(name))
    os.makedirs(os.path.dirname(log_path(name)), exist_ok=True)
    m, (do_install, _, _, _) = _backend()
    do_install(name)
    return m


def uninstall(name):
    m, (_, do_uninstall, _, _) = _backend()
    do_uninstall(_check_name(name))
    link.write_state(name, "stopped")
    return m


def status():
    """Every registered remote this device could link to, with its link service and state."""
    out = []
    for r in remote_mod.load_remotes():
        if r.get("transport") == "link":
            continue  # it links in to us; nothing for this device to run
        try:
            inst = installed(r["name"])
        except RemoteError:
            inst = False
        st = link.read_state(r["name"])
        out.append({"remote": r["name"], "installed": inst, "state": st["state"] if inst or st["state"] != "stopped"
                    else "off", "since": st.get("since"), "error": st.get("error"), "pid": st.get("pid")})
    return out


def restart_installed(log=lambda m: None):
    """After an upgrade: restart link services so they run the new code."""
    m, (_, _, do_restart, _) = _backend()
    for item in status():
        if item["installed"]:
            try:
                do_restart(item["remote"])
                log("restarted link to %s" % item["remote"])
            except RemoteError as exc:
                log("could not restart link to %s: %s" % (item["remote"], exc))
