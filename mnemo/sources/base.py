import glob
import json
import os
import contextlib
import shutil
import subprocess
import tempfile

from ..model import Msg, clip, norm_ts


class SourceUnavailable(Exception):
    """A source's store exists but cannot be read right now (permissions, locked,
    unexpected format). Sync skips that source and keeps what it already indexed,
    instead of treating every session as deleted."""


def own_home(home):
    """Whether `home` is this user's own home, where the agents' env vars apply."""
    return os.path.realpath(home) == os.path.realpath(os.path.expanduser("~"))


class Source:
    name = ""
    # Where the agent keeps its logs: a config dir under HOME (CONFIG_DIR), the
    # env var that moves it (CONFIG_ENV), and the log folders inside it (LOG_DIRS).
    # MULTI_ACCOUNT also reads sibling dirs such as ~/.claude-work, one per account.
    CONFIG_DIR = None
    CONFIG_ENV = None
    LOG_DIRS = ()
    LOG_SUFFIX = ".jsonl"
    MULTI_ACCOUNT = False
    # One file per session, so a copy in the archive can stand in for it (archive.py).
    ARCHIVABLE = True
    # A session file that can change without changing size (a database updated in
    # place): its archived copy is current only with the very same mtime.
    EXACT_MTIME = False

    def __init__(self, home=None):
        self.home = os.path.expanduser(home or "~")
        # Config dirs learned elsewhere: the archive remembers every account it has
        # copied from, so a $CLAUDE_CONFIG_DIR one stays indexed without the variable.
        self.extra_dirs = ()

    def config_dirs(self):
        """Every config dir of this agent: the default one, sibling accounts, the env var's."""
        if not self.CONFIG_DIR:
            return []
        default = os.path.join(self.home, self.CONFIG_DIR)
        dirs = [default]
        if self.MULTI_ACCOUNT:
            dirs += sorted(glob.glob(glob.escape(default) + "-*"))
        value = os.environ.get(self.CONFIG_ENV or "", "")
        # Like XDG settings: a developer's env var must not leak into a test or demo home.
        if value and own_home(self.home):
            dirs.append(os.path.abspath(os.path.expanduser(value)))
        dirs += list(self.extra_dirs)
        return [d for d in dirs if os.path.isdir(d)]

    def roots(self):
        """Log folders to scan, once each: a symlinked one (~/.codex-work/sessions ->
        ~/.codex/sessions) is skipped when its target is already listed."""
        seen, out = set(), []
        for config in self.config_dirs():
            for sub in self.LOG_DIRS:
                root = os.path.join(config, sub)
                real = os.path.realpath(root)
                if real not in seen and os.path.isdir(root):
                    seen.add(real)
                    out.append(root)
        return out

    def files(self):
        """Yield session file paths."""
        for root in self.roots():
            for path in self.walk(root):
                yield path

    def walk(self, root):
        """Session files under root, by the paths the index knows them by."""
        for dirpath, dirs, names in os.walk(root):
            dirs.sort()
            for n in sorted(names):
                if n.endswith(self.LOG_SUFFIX):
                    yield os.path.join(dirpath, n)

    def actual(self, path):
        """The file on disk behind a session path (Codex may have compressed it)."""
        return path

    def exists(self, path):
        return os.path.exists(self.actual(path))

    def stat(self, path):
        """os.stat of a session file: its change key (st_mtime, st_size) and identity."""
        return os.stat(self.actual(path))

    def snapshot(self, src, dest):
        """Copy the session file src over dest, an empty 0600 file in the archive."""
        with open(src, "rb") as f, open(dest, "wb") as out:
            shutil.copyfileobj(f, out, 1 << 20)

    def account_of(self, path):
        """The config dir a session file belongs to (~/.claude-work for its projects/...)."""
        # Nothing below a log folder repeats its name; a config dir path may, so
        # the last match wins.
        i = max([path.rfind(os.sep + sub + os.sep) for sub in self.LOG_DIRS] or [-1])
        return path[:i] if i > 0 else None

    def account(self, path):
        """Short account label for results: the config dir's name, e.g. .claude-work."""
        config = self.account_of(path)
        return os.path.basename(config) if config else None

    def records(self):
        """Yield (path, mtime, size) for every session.

        Sources whose store holds many sessions in one file (a SQLite database,
        for example) override this so each session can report its own change
        key; everything else keeps the one-file-per-session default. A file
        reachable through a symlink or hard link is reported once, by the first
        path found (the default config dir comes first).
        """
        seen = set()
        for path in self.files():
            try:
                st = self.stat(path)
            except OSError:
                continue
            key = (st.st_dev, st.st_ino)
            if key in seen:
                continue
            seen.add(key)
            yield path, st.st_mtime, st.st_size

    def parse(self, path, clip_text=True):
        """Return (session_id, cwd, [(lineno, Msg), ...]).

        clip_text=False bypasses the per-message 20k-character cap, reading
        full bodies straight from the session file.
        """
        raise NotImplementedError

    @staticmethod
    def read_jsonl(path):
        with open_jsonl(path) as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield lineno, json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue


@contextlib.contextmanager
def open_jsonl(path):
    # Codex changes a cold rollout's representation without changing its logical path.
    compressed = path.endswith(".jsonl.zst")
    if not compressed and not os.path.exists(path) and os.path.exists(path + ".zst"):
        path += ".zst"
        compressed = True
    if not compressed:
        with open(path, "r", encoding="utf-8", errors="replace") as stream:
            yield stream
        return
    binary = shutil.which("zstd")
    if not binary:
        raise SourceUnavailable("compressed Codex logs require the optional zstd executable")
    # stderr goes to a file so a corrupt stream cannot fill a pipe and deadlock stdout.
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen([binary, "-dc", "--", path], stdout=subprocess.PIPE,
                                   stderr=errors, universal_newlines=True, encoding="utf-8",
                                   errors="replace")
        try:
            yield process.stdout
            if process.wait():
                errors.seek(0)
                raise SourceUnavailable("cannot decompress %s: %s" %
                                        (path, errors.read(2048).decode("utf-8", "replace")))
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.terminate()
            process.wait()


def decode_cwd_dir(name):
    """'-Users-bytedance-foo' -> '/Users/bytedance/foo'."""
    if name.startswith("-"):
        return "/" + name[1:].replace("-", "/")
    return name.replace("-", "/")
