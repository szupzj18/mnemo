import json
import os

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
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield lineno, json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue


def decode_cwd_dir(name):
    """'-Users-bytedance-foo' -> '/Users/bytedance/foo'."""
    if name.startswith("-"):
        return "/" + name[1:].replace("-", "/")
    return name.replace("-", "/")
