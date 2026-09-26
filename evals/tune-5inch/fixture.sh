#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/../_fixture_common.sh"
mkdir -p fpv/5inch
"$BFTUNE" synth 5inch -o fpv/5inch/flight1.pkl --repeats 2 --chirp-s 20 --freestyle-s 20 --seed 11 >/dev/null
