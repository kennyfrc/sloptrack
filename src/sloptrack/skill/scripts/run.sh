#!/usr/bin/env bash
# Measure code slop with the Tree-sitter grammars the target repo actually needs.
#
#   run.sh [PATH] [slop_measure.py flags...]
#
# Uses uvx to build a throwaway environment, so nothing is installed globally.
# Falls back to plain python3 when uv is unavailable (SLOC and git metrics only).

set -euo pipefail

# -P resolves symlinks, so a --link install still finds the package above it.
SKILL_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
TREE_SITTER_VERSION="0.25.2"

# An installed skill carries its own copy of the analyzer next to this script.
# In a source checkout, the analyzer lives two directories up in the package.
if [ -f "$SKILL_DIR/slop_measure.py" ]; then
  MEASURE="$SKILL_DIR/slop_measure.py"
elif [ -f "$SKILL_DIR/../../measure.py" ]; then
  MEASURE="$(cd "$SKILL_DIR/../.." && pwd)/measure.py"
else
  echo "error: slop_measure.py not found next to $SKILL_DIR or in the package above it" >&2
  echo "       reinstall with: sloptrack install-skill --force" >&2
  exit 2
fi

target="."
for arg in "$@"; do
  case "$arg" in
    -*) ;;
    *) target="$arg"; break ;;
  esac
done
[ -e "$target" ] || target="."

if ! command -v uvx >/dev/null 2>&1; then
  echo "note: uvx not found; running without Tree-sitter (SLOC and git metrics only)" >&2
  exec python3 "$MEASURE" "$@"
fi

pkgs="$(python3 "$MEASURE" --print-requirements --no-git -- "$target" 2>/dev/null || true)"

with_args=()
for pkg in $pkgs; do
  with_args+=(--with "$pkg")
done

exec uvx --quiet \
  --with "tree-sitter==${TREE_SITTER_VERSION}" \
  ${with_args[@]+"${with_args[@]}"} \
  python3 "$MEASURE" "$@"