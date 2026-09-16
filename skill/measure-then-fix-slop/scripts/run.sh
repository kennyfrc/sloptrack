#!/usr/bin/env bash
# Measure code slop with the Tree-sitter grammars the target repo actually needs.
#
#   run.sh [PATH] [slop_measure.py flags...]
#
# Uses uvx to build a throwaway environment, so nothing is installed globally.
# Falls back to plain python3 when uv is unavailable (SLOC and git metrics only).

set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MEASURE="$SKILL_DIR/slop_measure.py"
TREE_SITTER_VERSION="0.25.2"

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