#!/usr/bin/env bash
# Install the measure-then-fix-slop agent skill into ~/.agents/skills.
#
#   ./install.sh [install-skill flags...]
#   curl -fsSL https://raw.githubusercontent.com/kennyfrc/sloptrack/main/install.sh | bash
#
# Uses this checkout when it is run from one, and a shallow clone otherwise.
# Pass --link to symlink a checkout instead of copying, --dest DIR to install
# somewhere other than ~/.agents/skills, and --force to replace an existing one.

set -euo pipefail

REPO_URL="${SLOPTRACK_REPO:-https://github.com/kennyfrc/sloptrack.git}"
CHECKOUT="${SLOPTRACK_HOME:-$HOME/.local/share/sloptrack}"

here="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
if [ -f "$here/pyproject.toml" ] && [ -d "$here/src/sloptrack" ]; then
  repo="$here"
else
  command -v git >/dev/null 2>&1 || { echo "error: git is required to fetch sloptrack" >&2; exit 2; }
  if [ -d "$CHECKOUT/.git" ]; then
    git -C "$CHECKOUT" pull --ff-only --quiet
  else
    mkdir -p "$(dirname "$CHECKOUT")"
    git clone --depth 1 --quiet "$REPO_URL" "$CHECKOUT"
  fi
  repo="$CHECKOUT"
fi

command -v python3 >/dev/null 2>&1 || { echo "error: python3 is required" >&2; exit 2; }

PYTHONPATH="$repo/src" exec python3 -m sloptrack install-skill "$@"
