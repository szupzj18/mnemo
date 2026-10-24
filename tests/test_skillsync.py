"""Skill sync: the manifest, links made or taken over and recorded in the lock, conflicts left alone,
rollback to the previous lock, scoped sources kept off other devices, and the push to a device
that cannot get a source by git."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from helpers import REPO

from mnemo import cli, link, remote, skills, skillsync

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main"]


def git(*args):
    subprocess.run(GIT + list(args), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def skill(repo, name, body="body"):
    write(os.path.join(repo, "skills", name, "SKILL.md"), "---\nname: %s\ndescription: %s\n---\n%s\n" % (name, name, body))


def commit(repo, message="c"):
    git("-C", repo, "add", "-A")
    git("-C", repo, "commit", "-q", "-m", message)


def snapshot(*roots):
    """Every path under roots with what it is: a link's target or a file's content."""
    out = {}
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            for n in dirnames + filenames:
                p = os.path.join(dirpath, n)
                if os.path.islink(p):
                    out[p] = "-> " + os.readlink(p)
                elif os.path.isfile(p):
                    with open(p, "rb") as f:
                        out[p] = f.read()
                else:
                    out[p] = "dir"
            dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
    return out


MANIFEST = """
[sources.mine]
git = "%(origin)s"
checkout = "~/dev/mine"

[sources.work]
git = "%(work)s"
checkout = "~/dev/work"
scope = "internal"

[devices]
internal = ["local"]

[[install]]
from = "mine"
skills = ["*"]
exclude = ["draft-*"]
roots = ["claude", "agents"]

[[install]]
from = "work"
skills = ["*"]
"""


class Fixture(unittest.TestCase):
    """A HOME with a personal source (cloned at ~/dev/mine) and an internal one, and skill folders
    that already hold things sync must not touch."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mnemo-skillsync-")
        self.home = os.path.join(self.root, "home")
        self.saved = (os.environ.get("HOME"), remote.CONFIG_DIR, remote.CONFIG_PATH)
        os.environ["HOME"] = self.home
        self.state = os.path.join(self.home, ".mnemo")
        # The CLI reads the manifest, lock and registered devices from here: never the real ~/.mnemo.
        remote.CONFIG_DIR, remote.CONFIG_PATH = self.state, os.path.join(self.state, "remotes.json")
        self.origin = os.path.join(self.root, "origin.git")
        self.upstream = os.path.join(self.root, "upstream")
        for name in ("read-pr", "say-it", "taken", "draft-x"):
            skill(self.upstream, name)
        git("init", "-q", self.upstream)
        commit(self.upstream)
        git("clone", "-q", "--bare", self.upstream, self.origin)
        git("-C", self.upstream, "remote", "add", "origin", self.origin)
        self.work = os.path.join(self.root, "work.git")
        work_src = os.path.join(self.root, "work-src")
        skill(work_src, "secret-tool")
        git("init", "-q", work_src)
        commit(work_src)
        git("clone", "-q", "--bare", work_src, self.work)
        self.checkout = os.path.join(self.home, "dev", "mine")
        git("clone", "-q", self.origin, self.checkout)
        self.claude = os.path.join(self.home, ".claude", "skills")
        self.agents = os.path.join(self.home, ".agents", "skills")
        os.makedirs(os.path.join(self.claude, "synced", "org_acct", "pdf"))
        write(os.path.join(self.claude, "synced", "org_acct", "pdf", "SKILL.md"), "synced by Claude Code")
        os.makedirs(os.path.join(self.home, ".codex", "skills", ".system", "imagegen"))
        # By hand, before mnemo: read-pr linked to the checkout (taken over), taken is someone else's folder.
        os.symlink(os.path.join(self.checkout, "skills", "read-pr"), os.path.join(self.claude, "read-pr"))
        os.makedirs(os.path.join(self.claude, "taken"))
        write(os.path.join(self.claude, "taken", "SKILL.md"), "a different taken")
        os.symlink("/elsewhere/other", os.path.join(self.claude, "other"))
        self.manifest_path = os.path.join(self.state, "skills.toml")
        write(self.manifest_path, MANIFEST % {"origin": self.origin, "work": self.work})
        self.manifest = skillsync.load_manifest(self.manifest_path)

    def tearDown(self):
        os.environ["HOME"], remote.CONFIG_DIR, remote.CONFIG_PATH = self.saved
        shutil.rmtree(self.root, ignore_errors=True)
        skills._TRACKED.clear()

    def sync(self, dry_run=False, device="local"):
        out = skillsync.sync_device(device, self.manifest, dry_run, state_dir=self.state)
        self.assertNotIn("error", out)
        return out

    def lock(self):
        with open(os.path.join(self.state, skillsync.LOCK)) as f:
            return json.load(f)

    def untouched(self):
        return snapshot(os.path.join(self.claude, "synced"), os.path.join(self.claude, "taken"),
                        os.path.join(self.home, ".codex"))


def steps(out):
    return [(a["action"], a["link"], a.get("target")) for a in out["actions"]]


class ManifestTest(unittest.TestCase):
    def test_toml_subset(self):
        data = skillsync.parse_toml('# c\n[sources.a]\ngit = "u#1" # x\npath = \'s\'\n[devices]\n'
                                    'internal = ["local",\n  "box",  # one\n]\n[[install]]\nfrom = "a"\n'
                                    '[[install]]\nfrom = "a"\nskills = ["x"]\n')
        self.assertEqual(data, {"sources": {"a": {"git": "u#1", "path": "s"}}, "devices": {"internal": ["local", "box"]},
                                "install": [{"from": "a"}, {"from": "a", "skills": ["x"]}]})
        for bad in ('a = 1.5', 'a = "x', '[t\n', 'a = 1\na = 2', 'a = [1, 2'):
            with self.assertRaises(skillsync.SyncError, msg=bad):
                skillsync.parse_toml(bad)

    def test_checks(self):
        ok = {"sources": {"a": {"git": "u"}}, "install": [{"from": "a"}]}
        m = skillsync.manifest_from(ok)
        self.assertEqual(m["sources"]["a"]["checkout"], "~/.mnemo/skill-sources/a")
        self.assertEqual(m["install"][0]["roots"], ["agents", "claude"])
        for bad in ({"sources": {"a": {"git": "u", "scope": "internal"}}},
                    {"sources": {"a": {"git": "u"}}, "install": [{"from": "b"}]},
                    {"sources": {"a": {"git": "u"}}, "install": [{"from": "a", "roots": ["codex"]}]},
                    {"sources": {"a": {"git": "u", "path": "../x"}}},
                    {"sources": {"a": {"gti": "u"}}}):
            with self.assertRaises(skillsync.SyncError, msg=bad):
                skillsync.manifest_from(bad)

    def test_url_forms_match(self):
        forms = ["https://github.com/u/r.git", "git@github.com:u/r", "ssh://git@github.com/u/r.git", "github.com/u/r/"]
        self.assertEqual({skillsync.norm_url(u) for u in forms}, {"github.com/u/r"})


class SyncTest(Fixture):
    def test_dry_run_changes_nothing_and_matches_the_sync(self):
        home = lambda: snapshot(*(os.path.join(self.home, d) for d in ("dev", ".claude", ".agents", ".codex", ".mnemo")))
        before = home()
        cwd = os.getcwd()
        os.chdir(self.root)  # not a repository: git must be asked about the checkout, not the working directory
        try:
            dry = self.sync(dry_run=True)
        finally:
            os.chdir(cwd)
        self.assertEqual(home(), before, "a dry run writes nothing")
        self.assertEqual({s["source"]: s["state"] for s in dry["sources"]}, {"mine": "pull", "work": "clone"})
        self.assertFalse(os.path.exists(os.path.join(self.state, skillsync.LOCK)))
        done = self.sync()
        self.assertEqual(steps(done), steps(dry), "the sync does what the dry run said")
        acts = {(a["root"], a["name"]): a["action"] for a in done["actions"] if a.get("name")}
        self.assertEqual(acts, {("claude", "read-pr"): "adopt", ("claude", "say-it"): "link",
                                ("claude", "taken"): "conflict", ("agents", "read-pr"): "link",
                                ("agents", "say-it"): "link", ("agents", "taken"): "link",
                                ("agents", "secret-tool"): "link", ("claude", "secret-tool"): "link"})
        self.assertIn(("mkdir", self.agents, None), steps(done))

    def test_links_lock_and_what_stays_untouched(self):
        untouched = self.untouched()
        adopted = os.lstat(os.path.join(self.claude, "read-pr"))
        self.sync()
        self.assertEqual(self.untouched(), untouched, "synced/, .system/ and the conflicting folder are left alone")
        self.assertEqual(os.readlink(os.path.join(self.claude, "other")), "/elsewhere/other")
        self.assertEqual(os.lstat(os.path.join(self.claude, "read-pr")).st_ino, adopted.st_ino, "taken over, not rebuilt")
        self.assertEqual(os.readlink(os.path.join(self.agents, "say-it")), os.path.join(self.checkout, "skills", "say-it"))
        self.assertFalse(os.path.lexists(os.path.join(self.agents, "draft-x")), "excluded")
        lock = self.lock()
        by = {(e["root"], e["name"]): e for e in lock["skills"]}
        self.assertEqual(by[("claude", "read-pr")]["origin"], "adopted")
        self.assertEqual(by[("agents", "say-it")]["origin"], "linked")
        self.assertNotIn(("claude", "taken"), by)
        head = subprocess.run(["git", "-C", self.checkout, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        e = by[("agents", "say-it")]
        self.assertEqual((e["commit"], e["content_hash"]), (head, skills.tree_hash(e["link"])))
        self.assertEqual([f["path"] for f in e["files"]], ["SKILL.md"])
        self.assertEqual(lock["sources"]["mine"]["commit"], head)
        again = self.sync()
        self.assertEqual({a["action"] for a in again["actions"]}, {"keep", "conflict"})
        self.assertFalse(again["written"], "nothing changed, so no new lock and no history entry")
        self.assertEqual(len(os.listdir(os.path.join(self.state, skillsync.HISTORY))), 1)

    def test_a_link_to_elsewhere_is_a_conflict_and_stays(self):
        os.makedirs(self.agents)
        os.symlink("/somewhere/say-it", os.path.join(self.agents, "say-it"))
        dry = self.sync(dry_run=True)
        conflict = [a for a in dry["actions"] if a["action"] == "conflict"]
        self.assertIn((self.agents + "/say-it", "link -> /somewhere/say-it"),
                      [(a["link"], a["detail"]) for a in conflict])
        self.sync()
        self.assertEqual(os.readlink(os.path.join(self.agents, "say-it")), "/somewhere/say-it")
        self.assertNotIn(os.path.join(self.agents, "say-it"), [e["link"] for e in self.lock()["skills"]])

    def test_relative_and_chained_links_to_the_same_folder_are_taken_over(self):
        os.makedirs(self.agents)
        os.symlink("../../dev/mine/skills/say-it", os.path.join(self.agents, "say-it"))
        os.symlink("../../.agents/skills/say-it", os.path.join(self.claude, "say-it"))
        acts = {a["link"]: a["action"] for a in self.sync()["actions"]}
        self.assertEqual(acts[os.path.join(self.agents, "say-it")], "adopt")
        self.assertEqual(acts[os.path.join(self.claude, "say-it")], "adopt")
        self.assertEqual(os.readlink(os.path.join(self.claude, "say-it")), "../../.agents/skills/say-it")

    def test_unwanted_links_sync_made_go_and_hand_changes_are_forgotten(self):
        self.sync()
        write(self.manifest_path, (MANIFEST % {"origin": self.origin, "work": self.work})
              .replace('exclude = ["draft-*"]', 'exclude = ["draft-*", "say-it", "read-pr"]'))
        self.manifest = skillsync.load_manifest(self.manifest_path)
        os.unlink(os.path.join(self.agents, "taken"))
        os.symlink("/mine/now", os.path.join(self.agents, "taken"))
        acts = {}
        for a in self.sync()["actions"]:
            acts.setdefault(a["link"], []).append(a["action"])
        self.assertEqual(acts[os.path.join(self.agents, "say-it")], ["unlink"])
        self.assertFalse(os.path.lexists(os.path.join(self.agents, "say-it")))
        self.assertEqual(acts[os.path.join(self.claude, "read-pr")], ["forget"], "it was there before sync")
        self.assertTrue(os.path.islink(os.path.join(self.claude, "read-pr")))
        self.assertEqual(acts[os.path.join(self.agents, "taken")], ["conflict", "forget"],
                         "replaced by hand: left alone and dropped from the lock")
        self.assertNotIn(os.path.join(self.agents, "taken"), [e["link"] for e in self.lock()["skills"]])
        self.assertEqual(os.readlink(os.path.join(self.agents, "taken")), "/mine/now")

    def test_first_entry_wins_a_name_two_sources_have(self):
        work_src = os.path.join(self.root, "work-src")
        skill(work_src, "say-it", "work version")
        commit(work_src)
        git("-C", work_src, "push", "-q", self.work, "HEAD:main")
        out = self.sync()
        targets = {a["link"]: a.get("source") for a in out["actions"]}
        self.assertEqual(targets[os.path.join(self.agents, "say-it")], "mine")
        self.assertTrue(any("work's is not linked" in n for n in out["notes"]))

    def test_never_touches_hidden_or_managed_names(self):
        skill(self.upstream, "synced", "would shadow Claude Code's folder")
        commit(self.upstream)
        git("-C", self.upstream, "push", "-q", "origin", "HEAD:main")
        before = self.untouched()
        out = self.sync()
        self.assertIn(("skip", os.path.join(self.claude, "synced")), [(a["action"], a["link"]) for a in out["actions"]])
        self.assertEqual(self.untouched(), before)


class RollbackTest(Fixture):
    def test_rollback_restores_the_previous_lock_link_by_link(self):
        self.sync()
        first = self.lock()
        skill(self.upstream, "new-one")
        skill(self.upstream, "say-it", "changed upstream")
        commit(self.upstream)
        git("-C", self.upstream, "push", "-q", "origin", "HEAD:main")
        second = self.sync()
        self.assertIn(("link", os.path.join(self.agents, "new-one")), [(a["action"], a["link"]) for a in second["actions"]])
        self.assertNotEqual(self.lock()["sources"]["mine"]["commit"], first["sources"]["mine"]["commit"])

        dry = skillsync.rollback_device("local", self.manifest, True, state_dir=self.state)
        self.assertEqual([c["action"] for c in dry["content"]], ["reset"])
        self.assertTrue(os.path.lexists(os.path.join(self.agents, "new-one")), "a dry rollback changes nothing")

        out = skillsync.rollback_device("local", self.manifest, False, state_dir=self.state)
        self.assertEqual(out["mismatches"], [])
        self.assertFalse(os.path.lexists(os.path.join(self.agents, "new-one")))
        self.assertEqual(self.lock(), first)
        for e in first["skills"]:
            self.assertEqual((os.readlink(e["link"]), skills.tree_hash(e["link"])), (e["target"], e["content_hash"]))

        # Back past the first sync: links it made go, the one it took over stays.
        skillsync.rollback_device("local", self.manifest, False, state_dir=self.state)
        self.assertFalse(os.path.lexists(os.path.join(self.agents, "say-it")))
        self.assertTrue(os.path.islink(os.path.join(self.claude, "read-pr")))
        self.assertEqual(self.lock()["skills"], [])
        again = skillsync.rollback_device("local", self.manifest, False, state_dir=self.state)
        self.assertIn("no earlier lock", again["error"])

    def test_rollback_leaves_a_checkout_that_moved_on(self):
        self.sync()
        skill(self.upstream, "say-it", "v2")
        commit(self.upstream)
        git("-C", self.upstream, "push", "-q", "origin", "HEAD:main")
        self.sync()
        write(os.path.join(self.checkout, "skills", "say-it", "notes.md"), "local work in progress")
        out = skillsync.rollback_device("local", self.manifest, False, state_dir=self.state)
        self.assertEqual(out["content"][0]["action"], "conflict")
        self.assertTrue(os.path.exists(os.path.join(self.checkout, "skills", "say-it", "notes.md")))
        self.assertTrue(out["mismatches"], "says which links do not match the earlier lock")


class DeviceTest(Fixture):
    """A second HOME reached through the local transport, as a devbox is over SSH."""

    def setUp(self):
        super().setUp()
        self.box_home = os.path.join(self.root, "box")
        os.makedirs(self.box_home)
        self.box = {"name": "box", "host": "local:box", "transport": "local", "home": self.box_home}

    def on_box(self, *parts):
        return os.path.join(self.box_home, *parts)

    def test_internal_source_never_reaches_a_device_off_its_list(self):
        sent = []
        real = remote.remote_python
        remote.remote_python = lambda r, prog, timeout=120: sent.append(prog) or real(r, prog, timeout)
        try:
            out = skillsync.sync_device("box", self.manifest, False, remote=self.box)
        finally:
            remote.remote_python = real
        self.assertNotIn("error", out)
        self.assertTrue(sent)
        for prog in sent:
            self.assertNotIn(self.work, prog)
            self.assertNotIn("secret-tool", prog)
        self.assertEqual({s["source"] for s in out["sources"]}, {"mine"})
        self.assertFalse(os.path.lexists(self.on_box(".claude", "skills", "secret-tool")))
        self.assertFalse(os.path.lexists(self.on_box("dev", "work")))
        self.assertEqual(skillsync.for_device(self.manifest, "box")["sources"].keys(), {"mine"})
        self.assertEqual(set(skillsync.for_device(self.manifest, "local")["sources"]), {"mine", "work"})

    def test_device_clones_when_git_works(self):
        out = skillsync.sync_device("box", self.manifest, False, remote=self.box)
        self.assertEqual([s["state"] for s in out["prepared"]], ["cloned"])
        self.assertEqual(out["pushed"], [])
        self.assertTrue(os.path.isdir(self.on_box("dev", "mine", ".git")))
        self.assertEqual(os.readlink(self.on_box(".agents", "skills", "say-it")), self.on_box("dev", "mine", "skills", "say-it"))
        with open(self.on_box(".mnemo", skillsync.LOCK)) as f:
            self.assertEqual(len(json.load(f)["skills"]), 6)

    def test_push_when_git_fails_and_a_copy_without_history_is_taken_over(self):
        broken = MANIFEST.replace('git = "%(origin)s"', 'git = "%(origin)s-unreachable"')
        write(self.manifest_path, broken % {"origin": self.origin, "work": self.work})
        self.manifest = skillsync.load_manifest(self.manifest_path)
        dry = skillsync.sync_device("box", self.manifest, True, remote=self.box)
        self.assertEqual([s["state"] for s in dry["sources"]], ["push"])
        self.assertIn("git clone fails", dry["sources"][0]["note"])
        self.assertEqual(sorted(a["name"] for a in dry["actions"] if a["action"] == "link"),
                         ["read-pr", "read-pr", "say-it", "say-it", "taken", "taken"])
        self.assertFalse(os.path.exists(self.on_box("dev")), "a dry run pushes nothing")
        out = skillsync.sync_device("box", self.manifest, False, remote=self.box)
        self.assertEqual(out["pushed"], ["mine"])
        self.assertEqual(steps(out), steps(dry))
        self.assertTrue(os.path.isfile(self.on_box("dev", "mine", skillsync.MARKER)))
        self.assertFalse(os.path.exists(self.on_box("dev", "mine", ".git")))
        again = skillsync.sync_device("box", self.manifest, False, remote=self.box)
        self.assertEqual(([s["state"] for s in again["prepared"]], again["pushed"]), (["current"], []))
        skill(self.checkout, "say-it", "edited and committed here")
        commit(self.checkout)
        third = skillsync.sync_device("box", self.manifest, False, remote=self.box)
        self.assertEqual(third["pushed"], ["mine"])
        with open(self.on_box("dev", "mine", "skills", "say-it", "SKILL.md")) as f:
            self.assertIn("edited and committed here", f.read())
        back = skillsync.rollback_device("box", self.manifest, False, remote=self.box)
        self.assertEqual(([c["action"] for c in back["content"]], back["mismatches"]), (["pushed"], []))
        with open(self.on_box("dev", "mine", "skills", "say-it", "SKILL.md")) as f:
            self.assertNotIn("edited and committed here", f.read())

    def test_a_hand_made_copy_and_its_links_are_taken_over_without_a_push(self):
        # What a devbox looks like after copying the repository over by hand: files only, no .git, no marker.
        copy = self.on_box("dev", "mine")
        tracked = subprocess.run(["git", "-C", self.checkout, "ls-files"], capture_output=True, text=True).stdout.split()
        for rel in tracked:
            os.makedirs(os.path.dirname(os.path.join(copy, rel)), exist_ok=True)
            shutil.copy(os.path.join(self.checkout, rel), os.path.join(copy, rel))
        for root in (".claude", ".agents"):
            os.makedirs(self.on_box(root, "skills"))
            for name in ("read-pr", "say-it", "taken"):
                os.symlink(os.path.join(copy, "skills", name), self.on_box(root, "skills", name))
        # Only what sync may touch: git on macOS can create ~/Library/Caches in a fresh HOME.
        box = lambda: snapshot(self.on_box("dev"), self.on_box(".claude"), self.on_box(".agents"), self.on_box(".mnemo"))
        before = box()
        dry = skillsync.sync_device("box", self.manifest, True, remote=self.box)
        self.assertEqual([(s["kind"], s["state"]) for s in dry["sources"]], [("copy", "current")])
        self.assertEqual({a["action"] for a in dry["actions"]}, {"adopt"})
        self.assertEqual(box(), before)
        out = skillsync.sync_device("box", self.manifest, False, remote=self.box)
        self.assertEqual((out["pushed"], steps(out)), ([], steps(dry)))
        after = box()
        self.assertEqual({k: v for k, v in after.items() if "/.mnemo" not in k}, before, "only the lock was written")
        self.assertTrue(os.path.isfile(self.on_box(".mnemo", skillsync.LOCK)))

    def test_a_linked_in_device_is_refused(self):
        out = None
        try:
            skillsync.sync_device("lap", self.manifest, True, remote={"name": "lap", "host": "x", "transport": "link"})
        except remote.RemoteError as exc:
            out = str(exc)
        self.assertIn("linked in", out)


class CliTest(Fixture):
    def run_cli(self, *argv):
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, buf.getvalue(), err.getvalue()

    def test_sync_dry_run_and_rollback_from_the_cli(self):
        code, out, _ = self.run_cli("skill", "sync", "--dry-run", "--manifest", self.manifest_path)
        self.assertEqual(code, 0)
        self.assertIn("dry run, nothing changed", out)
        self.assertIn("adopt", out)
        self.assertIn("conflict", out)
        code, out, _ = self.run_cli("skill", "sync", "--json", "--manifest", self.manifest_path)
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["devices"]["local"]["written"])

    def test_missing_manifest_prints_an_example(self):
        code, _, err = self.run_cli("skill", "sync", "--manifest", os.path.join(self.root, "none.toml"))
        self.assertEqual(code, 2)
        self.assertIn("[[install]]", err)

    def test_unknown_device(self):
        code, _, err = self.run_cli("skill", "sync", "--device", "nope", "--manifest", self.manifest_path)
        self.assertEqual(code, 2)
        self.assertIn("no device named", err)


class LinkPolicyTest(unittest.TestCase):
    def test_sync_and_rollback_never_run_over_a_link(self):
        for sub in ("sync", "rollback"):
            self.assertFalse(link.allowed(["skill", sub, "--json", "--host", "local"]))
            self.assertFalse(link.allowed(["skill", sub, "--dry-run", "--json", "--host", "local"]))


if __name__ == "__main__":
    unittest.main()
