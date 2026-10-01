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


class Source:
    name = ""

    def __init__(self, home=None):
        self.home = os.path.expanduser(home or "~")

    def files(self):
        """Yield session file paths."""
        raise NotImplementedError

    def records(self):
        """Yield (path, mtime, size) for every session.

        Sources whose store holds many sessions in one file (a SQLite database,
        for example) override this so each session can report its own change
        key; everything else keeps the one-file-per-session default.
        """
        for path in self.files():
            try:
                st = os.stat(path)
            except OSError:
                continue
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
