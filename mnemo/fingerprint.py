"""A short hash of the code this mnemo runs, so devices can tell who is behind.

__version__ is not bumped per change and remotes get code by rsync without
.git, so the version number cannot say whether two devices run the same code.
The hash covers what a device executes: the Python package, the agent
integrations and the dashboard build (via its source hash). Not the launcher:
pip/uv installs have none, and it only puts the package on sys.path.
"""
import functools
import hashlib
import os

PACKAGE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(PACKAGE)


def _files():
    yield os.path.join(PACKAGE, "web_dist", ".source-hash")
    integrations = os.path.join(PACKAGE, "integrations")
    for dirpath, dirnames, filenames in os.walk(PACKAGE):
        dirnames[:] = sorted(d for d in dirnames if d not in ("__pycache__", "web_dist"))
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            if name.endswith(".py") or path.startswith(integrations + os.sep):
                yield path


@functools.lru_cache(maxsize=1)
def code_fingerprint():
    """The code this process loaded (computed once, at first use)."""
    return compute()


def compute():
    """The code on disk right now; differs from code_fingerprint() after an update."""
    h = hashlib.sha256()
    for path in _files():
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            continue
        h.update(os.path.relpath(path, ROOT).replace(os.sep, "/").encode("utf-8") + b"\0")
        h.update(hashlib.sha256(data).digest())
    return h.hexdigest()[:12]
