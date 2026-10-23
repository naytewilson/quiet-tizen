#!/bin/sh
# Legacy U8000F settings inverse disabled; use only its exact generated inverse.
set -eu
echo 'This unqualified settings inverse is disabled. Review and run restore.sh from the same exact-profile settings plan that produced apply.sh.' >&2
exit 64
