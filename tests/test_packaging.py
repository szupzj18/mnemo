"""pip/uv installs (the mnemo-search package) must behave like a checkout."""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from helpers import REPO

from mnemo import fingerprint, remote


def package_data():
    with open(os.path.join(REPO, "pyproject.toml"), encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"^\[tool\.setuptools\.package-data\].*?^mnemo = \[(.*?)\]", text, re.M | re.S)
    return re.findall(r'"([^"]+)"', m.group(1))


class PackagingTest(unittest.TestCase):
    def test_package_data_ships_every_fingerprinted_file(self):
        # A file missing from the wheel changes the fingerprint, and pip-installed
        # devices would then look outdated to checkouts forever (and vice versa).
        shipped = set()
        for pattern in package_data():
            shipped.update(glob.glob(os.path.join(fingerprint.PACKAGE, pattern), recursive=True))
        missing = [os.path.relpath(p, fingerprint.PACKAGE) for p in fingerprint._files()
                   if os.path.exists(p) and not p.endswith(".py") and p not in shipped]
        self.assertEqual(missing, [])

    def test_launcher_matches_the_checkout(self):
        with open(os.path.join(REPO, "bin", "mnemo"), encoding="utf-8") as f:
            self.assertEqual(f.read(), remote.LAUNCHER)

    def test_a_pip_install_ships_only_itself_to_remotes(self):
        site = tempfile.mkdtemp(prefix="mnemo-site-")
        try:
            shutil.copytree(fingerprint.PACKAGE, os.path.join(site, "mnemo"),
                            ignore=shutil.ignore_patterns("__pycache__"))
            open(os.path.join(site, "some_other_package.py"), "w").close()  # site-packages neighbors
            home = os.path.join(site, "B")
            os.makedirs(home)
            script = (
                "import json, os, sys\n"
                "from mnemo import fingerprint, remote\n"
                "with remote._source_tree() as t:\n"
                "    tree = sorted(os.listdir(t))\n"
                "remote.install({'name': 'B', 'home': sys.argv[1], 'transport': 'local'}, build_index=False)\n"
                "print(json.dumps({'tree': tree, 'code': fingerprint.code_fingerprint()}))\n"
            )
            env = dict(os.environ, PYTHONPATH=site)
            out = json.loads(subprocess.run([sys.executable, "-c", script, home], env=env, cwd=site,
                                            capture_output=True, text=True, check=True).stdout)
            self.assertEqual(out["tree"], ["bin", "mnemo"])
            self.assertEqual(sorted(os.listdir(os.path.join(home, "mnemo"))), ["bin", "mnemo"])
            self.assertEqual(out["code"], fingerprint.code_fingerprint(), "same code, same fingerprint")
            p = subprocess.run([sys.executable, os.path.join(home, "mnemo", "bin", "mnemo"), "node", "--json"],
                               env=dict(os.environ, HOME=home), capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(p.stdout)["code"], out["code"])
        finally:
            shutil.rmtree(site, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
