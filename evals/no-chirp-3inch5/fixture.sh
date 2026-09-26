#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/../_fixture_common.sh"
mkdir -p mini
"$BFTUNE" synth 3.5inch -o mini/LOG00007.pkl --no-chirp --freestyle-s 60 --seed 12 >/dev/null
