# sourced by the case fixtures: find the plugin's bftune launcher (this file lives in <plugin>/evals/)
EVALS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BFTUNE="$EVALS_DIR/../bin/bftune"
[ -x "$BFTUNE" ] || BFTUNE="$(command -v bftune)"
