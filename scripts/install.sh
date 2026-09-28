#!/bin/sh
# Install or upgrade mnemo, then connect the coding agents on this machine.
#
#   curl -fsSL https://szupzj18.github.io/mnemo/install.sh | sh
#
# Re-running upgrades: git pull, then `mnemo upgrade` (backup, rebuild, swap).
# Environment overrides:
#   MNEMO_DIR       checkout location          (default ~/mnemo)
#   MNEMO_BIN_DIR   where the `mnemo` link goes (default ~/.local/bin)
#   MNEMO_REPO      git URL to clone           (default the GitHub repo)
#   MNEMO_NO_SETUP  set to 1 to skip `mnemo setup` (agent wiring)
set -eu

MNEMO_DIR="${MNEMO_DIR:-$HOME/mnemo}"
MNEMO_BIN_DIR="${MNEMO_BIN_DIR:-$HOME/.local/bin}"
MNEMO_REPO="${MNEMO_REPO:-https://github.com/szupzj18/mnemo}"

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

command -v git >/dev/null 2>&1 || die "git is required"
PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null 2>&1 || die "python3 (3.7+) is required"
"$PYTHON" - <<'EOF' || die "mnemo needs Python 3.7+ with SQLite FTS5"
import sqlite3, sys
if sys.version_info < (3, 7):
    sys.exit("Python %d.%d is too old" % sys.version_info[:2])
sqlite3.connect(":memory:").execute("CREATE VIRTUAL TABLE t USING fts5(x)")
EOF

upgrade=0
if [ -d "$MNEMO_DIR/.git" ]; then
  say "updating $MNEMO_DIR"
  if [ -n "$(git -C "$MNEMO_DIR" status --porcelain --untracked-files=no)" ]; then
    die "$MNEMO_DIR has local changes; commit or stash them, then re-run"
  fi
  git -C "$MNEMO_DIR" pull --ff-only --quiet
  upgrade=1
elif [ -e "$MNEMO_DIR" ]; then
  die "$MNEMO_DIR exists and is not a mnemo checkout; set MNEMO_DIR to install elsewhere"
else
  say "cloning into $MNEMO_DIR"
  git clone --quiet "$MNEMO_REPO" "$MNEMO_DIR"
fi

mkdir -p "$MNEMO_BIN_DIR"
link="$MNEMO_BIN_DIR/mnemo"
if [ -e "$link" ] && [ ! -L "$link" ]; then
  warn "$link exists and is not a symlink; leaving it alone"
else
  ln -sfn "$MNEMO_DIR/bin/mnemo" "$link"
  say "linked $link"
fi
mnemo="$MNEMO_DIR/bin/mnemo"

if [ "$upgrade" = 1 ]; then
  say "upgrading the index (backup, rebuild, swap)"
  "$PYTHON" "$mnemo" upgrade
else
  say "building the index (first run can take a minute)"
  "$PYTHON" "$mnemo" index
fi

if [ "${MNEMO_NO_SETUP:-0}" != 1 ]; then
  say "connecting agents"
  "$PYTHON" "$mnemo" setup || warn "some agents could not be configured; see above"
fi

case ":$PATH:" in
  *":$MNEMO_BIN_DIR:"*) ;;
  *) warn "$MNEMO_BIN_DIR is not on PATH; add it to your shell profile:
    export PATH=\"$MNEMO_BIN_DIR:\$PATH\"" ;;
esac

say "done. Try: mnemo search <keywords>   or   mnemo dashboard"
