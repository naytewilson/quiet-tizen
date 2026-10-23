#!/bin/sh
# Legacy U8000F mutation path intentionally disabled before any target write.
set -eu
echo 'This unqualified installer is disabled. Generate and review a new exact-profile manual launcher plan with tools/plan_launcher.py, then use only that plan directory install.sh.' >&2
exit 64
