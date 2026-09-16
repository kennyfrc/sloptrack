#!/usr/bin/env bash
# Verify every language in the LANGS table against a known-answer fixture.
#
#   check_languages.sh [check_languages.py flags...]
#
# Asks uvx for every grammar the table declares, so the check runs the same
# way a report does: nothing installed into the user's environment.
#
# Exit codes: 0 all pass, 1 a fixture or vocabulary failed, 2 a grammar could
# not be imported.

set -euo pipefail

# -P resolves symlinks, so a --link install still finds the package above it.
SKILL_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
TREE_SITTER_VERSION="0.25.2"

# An installed skill carries its own copy of both scripts next to this one. In a
# source checkout they live two directories up, in the package, where the analyzer
# is named measure.py rather than slop_measure.py.
if [ -f "$SKILL_DIR/slop_measure.py" ]; then
  PKG_DIR="$SKILL_DIR"
elif [ -f "$SKILL_DIR/../../measure.py" ]; then
  PKG_DIR="$(cd "$SKILL_DIR/../.." && pwd)"
else
  echo "error: the analyzer is not next to $SKILL_DIR or in the package above it" >&2
  echo "       reinstall with: sloptrack install-skill --force" >&2
  exit 2
fi
CHECK="$PKG_DIR/check_languages.py"

if ! command -v uvx >/dev/null 2>&1; then
  echo "note: uvx not found; checking against the python3 environment as-is" >&2
  exec python3 "$CHECK" "$@"
fi

# The checker owns the language table, so it also answers which packages supply
# the grammars. It reads the table only, so this works before anything is installed.
pkgs="$(python3 "$CHECK" --print-requirements "$@")"
if [ -z "$pkgs" ]; then
  echo "error: no grammar packages reported for this request" >&2
  exit 2
fi

with_args=()
for pkg in $pkgs; do
  with_args+=(--with "$pkg")
done

exec uvx --quiet \
  --with "tree-sitter==${TREE_SITTER_VERSION}" \
  ${with_args[@]+"${with_args[@]}"} \
  python3 "$CHECK" "$@"