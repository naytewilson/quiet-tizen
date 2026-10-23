#!/bin/sh
# Run through tools/tv.py exec --script only with exact profile model/firmware and a new phase name.
# Arguments: EXPECTED_MODEL EXPECTED_FIRMWARE UNIQUE_PHASE_NAME
set -eu
umask 077
test "$(id -u)" = 0
model_expected=${1:?Exact profile model required}
firmware_expected=${2:?Exact profile firmware required}
name=${3:?Give this snapshot a new unique phase name}
case "$model_expected" in ''|*[!A-Z0-9_]*) echo 'Invalid exact profile model' >&2; exit 1;; esac
case "$firmware_expected" in ''|*[!A-Za-z0-9_.-]*) echo 'Invalid exact profile firmware' >&2; exit 1;; esac
case "$name" in ''|*[!A-Za-z0-9_-]*) echo 'Invalid snapshot name' >&2; exit 1;; esac

identity_check() {
    test -r /etc/prd_info.ini
    model_count=$(sed -n '/^[[:space:]]*ModelName[[:space:]]*=/p' /etc/prd_info.ini | wc -l | tr -d ' ')
    test "$model_count" = 1
    model=$(sed -n 's/^[[:space:]]*ModelName[[:space:]]*=[[:space:]]*//p' /etc/prd_info.ini | sed 's/[[:space:]]*$//')
    firmware_count=$(sed -n '/^[[:space:]]*\(Version\|FirmwareVersion\)[[:space:]]*=/p' /etc/prd_info.ini | wc -l | tr -d ' ')
    test "$firmware_count" = 1
    firmware=$(sed -n 's/^[[:space:]]*\(Version\|FirmwareVersion\)[[:space:]]*=[[:space:]]*//p' /etc/prd_info.ini | sed 's/[[:space:]]*;[[:space:]]*$//; s/[[:space:]]*$//')
    test "$model" = "$model_expected"
    test "$firmware" = "$firmware_expected"
}

hash_readback() {
    filename=$1
    digest=$(cd "$D" && sha256sum "$filename" | awk '{print $1}')
    test -n "$digest"
    ( set -C; cd "$D"; printf '%s  %s\n' "$digest" "$filename" > "$filename.sha256" )
    ( cd "$D" && sha256sum -c "$filename.sha256" )
}

artifact_report() {
    filename=$1
    path=$D/$filename
    test -f "$path" && test ! -L "$path"
    case "$path" in /*) ;; *) echo 'Refusing non-absolute backup artifact path.' >&2; return 1;; esac
    size=$(wc -c < "$path" | tr -d '[:space:]')
    digest=$(sha256sum "$path" | awk '{print $1}')
    case "$size" in ''|*[!0-9]*) echo 'Invalid backup artifact size.' >&2; return 1;; esac
    case "$digest" in ''|*[!a-f0-9]*) echo 'Invalid backup artifact SHA-256.' >&2; return 1;; esac
    test "${#digest}" = 64
    printf 'ARTIFACT|%s|%s|%s\n' "$path" "$size" "$digest"
}

# Identity is established before creating a backup directory or file.
identity_check
BACKUP_ROOT=/opt/usr/share/quiet-tizen-backups
if [ -e "$BACKUP_ROOT" ] || [ -L "$BACKUP_ROOT" ]; then
    test -d "$BACKUP_ROOT" && test ! -L "$BACKUP_ROOT"
else
    # Only this project-owned parent may be created. Existing vendor ancestors
    # are required; never create or relabel a missing system directory.
    parent=${BACKUP_ROOT%/*}
    test -d "$parent" && test ! -L "$parent"
    mkdir "$BACKUP_ROOT"
fi
D=$BACKUP_ROOT/$name
test ! -e "$D" && test ! -L "$D" || { echo 'Snapshot already exists; never overwrite the original.' >&2; exit 1; }
mkdir "$D"

for base in .pkgmgr_parser .app-context .webappservice; do
    source=/opt/dbspace/$base.db
    filename=$base.db
    destination=$D/$filename
    test -f "$source" && test ! -L "$source"
    test ! -e "$destination" && test ! -L "$destination"
    test "$(sqlite3 -readonly "$source" 'PRAGMA quick_check;')" = ok
    sqlite3 -readonly "$source" ".backup '$destination'"
    test -f "$destination" && test ! -L "$destination"
    test "$(sqlite3 -readonly "$destination" 'PRAGMA quick_check;')" = ok
    hash_readback "$filename"
done

(
    set -C
    for key in db/appfw/home_appid db/appfw/startup_appid \
        db/was/preload_by_country db/menu/autorun_tv_plus \
        db/menu/voice/voice_activated db/menu/voice/wakeupless_status \
        db/si_service/voice_recog_enable db/multi-assistant/prelaunch_mode; do
        printf '%s\n' "$key"
        vconftool get "$key"
    done > "$D/preferences-before.txt"
)
test -s "$D/preferences-before.txt" && test ! -L "$D/preferences-before.txt"
hash_readback preferences-before.txt

( set -C; cd "$D"; sha256sum .pkgmgr_parser.db .app-context.db .webappservice.db preferences-before.txt > SHA256SUMS )
( cd "$D" && sha256sum -c SHA256SUMS )
artifact_report .pkgmgr_parser.db
artifact_report .app-context.db
artifact_report .webappservice.db
artifact_report preferences-before.txt
echo "Verified, immutable snapshot retained at: $D"
echo 'This helper records state only. Use a separate reviewed phase plan for every later write.'
