"""A durable copy of every session log mnemo indexes.

Claude Code deletes session logs after `cleanupPeriodDays` (30 by default), and
Codex and Pi logs can be removed by hand. With the archive on (the default),
sync mirrors every session file into `archive/` beside the index and, once the
original is gone, keeps indexing the copy, so search, context and session go on
working. The index still knows the session by its original path; "archived"
just means that path is gone and the copy is what is left.

    archive/<source>/<account>/<path inside the config dir>
    archive/claude/.claude-work/projects/-Users-me-app/<session>.jsonl
    archive/claude/.claude-work/origin.json      {"config_dir": "/Users/me/.claude-work"}

origin.json maps an account folder back to the config dir it mirrors, so an
index rebuilt from scratch (mnemo upgrade, index --rebuild) finds archived
sessions again under their original paths. Copies are written atomically
(temp file, then rename) with mode 0600 in 0700 folders: session logs can
contain credentials.
"""
import hashlib
import json
import os
import threading

ENV = "MNEMO_ARCHIVE"
ORIGIN = "origin.json"
OFF = ("0", "off", "false", "no")
ON = ("1", "on", "true", "yes")


def root_for(db_path):
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "archive")


def enabled():
    """MNEMO_ARCHIVE=on|off for one command, else `mnemo node --archive`, else on."""
    value = os.environ.get(ENV, "").strip().lower()
    if value in OFF:
        return False
    if value in ON:
        return True
    return setting()


def overridden():
    return os.environ.get(ENV, "").strip().lower() in ON + OFF


def setting():
    """This device's saved archive setting (node.json), without creating the file."""
    from . import remote

    try:
        with open(remote._node_path(), encoding="utf-8") as f:
            return json.load(f).get("archive", True) is not False
    except (OSError, ValueError, AttributeError):
        return True


def same_copy(a, b, exact=False):
    """(mtime, size) pairs of a file and its copy: equal size, mtime within a second
    (some filesystems keep coarser timestamps than the one copied from), or the
    very same mtime for a file that can change without changing size (exact)."""
    return a[1] == b[1] and (a[0] == b[0] if exact else abs(a[0] - b[0]) < 1)


class Archive:
    def __init__(self, root):
        self.root = root
        self._accounts = {}
        self._checked_root = False

    # ------------------------------------------------------------ accounts

    def accounts(self, source_name):
        """{account folder: config dir} archived for one source."""
        if source_name not in self._accounts:
            found = {}
            base = os.path.join(self.root, source_name)
            try:
                labels = sorted(os.listdir(base))
            except OSError:
                labels = []
            for label in labels:
                origin = self._origin(source_name, label)
                if origin:
                    found[label] = origin
            self._accounts[source_name] = found
        return self._accounts[source_name]

    def origins(self, source_name):
        return sorted(set(self.accounts(source_name).values()))

    def _origin(self, source_name, label):
        try:
            with open(os.path.join(self.root, source_name, label, ORIGIN), encoding="utf-8") as f:
                origin = json.load(f).get("config_dir")
        except (OSError, ValueError, AttributeError):
            return None
        return origin if isinstance(origin, str) and origin else None

    def label(self, source_name, config_dir, create=False):
        """The account folder for a config dir: its name (.claude-work), or the name
        plus a hash when another config dir of the same name got there first."""
        accounts = self.accounts(source_name)
        for label, origin in accounts.items():
            if origin == config_dir:
                return label
        if not create:
            return None
        name = os.path.basename(config_dir.rstrip(os.sep)) or "root"
        digest = hashlib.sha1(config_dir.encode("utf-8", "surrogateescape")).hexdigest()[:8]
        for label in (name, "%s-%s" % (name, digest)):
            if label in accounts:
                continue
            if self._claim(source_name, label, config_dir) or self._origin(source_name, label) == config_dir:
                accounts[label] = config_dir
                return label
        raise OSError("no archive folder free for %s" % config_dir)

    def _claim(self, source_name, label, config_dir):
        """Create the folder's origin.json, unless another process already did."""
        folder = os.path.join(self.root, source_name, label)
        self._makedirs(folder)
        target = os.path.join(folder, ORIGIN)
        tmp = self._write_tmp(target, json.dumps({"config_dir": config_dir}).encode("utf-8"))
        try:
            os.link(tmp, target)  # atomic, and never replaces another process's claim
            return True
        except FileExistsError:
            return False
        except OSError:  # no hard links on this filesystem
            if os.path.exists(target):
                return False
            os.replace(tmp, target)
            return True
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    # ------------------------------------------------------------ files

    def copy_path(self, source, path, create=False):
        """Where the copy of a session file lives (by the path the index uses)."""
        config = source.account_of(path)
        if not config:
            return None
        label = self.label(source.name, config, create)
        if not label:
            return None
        return os.path.join(self.root, source.name, label, os.path.relpath(path, config))

    def locate(self, source, path):
        """The copy of a session file, if there is one."""
        copy = self.copy_path(source, path)
        return copy if copy and source.exists(copy) else None

    def scan(self, source):
        """{original path: (copy, mtime, size)} for every copy of this source."""
        out = {}
        for label, origin in sorted(self.accounts(source.name).items()):
            top = os.path.join(self.root, source.name, label)
            for sub in source.LOG_DIRS:
                for copy in source.walk(os.path.join(top, sub)):
                    try:
                        st = source.stat(copy)
                    except OSError:
                        continue
                    out.setdefault(os.path.join(origin, os.path.relpath(copy, top)),
                                   (copy, st.st_mtime, st.st_size))
        return out

    def store(self, source, path):
        """Copy one session file into the archive (atomically, mode 0600)."""
        copy = self.copy_path(source, path, create=True)
        if not copy:
            raise OSError("not inside a known config dir")
        actual = source.actual(path)
        target = copy + actual[len(path):]  # Codex: keep the .zst of a compressed log
        st = source.stat(path)
        self._makedirs(os.path.dirname(target))
        tmp = self._tmp_name(target)
        os.close(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        try:
            # Bytes as they are, or a database's own consistent copy (agy).
            source.snapshot(actual, tmp)
            os.chmod(tmp, 0o600)
            # The copy keeps the original's mtime: once the original is gone the
            # copy reports the same change key, so the index has nothing to redo.
            os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
            os.replace(tmp, target)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        # One representation per session: drop the plain copy once it is compressed.
        for other in (copy, copy + ".zst"):
            if other != target and os.path.exists(other):
                os.remove(other)

    def remove(self, copy):
        for path in (copy, copy + ".zst"):
            if os.path.exists(path):
                os.remove(path)

    def sync(self, source, live, log=lambda m: None):
        """Mirror the live session files; return the archived ones to index in their place.

        live is {path: (mtime, size)} from source.records(). Returns
        ({original path: (copy, mtime, size)}, number of files copied). A copy
        stands in for its original only once the original is gone from disk, and
        not while a live file of the same session name exists (Source.session_name;
        Codex moving a rollout to archived_sessions, a session continued under
        another account): that file carries the session on. Of several copies with
        one name, the newest wins.
        """
        have = self.scan(source)
        copied = 0
        mirrored = set()
        for path, key in sorted(live.items()):
            entry = have.get(path)
            if entry is None or not same_copy(entry[1:], key, source.EXACT_MTIME):
                try:
                    self.store(source, path)
                except OSError as exc:
                    log("skip archiving %s: %s" % (path, exc))
                    continue
                copied += 1
            mirrored.add(path)
        by_name = {source.session_name(p): (p, key) for p, key in live.items()}
        chosen = {}
        for orig, (copy, mtime, size) in sorted(have.items()):
            if orig in live or source.exists(orig):
                continue  # still on disk (a symlinked duplicate, say): not ours to keep
            name = source.session_name(orig)
            if name in by_name:
                path, key = by_name[name]
                if path in mirrored and same_copy((mtime, size), key, source.EXACT_MTIME):
                    self.remove(copy)  # the very same file, moved; its new path has a copy
                continue
            best = chosen.get(name)
            if best is None or mtime > best[1][1]:
                chosen[name] = (orig, (copy, mtime, size))
        return dict(chosen.values()), copied

    # ------------------------------------------------------------ disk

    def usage(self):
        """(files, bytes) of session copies in the archive."""
        files = size = 0
        for dirpath, _dirs, names in os.walk(self.root):
            for n in names:
                if n == ORIGIN or n.endswith(".tmp"):
                    continue
                try:
                    size += os.path.getsize(os.path.join(dirpath, n))
                except OSError:
                    continue
                files += 1
        return files, size

    def _makedirs(self, path):
        """os.makedirs, with every folder it creates in the archive private (0700)."""
        if not self._checked_root:
            os.makedirs(os.path.dirname(self.root), exist_ok=True)
            self._mkdir(self.root)
            if os.stat(self.root).st_mode & 0o077:
                os.chmod(self.root, 0o700)
            self._checked_root = True
        rel = os.path.relpath(path, self.root)
        if rel.startswith(os.pardir):
            raise OSError("%s is outside the archive" % path)
        current = self.root
        for part in rel.split(os.sep):
            if part in ("", os.curdir):
                continue
            current = os.path.join(current, part)
            self._mkdir(current)

    @staticmethod
    def _mkdir(path):
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            return
        os.chmod(path, 0o700)  # mkdir's mode is masked by the umask

    @staticmethod
    def _tmp_name(target):
        return os.path.join(os.path.dirname(target), ".%s.%d.%d.tmp" % (
            os.path.basename(target), os.getpid(), threading.get_ident()))

    def _write_tmp(self, target, data):
        tmp = self._tmp_name(target)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o600)
        return tmp
