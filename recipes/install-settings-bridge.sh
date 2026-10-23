#!/bin/sh
# Legacy U8000F settings mutation path intentionally disabled before any target write.
set -eu
echo 'This unqualified settings installer is disabled. Generate and review an exact-profile settings plan with tools/plan_settings_bridge.py, then use only that plan directory apply.sh.' >&2
exit 64
