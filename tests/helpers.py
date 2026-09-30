import os
import runpy
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)


# A developer's own XDG settings must not reach the throwaway HOMEs tests use
# (here or in the mnemo subprocesses they start); tests of XDG set them explicitly.
for _var in ("XDG_DATA_HOME", "XDG_CONFIG_HOME"):
    os.environ.pop(_var, None)


class DemoHome:
    """A throwaway HOME filled by scripts/make-demo-home.py (5 sessions, 45 messages)."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="mnemo-test-")
        self.home = os.path.join(self.root, "home")
        argv = sys.argv
        try:
            sys.argv = ["make-demo-home.py", self.home]
            runpy.run_path(os.path.join(REPO, "scripts", "make-demo-home.py"), run_name="__main__")
        finally:
            sys.argv = argv
        self.db = os.path.join(self.root, "index.db")

    def path(self, *parts):
        return os.path.join(self.home, *parts)

    def activate(self):
        """Point HOME and the remote registry at the demo so nothing touches the
        real ~/.claude, ~/.codex, ~/.pi or SSHes to registered devices."""
        from mnemo import remote

        self._saved = (os.environ.get("HOME"), remote.CONFIG_DIR, remote.CONFIG_PATH)
        os.environ["HOME"] = self.home
        remote.CONFIG_DIR = os.path.join(self.root, "mnemo-config")
        remote.CONFIG_PATH = os.path.join(remote.CONFIG_DIR, "remotes.json")

    def deactivate(self):
        from mnemo import remote

        home, remote.CONFIG_DIR, remote.CONFIG_PATH = self._saved
        if home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = home

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)
