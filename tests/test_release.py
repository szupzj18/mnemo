"""scripts/release.py: versions, changelog cuts and release notes."""
import datetime
import importlib.util
import os
import unittest

from helpers import REPO

spec = importlib.util.spec_from_file_location("release", os.path.join(REPO, "scripts", "release.py"))
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

URL = release.REPO_URL
CHANGELOG = """# Changelog

Intro.

## [Unreleased]

### Added
- A thing.

### Fixed
- A bug.

## [0.2.0] - 2026-09-29

Summary.

### Added
- Old thing.

[Unreleased]: %(u)s/compare/v0.2.0...HEAD
[0.2.0]: %(u)s/compare/v0.1.0...v0.2.0
""" % {"u": URL}
INIT = '__version__ = "0.2.0"\n'


class ReleaseTest(unittest.TestCase):
    def test_bump_follows_the_kinds_of_change(self):
        self.assertEqual(release.bump_kind("### Added\n- x\n"), "minor")
        self.assertEqual(release.bump_kind("### Changed\n- x\n### Fixed\n- y\n"), "minor")
        self.assertEqual(release.bump_kind("### Fixed\n- y\n"), "patch")
        self.assertEqual(release.bump_kind("### Added\n\n### Fixed\n"), None, "headings alone are not changes")
        self.assertEqual(release.bump_kind(""), None)
        self.assertEqual(release.next_version("0.2.0", "minor"), "0.3.0")
        self.assertEqual(release.next_version("0.2.3", "patch"), "0.2.4")

    def test_plan(self):
        p = release.plan(CHANGELOG, INIT)
        self.assertEqual((p["needed"], p["current"], p["next"], p["bump"]), (True, "0.2.0", "0.3.0", "minor"))
        self.assertEqual(p["counts"], {"Added": 1, "Fixed": 1})
        self.assertTrue(p["notes"].startswith("### Added\n- A thing."))
        empty = CHANGELOG.replace("### Added\n- A thing.\n\n### Fixed\n- A bug.\n\n", "")
        self.assertFalse(release.plan(empty, INIT)["needed"])

    def test_cut_moves_unreleased_and_updates_links(self):
        out = release.cut(CHANGELOG, "0.3.0", "0.2.0", "2026-10-12")
        self.assertIn("## [Unreleased]\n\n## [0.3.0] - 2026-10-12\n\n### Added\n- A thing.", out)
        self.assertEqual(release.section(out, "Unreleased"), "")
        self.assertEqual(release.section(out, "0.3.0"), "### Added\n- A thing.\n\n### Fixed\n- A bug.")
        self.assertIn("[Unreleased]: %s/compare/v0.3.0...HEAD\n[0.3.0]: %s/compare/v0.2.0...v0.3.0\n"
                      "[0.2.0]: %s/compare/v0.1.0...v0.2.0" % (URL, URL, URL), out)
        self.assertEqual(release.section(out, "0.2.0"), "Summary.\n\n### Added\n- Old thing.")
        with self.assertRaises(SystemExit):
            release.cut(out, "0.3.0", "0.2.0", "2026-10-12")

    def test_version_file(self):
        self.assertEqual(release.current_version(INIT), "0.2.0")
        self.assertEqual(release.bump(INIT, "0.3.0"), '__version__ = "0.3.0"\n')

    def test_releases_run_every_other_week(self):
        weeks = [release.due(datetime.date(2026, 10, 5) + datetime.timedelta(weeks=i)) for i in range(4)]
        self.assertEqual(weeks, [False, True, False, True])  # ISO weeks 41-44

    def test_the_real_changelog_parses(self):
        with open(os.path.join(REPO, "CHANGELOG.md"), encoding="utf-8") as f:
            text = f.read()
        with open(os.path.join(REPO, "mnemo", "__init__.py"), encoding="utf-8") as f:
            version = release.current_version(f.read())
        self.assertIsNotNone(release.section(text, "Unreleased"))
        self.assertTrue(release.section(text, version), "the current version has release notes")


if __name__ == "__main__":
    unittest.main()
