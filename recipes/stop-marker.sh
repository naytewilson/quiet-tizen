#!/bin/sh
# Unqualified service-marker mutation is blocked; current offline plans do not generate this phase.
set -eu
echo 'Direct service-marker writes are disabled. Do not use this legacy helper; optional cleanup needs exact-profile dependency proof, per-group owner opt-in, and a reviewed generated inverse.' >&2
exit 64
