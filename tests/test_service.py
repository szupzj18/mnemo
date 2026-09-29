"""Link services: launchd/systemd files and commands, and a real background link."""
import contextlib
import io
import json
import os
import plistlib
import subprocess
import sys
import time
import unittest

from helpers import DemoHome
from test_mesh import MNEMO, Device

from mnemo import remote, service
from mnemo.remote import RemoteError


class ServiceFilesTest(unittest.TestCase):
    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.demo = DemoHome()
        self.demo.activate()
        remote.save_remotes([{"name": "devbox-109", "host": "devbox-109"}])
        self.calls = []
        self._saved = (service._run, os.environ.get("MNEMO_SERVICE_MANAGER"))
        service._run = lambda argv, check=True: self.calls.append(argv) or 0

    def tearDown(self):
        service._run, forced = self._saved
        if forced is None:
            os.environ.pop("MNEMO_SERVICE_MANAGER", None)
        else:
            os.environ["MNEMO_SERVICE_MANAGER"] = forced
        self.demo.deactivate()
        self.demo.cleanup()

    def use(self, manager):
        os.environ["MNEMO_SERVICE_MANAGER"] = manager

    def test_launchd_agent_starts_now_and_at_login(self):
        self.use("launchd")
        self.assertEqual(service.install("devbox-109"), "launchd")
        path = os.path.join(self.demo.home, "Library", "LaunchAgents", "dev.mnemo.link.devbox-109.plist")
        with open(path, "rb") as f:
            plist = plistlib.load(f)
        self.assertEqual(plist["ProgramArguments"][-3:], ["link", "devbox-109", "--allow-inbound"])
        self.assertTrue(plist["RunAtLoad"] and plist["KeepAlive"])
        self.assertTrue(plist["StandardOutPath"].endswith("link-devbox-109.log"))
        verbs = [c[1] for c in self.calls]
        # bootstrap alone leaves a mid-session RunAtLoad pending; kickstart starts it.
        self.assertEqual(verbs, ["bootout", "bootstrap", "kickstart"])
        self.assertTrue(service.installed("devbox-109"))
        self.assertEqual(service.status()[0]["installed"], True)

        self.calls.clear()
        service.restart_installed()
        self.assertEqual(self.calls, [["launchctl", "kickstart", "-k", "gui/%d/dev.mnemo.link.devbox-109" % os.getuid()]])

        self.calls.clear()
        service.uninstall("devbox-109")
        self.assertEqual([c[1] for c in self.calls], ["bootout"])
        self.assertFalse(os.path.exists(path))
        self.assertEqual(service.status()[0]["state"], "off")

    def test_systemd_unit_restarts_and_is_enabled(self):
        self.use("systemd")
        service.install("devbox-109")
        path = os.path.join(self.demo.home, ".config", "systemd", "user", "mnemo-link-devbox-109.service")
        with open(path) as f:
            unit = f.read()
        self.assertIn("Restart=always", unit)
        self.assertIn("link devbox-109 --allow-inbound", unit)
        self.assertIn("WantedBy=default.target", unit)
        self.assertEqual(self.calls, [["systemctl", "--user", "daemon-reload"],
                                      ["systemctl", "--user", "enable", "--now", "mnemo-link-devbox-109.service"]])
        self.calls.clear()
        service.uninstall("devbox-109")
        self.assertEqual(self.calls[0], ["systemctl", "--user", "disable", "--now", "mnemo-link-devbox-109.service"])
        self.assertFalse(os.path.exists(path))

    def test_refuses_unknown_remotes_odd_names_and_managers(self):
        self.use("launchd")
        with self.assertRaises(RemoteError):
            service.install("nope")
        remote.save_remotes([{"name": "a b", "host": "x"}])
        with self.assertRaises(RemoteError):
            service.install("a b")
        self.use("upstart")
        with self.assertRaises(RemoteError):
            service.install("a b")
        self.assertEqual(self.calls, [])


class BackgroundLinkTest(unittest.TestCase):
    """The fallback manager, end to end: A installs a link to B; B searches A."""

    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.demo = DemoHome()
        self.a, self.b = Device("A", self.demo.root), Device("B", self.demo.root)
        for dev in (self.a, self.b):
            dev.node(forward=False)
        self.a.link(self.b)
        self.env = dict(os.environ, HOME=self.a.home, MNEMO_SERVICE_MANAGER="background")

    def tearDown(self):
        self.mnemo(self.a, "link", "B", "--uninstall", check=False)
        self.demo.cleanup()

    def mnemo(self, dev, *argv, check=True):
        env = self.env if dev is self.a else dict(os.environ, HOME=dev.home)
        p = subprocess.run(MNEMO + list(argv), env=env, capture_output=True, text=True, timeout=120)
        if check and p.returncode:
            self.fail("mnemo %s failed: %s" % (" ".join(argv), p.stderr))
        return p

    def links(self):
        return {x["remote"]: x for x in json.loads(self.mnemo(self.a, "link", "--list", "--json").stdout)}

    def wait_for(self, state, pid_not=None):
        deadline = time.time() + 30
        while time.time() < deadline:
            item = self.links()["B"]
            if item["state"] == state and item.get("pid") != pid_not:
                return item
            time.sleep(0.3)
        self.fail("link never reached %s: %s" % (state, self.links()))

    def test_install_list_upgrade_and_uninstall(self):
        self.assertEqual(self.links()["B"]["state"], "off")
        self.assertIn("--allow-inbound", self.mnemo(self.a, "link", "B", "--install", check=False).stderr,
                      "installing still needs consent")
        out = self.mnemo(self.a, "link", "B", "--allow-inbound", "--install").stdout
        self.assertIn("via background", out)
        first = self.wait_for("connected")
        self.assertTrue(first["installed"])
        hits = json.loads(self.mnemo(self.b, "search", "wordA", "--json").stdout)
        self.assertEqual({h["host"] for h in hits}, {"A"})

        # An upgrade restarts the link so it runs the new code.
        out = self.mnemo(self.a, "upgrade", "--no-backup", "--no-remotes").stdout
        self.assertIn("links: restarted link to B", out)
        self.wait_for("connected", pid_not=first["pid"])

        self.mnemo(self.a, "link", "B", "--uninstall")
        deadline = time.time() + 15
        while self.links()["B"]["state"] != "off":
            self.assertLess(time.time(), deadline)
            time.sleep(0.3)
        out = self.mnemo(self.b, "session", hits[0]["path"], "--host", "A", check=False)
        self.assertIn("not linked right now", out.stderr)


if __name__ == "__main__":
    unittest.main()
