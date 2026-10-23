#!/bin/sh
# Legacy U8000F inverse disabled; use only the inverse paired with the exact launcher plan.
set -eu
echo 'This unqualified inverse is disabled. Review and run restore.sh from the same exact-profile plan that produced the install.' >&2
exit 64
