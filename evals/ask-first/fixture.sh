#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/../_fixture_common.sh"
mkdir -p 1
"$BFTUNE" synth 5inch -o 1/btfl_001.pkl --chirp-s 12 --freestyle-s 10 --seed 21 >/dev/null
