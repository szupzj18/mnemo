#!/usr/bin/env python3
"""Cut releases from CHANGELOG.md. Used by .github/workflows/release-pr.yml and release.yml.

  release.py current              the version in mnemo/__init__.py
  release.py plan [--json]        whether Unreleased has entries, and the next version
  release.py prepare [--version X] [--date YYYY-MM-DD]
                                  move Unreleased into a new version section, bump __version__
  release.py notes VERSION        that version's changelog section (release notes)
  release.py due [--date D]       exit 0 in release weeks (even ISO weeks), 1 otherwise

Versioning while on 0.x: Added, Changed, Removed or Deprecated entries bump the
minor version; only Fixed or Security entries bump the patch version. Pass
--version to override (e.g. for 1.0.0).
"""
import argparse
import datetime
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHANGELOG = os.path.join(ROOT, "CHANGELOG.md")
VERSION_FILE = os.path.join(ROOT, "mnemo", "__init__.py")
REPO_URL = "https://github.com/szupzj18/mnemo"

MINOR = ("Added", "Changed", "Removed", "Deprecated")
PATCH = ("Fixed", "Security")
VERSION_RE = re.compile(r'^__version__ = "([^"]+)"$', re.M)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def current_version(init_text):
    m = VERSION_RE.search(init_text)
    if not m:
        raise SystemExit("no __version__ in mnemo/__init__.py")
    return m.group(1)


def section(changelog, heading):
    """Body of `## [heading]...` up to the next `## [` or the link references."""
    m = re.search(r"^## \[%s\][^\n]*\n" % re.escape(heading), changelog, re.M)
    if not m:
        return None
    rest = changelog[m.end():]
    end = re.search(r"^## \[|^\[[^\]]+\]: ", rest, re.M)
    return rest[:end.start() if end else len(rest)].strip("\n")


def entries(body):
    """{"Added": [...], "Fixed": [...]}: bullet entries per `### Kind` subsection."""
    out, kind = {}, None
    for line in (body or "").splitlines():
        h = re.match(r"^### (\w+)", line)
        if h:
            kind = h.group(1)
            out.setdefault(kind, [])
        elif kind and line.startswith("- "):
            out[kind].append(line)
    return {k: v for k, v in out.items() if v}


def bump_kind(body):
    kinds = entries(body)
    if any(k in kinds for k in MINOR):
        return "minor"
    if any(k in kinds for k in PATCH):
        return "patch"
    return None


def next_version(version, kind):
    major, minor, patch = (int(x) for x in version.split("."))
    if kind == "minor":
        return "%d.%d.0" % (major, minor + 1)
    if kind == "patch":
        return "%d.%d.%d" % (major, minor, patch + 1)
    raise ValueError(kind)


def plan(changelog, init_text):
    cur = current_version(init_text)
    body = section(changelog, "Unreleased")
    kind = bump_kind(body)
    if not kind:
        return {"needed": False, "current": cur, "reason": "nothing under Unreleased"}
    return {"needed": True, "current": cur, "bump": kind, "next": next_version(cur, kind),
            "counts": {k: len(v) for k, v in entries(body).items()}, "notes": body}


def cut(changelog, version, previous, date):
    """Move Unreleased into `## [version] - date` and point the link references at the new tag."""
    if section(changelog, version) is not None:
        raise SystemExit("CHANGELOG already has a %s section" % version)
    changelog = re.sub(r"^## \[Unreleased\][^\n]*\n", "## [Unreleased]\n\n## [%s] - %s\n" % (version, date),
                       changelog, count=1, flags=re.M)
    changelog = re.sub(r"\n{3,}(?=## \[%s\])" % re.escape(version), "\n\n", changelog)
    link = re.compile(r"^\[Unreleased\]: .*$", re.M)
    new_links = ("[Unreleased]: %s/compare/v%s...HEAD\n[%s]: %s/compare/v%s...v%s"
                 % (REPO_URL, version, version, REPO_URL, previous, version))
    if link.search(changelog):
        changelog = link.sub(new_links, changelog, count=1)
    else:
        changelog = changelog.rstrip("\n") + "\n\n" + new_links + "\n"
    return changelog


def bump(init_text, version):
    return VERSION_RE.sub('__version__ = "%s"' % version, init_text, count=1)


def due(date):
    return date.isocalendar()[1] % 2 == 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("current")
    sp = sub.add_parser("plan")
    sp.add_argument("--json", action="store_true")
    sp = sub.add_parser("prepare")
    sp.add_argument("--version")
    sp.add_argument("--date", default=datetime.date.today().isoformat())
    sp = sub.add_parser("notes")
    sp.add_argument("version")
    sp = sub.add_parser("due")
    sp.add_argument("--date", default=datetime.date.today().isoformat())
    args = p.parse_args(argv)

    changelog, init_text = read(CHANGELOG), read(VERSION_FILE)
    if args.cmd == "current":
        print(current_version(init_text))
    elif args.cmd == "plan":
        result = plan(changelog, init_text)
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        elif result["needed"]:
            print("release %s -> %s (%s): %s" % (result["current"], result["next"], result["bump"],
                                                  ", ".join("%d %s" % (n, k) for k, n in result["counts"].items())))
        else:
            print("no release: %s" % result["reason"])
    elif args.cmd == "prepare":
        result = plan(changelog, init_text)
        version = args.version or result.get("next")
        if not version:
            raise SystemExit("no release: %s" % result["reason"])
        if not re.match(r"^\d+\.\d+\.\d+$", version):
            raise SystemExit("version must look like 1.2.3")
        write(CHANGELOG, cut(changelog, version, result["current"], args.date))
        write(VERSION_FILE, bump(init_text, version))
        print(version)
    elif args.cmd == "notes":
        body = section(changelog, args.version)
        if body is None:
            raise SystemExit("no %s section in CHANGELOG.md" % args.version)
        print(body)
    elif args.cmd == "due":
        return 0 if due(datetime.date.fromisoformat(args.date)) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
