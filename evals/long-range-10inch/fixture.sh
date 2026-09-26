#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/../_fixture_common.sh"
mkdir -p lr10
"$BFTUNE" synth 10inch -o lr10/chirps.pkl --repeats 2 --chirp-s 20 --freestyle-s 20 --seed 13 >/dev/null
