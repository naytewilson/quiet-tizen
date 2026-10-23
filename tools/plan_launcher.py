#!/usr/bin/env python3
"""Generate a profile-bound manual launcher install and exact inverse offline.

The output is a reviewed plan only. It never contacts a television and does not
change Home/startup selectors, package registry flags, settings routes, services,
network state, or application data.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import sqlite3
import stat
import tarfile

from build import check_catalog, confined_generated_path, launcher_metadata, profile_key
from profiles import load_profile, require_capability

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / ".local"
APP_FIELDS = ("app_disable", "app_onboot", "app_autorestart")
REQUIRED_BACKUP_FILES = frozenset((
    ".pkgmgr_parser.db", ".app-context.db", ".webappservice.db", "preferences-before.txt",
))
MAX_EVIDENCE_AGE_SECONDS = 24 * 60 * 60
SHA256 = re.compile(r"\A[a-f0-9]{64}\Z")
APP_NAME = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
BACKUP_NAME = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
STAGE_NAME = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.tar\.gz\Z")
PACKAGE_PATH = re.compile(r"\A[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\Z")
MODE = re.compile(r"\A[0-7]{3,4}\Z")


def _hash_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"Duplicate JSON key: {key}")
        value[key] = item
    return value


def _read_json(path, label):
    try:
        with Path(path).open("r", encoding="utf-8") as stream:
            return json.load(stream, object_pairs_hook=_unique_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read {label}: {error}") from error


def _reject_symlink_chain(path, stop_at=None):
    path = Path(path).expanduser().absolute()
    stop = Path(stop_at).absolute() if stop_at is not None else None
    current = path
    while True:
        if current.is_symlink():
            raise ValueError(f"Refusing symlink path component: {current}")
        if current == stop or current.parent == current:
            break
        current = current.parent
    return path


def _private_input(path, label):
    original = _reject_symlink_chain(path)
    private = PRIVATE.resolve(strict=False)
    resolved = original.resolve(strict=True)
    if original == private or private not in resolved.parents or not resolved.is_file():
        raise ValueError(f"{label} must be a regular, non-symlink file below this checkout's .local/.")
    _reject_symlink_chain(original, PRIVATE)
    return resolved


def _regular_profile_input(path):
    original = _reject_symlink_chain(path)
    resolved = original.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("Profile must be a regular file.")
    return resolved


def _timestamp(value, label):
    if not isinstance(value, str):
        raise ValueError(f"{label} requires a timezone-aware timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} requires a timezone-aware timestamp.") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{label} requires a timezone-aware timestamp.")
    return parsed.astimezone(timezone.utc)


def _require_fresh(value, label, now=None):
    now = now or datetime.now(timezone.utc)
    observed = _timestamp(value, label)
    age = (now - observed).total_seconds()
    if not 0 <= age <= MAX_EVIDENCE_AGE_SECONDS:
        raise ValueError(f"{label} is stale or from the future; reacquire exact evidence.")
    return observed


def _profile(profile_path):
    profile = load_profile(profile_path)
    for capability in ("launcher_build", "package_host", "launcher_install", "rollback_ready"):
        require_capability(profile, capability)
    launcher, config = launcher_metadata(profile)
    if profile.get("registry") != "/opt/dbspace/.pkgmgr_parser.db":
        raise ValueError("Manual launcher planning requires the exact package registry path.")
    return profile, launcher, config


def _inventory(inventory_path, profile, launcher, config, now=None):
    inventory = _read_json(inventory_path, "current inventory")
    if not isinstance(inventory, dict) or inventory.get("schema") != "quiet-tizen.inventory.v1":
        raise ValueError("A current quiet-tizen.inventory.v1 record is required.")
    collected = _require_fresh(inventory.get("collected_at"), "Current inventory", now)
    device = inventory.get("device")
    if not isinstance(device, dict):
        raise ValueError("Current inventory has no exact device identity.")
    for field in ("model", "firmware"):
        item = device.get(field)
        if not isinstance(item, dict) or item.get("status") != "PROVEN":
            raise ValueError(f"Current inventory {field} is not PROVEN.")
        if item.get("value") != profile[field]:
            raise ValueError(f"Current inventory {field} does not match the exact profile.")
        _require_fresh(item.get("observed_at", inventory.get("collected_at")),
                       f"Current inventory {field}", now)

    try:
        report = inventory["databases"]["package_app_info"]
        rows_record = report["value"]
        rows = rows_record["rows"]
    except (KeyError, TypeError) as error:
        raise ValueError("Current inventory is missing package_app_info rows.") from error
    if (not isinstance(report, dict) or report.get("status") != "PROVEN" or
            not isinstance(rows_record, dict) or rows_record.get("complete") is not True or
            not isinstance(rows, list) or rows_record.get("row_count") != len(rows)):
        raise ValueError("Current package_app_info inventory is incomplete or unproven.")
    required_ids = {launcher["app_id"], *config["apps"], config["viewer_app"], config["native_settings_app"]}
    if any(not isinstance(app_id, str) or not APP_NAME.fullmatch(app_id) for app_id in required_ids):
        raise ValueError("Launcher targets contain an invalid application ID.")
    app_rows = {}
    for app_id in sorted(required_ids):
        matches = [row for row in rows if isinstance(row, dict) and row.get("app_id") == app_id]
        if len(matches) != 1:
            raise ValueError(f"Current package_app_info needs exactly one {app_id} row.")
        row = matches[0]
        if set(row) != {"app_id", *APP_FIELDS}:
            raise ValueError(f"Current package_app_info row for {app_id} has missing or unexpected fields.")
        if any(row.get(field) not in ("true", "false") for field in APP_FIELDS):
            raise ValueError(f"Current package_app_info flags for {app_id} are not strict boolean strings.")
        if row["app_disable"] != "false":
            role = "Launcher host package" if app_id == launcher["app_id"] else f"Configured launcher app target {app_id}"
            raise ValueError(f"{role} is not installed and enabled in current package inventory.")
        app_rows[app_id] = row
    return inventory, app_rows, collected


def _safe_mode(value, label):
    if not isinstance(value, str) or not MODE.fullmatch(value):
        raise ValueError(f"{label} mode must be an observed octal permission string.")
    parsed = int(value, 8)
    if parsed & 0o6000 or parsed & 0o022:
        raise ValueError(f"{label} mode has set-ID or group/world write permissions.")
    return value


def _host_evidence(host_path, profile, launcher, snapshot_path, backup_name, now=None):
    host = _read_json(host_path, "private launcher-host evidence")
    if not isinstance(host, dict) or host.get("schema") != "quiet-tizen.launcher-host.v1":
        raise ValueError("Private quiet-tizen.launcher-host.v1 evidence is required.")
    observed = _require_fresh(host.get("observed_at"), "Private launcher-host evidence", now)
    if host.get("model") != profile["model"] or host.get("firmware") != profile["firmware"]:
        raise ValueError("Private launcher-host evidence model/firmware differs from the exact profile.")
    if type(host.get("launcher_uid")) is not int or host["launcher_uid"] != launcher["uid"]:
        raise ValueError("Private launcher-host evidence UID does not match the exact profile.")
    expected_inputs = profile["launcher_config"]["inputs"]
    if host.get("source_inputs") != expected_inputs:
        raise ValueError("Private source-input evidence does not exactly match profile IDs and labels.")

    package_root = host.get("package_root")
    if (not isinstance(package_root, dict) or package_root.get("path") != launcher["root"] or
            package_root.get("is_directory") is not True or package_root.get("is_symlink") is not False or
            package_root.get("owner") != launcher["owner"]):
        raise ValueError("Private launcher-host package-root path, type, symlink, or owner differs from the profile.")
    root_mode = _safe_mode(package_root.get("mode"), "Launcher package root")

    entry = host.get("entry")
    entry_path = f"{launcher['root']}/bin/{launcher['dll']}"
    if (not isinstance(entry, dict) or entry.get("path") != entry_path or
            entry.get("owner") != launcher["owner"] or
            entry.get("smack_label") != launcher["smack_label"] or
            not isinstance(entry.get("sha256"), str) or not SHA256.fullmatch(entry["sha256"])):
        raise ValueError("Private launcher entry path, hash, owner, or SMACK label does not match the exact profile.")
    entry_mode = _safe_mode(entry.get("mode"), "Launcher entry")
    original_backup = entry.get("original_backup")
    if (not isinstance(original_backup, str) or
            not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", original_backup) or original_backup in (".", "..")):
        raise ValueError("Private launcher-host evidence must name its exact original entry backup file.")
    original_entry = _private_input(Path(host_path).parent / original_backup, "Private original launcher assembly")
    if _hash_file(original_entry) != entry["sha256"]:
        raise ValueError("Private original assembly hash differs from launcher-host evidence.")

    resource = host.get("resource_path")
    resource_path = f"{launcher['root']}/res/quiet-home"
    if (not isinstance(resource, dict) or resource.get("path") != resource_path or
            resource.get("exists") is not False or resource.get("is_symlink", False) is not False):
        raise ValueError("Private launcher resource path is present, symlinked, or not the exact configured root.")

    cache = host.get("native_image")
    cache_path = f"{launcher['root']}/bin/.native_image"
    if (not isinstance(cache, dict) or cache.get("path") != cache_path or
            cache.get("exists") is not False or cache.get("is_symlink", False) is not False):
        raise ValueError("Private native-image evidence is present, symlinked, or not the exact configured root.")

    backup = host.get("backup")
    expected_backup = f"/opt/usr/share/quiet-tizen-backups/{backup_name}"
    if not isinstance(backup, dict) or backup.get("path") != expected_backup:
        raise ValueError("Private backup path does not match the exact, unique phase name.")
    files = backup.get("files")
    if not isinstance(files, dict) or not REQUIRED_BACKUP_FILES <= set(files):
        raise ValueError("Private backup evidence must include the package, app-context, webappservice, and preference snapshots.")
    snapshot_hash = _hash_file(snapshot_path)
    if files.get(".pkgmgr_parser.db") != snapshot_hash:
        raise ValueError("Private package registry snapshot digest does not match the supplied snapshot.")
    host_parent = Path(host_path).parent
    for name, expected_hash in files.items():
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", name) or
                name in (".", "..") or not isinstance(expected_hash, str) or not SHA256.fullmatch(expected_hash)):
            raise ValueError("Private snapshot file map contains an unsafe name or digest.")
        if name == ".pkgmgr_parser.db":
            actual_path = snapshot_path
        else:
            actual_path = _private_input(host_parent / name, f"Private {name} snapshot")
        if _hash_file(actual_path) != expected_hash:
            raise ValueError(f"Private {name} snapshot digest differs from host evidence.")
    return host, observed, entry, original_entry, root_mode, entry_mode, files, snapshot_hash


def _snapshot_rows(snapshot_path, expected_rows):
    try:
        uri = Path(snapshot_path).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            integrity = connection.execute("PRAGMA quick_check").fetchall()
            if integrity != [("ok",)]:
                raise ValueError("Package registry snapshot failed PRAGMA quick_check.")
            columns = {row[1] for row in connection.execute("PRAGMA table_info(package_app_info)")}
            if not {"app_id", *APP_FIELDS} <= columns:
                raise ValueError("Package registry snapshot lacks the exact package_app_info fields.")
            rows = {}
            for app_id in expected_rows:
                found = connection.execute(
                    "SELECT app_id,app_disable,app_onboot,app_autorestart "
                    "FROM package_app_info WHERE app_id=?", (app_id,),
                ).fetchall()
                if len(found) != 1:
                    raise ValueError(f"Package registry snapshot needs exactly one {app_id} row.")
                rows[app_id] = dict(zip(("app_id", *APP_FIELDS), found[0]))
    except sqlite3.Error as error:
        raise ValueError(f"Cannot verify the immutable package registry snapshot: {error}") from error
    if rows != expected_rows:
        raise ValueError("Current inventory and immutable package registry snapshot differ for a configured launcher target.")
    return rows


def _package_files(package_path, profile, launcher, config):
    package_path = Path(package_path)
    if not package_path.is_dir() or package_path.is_symlink():
        raise ValueError(f"Exact profile build package is missing or symlinked: {package_path.relative_to(ROOT)}")
    # Reject every symlink or special file in the profile build, including
    # files excluded from this manual phase. Never archive a path by following it.
    for directory, dirs, names in os.walk(package_path, followlinks=False):
        parent = Path(directory)
        for name in [*dirs, *names]:
            candidate = parent / name
            mode = candidate.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ValueError(f"Profile build contains a symlink: {candidate.relative_to(package_path)}")
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise ValueError(f"Profile build contains a non-regular package item: {candidate.relative_to(package_path)}")

    entry = package_path / launcher["dll"]
    assets = package_path / "quiet-home"
    if not entry.is_file() or entry.is_symlink() or not assets.is_dir() or assets.is_symlink():
        raise ValueError("Profile build must contain the configured entry DLL and quiet-home resource directory.")
    local_catalog = assets / "apps.local.json"
    if local_catalog.exists() or local_catalog.is_symlink():
        raise ValueError("Private apps.local.json must not be packaged for a launcher install.")
    actual_config = _read_json(assets / "launcher.json", "built launcher configuration")
    if actual_config != config:
        raise ValueError("Built launcher configuration does not exactly match the selected profile.")
    try:
        check_catalog(profile, assets)
    except (OSError, ValueError) as error:
        raise ValueError(f"Built app catalog is not valid for the exact profile: {error}") from error

    selected = {launcher["dll"]: entry}
    for directory, dirs, names in os.walk(assets, followlinks=False):
        dirs.sort()
        names.sort()
        for name in names:
            path = Path(directory) / name
            relative = path.relative_to(package_path).as_posix()
            if not PACKAGE_PATH.fullmatch(relative) or ".." in PurePosixPath(relative).parts:
                raise ValueError(f"Unsafe profile package path: {relative!r}")
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"Profile resource is not a regular file: {relative}")
            selected[relative] = path

    package = []
    for relative, path in sorted(selected.items()):
        if not PACKAGE_PATH.fullmatch(relative) or Path(relative).is_absolute() or ".." in PurePosixPath(relative).parts:
            raise ValueError(f"Unsafe profile package path: {relative!r}")
        package.append({"path": relative, "sha256": _hash_file(path), "size": path.stat().st_size})
    if not any(item["path"] == launcher["dll"] for item in package):
        raise ValueError("Configured launcher entry is absent from the staged package.")
    return package


def _verify_archive(path, package_files):
    expected = {item["path"]: item["sha256"] for item in package_files}
    try:
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
            names = [member.name for member in members]
            if len(names) != len(set(names)) or set(names) != set(expected):
                raise ValueError("Generated archive members differ from the exact staged package contents.")
            for member in members:
                if (not member.isfile() or member.issym() or member.islnk() or member.name.startswith("/") or
                        ".." in PurePosixPath(member.name).parts or not PACKAGE_PATH.fullmatch(member.name)):
                    raise ValueError(f"Generated archive contains an unsafe member: {member.name!r}")
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError(f"Generated archive member cannot be read: {member.name!r}")
                digest = hashlib.sha256()
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                if digest.hexdigest() != expected[member.name]:
                    raise ValueError(f"Generated archive member hash mismatch: {member.name}")
    except (OSError, tarfile.TarError, EOFError) as error:
        raise ValueError(f"Cannot verify generated package archive: {error}") from error


def _write_archive(path, package_path, package_files):
    # Only validated regular files are written. No directory, symlink, hardlink,
    # absolute, or parent-traversal tar member is ever emitted.
    with Path(path).open("xb") as output:
        with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for item in package_files:
                    relative = item["path"]
                    source = Path(package_path) / relative
                    if source.is_symlink() or not source.is_file():
                        raise ValueError(f"Profile package changed during planning: {relative}")
                    info = tarfile.TarInfo(relative)
                    info.type = tarfile.REGTYPE
                    info.mode = 0o644
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mtime = 0
                    info.size = source.stat().st_size
                    with source.open("rb") as stream:
                        archive.addfile(info, stream)
    _verify_archive(path, package_files)


def identity_check(model, firmware):
    model_q = shlex.quote(model)
    firmware_q = shlex.quote(firmware)
    return f"""test -r /etc/prd_info.ini
model_count=$(sed -n '/^[[:space:]]*ModelName[[:space:]]*=/p' /etc/prd_info.ini | wc -l | tr -d ' ')
test "$model_count" = 1
model=$(sed -n 's/^[[:space:]]*ModelName[[:space:]]*=[[:space:]]*//p' /etc/prd_info.ini | sed 's/[[:space:]]*$//')
firmware_count=$(sed -n '/^[[:space:]]*\\(Version\\|FirmwareVersion\\)[[:space:]]*=/p' /etc/prd_info.ini | wc -l | tr -d ' ')
test "$firmware_count" = 1
firmware=$(sed -n 's/^[[:space:]]*\\(Version\\|FirmwareVersion\\)[[:space:]]*=[[:space:]]*//p' /etc/prd_info.ini | sed 's/[[:space:]]*;[[:space:]]*$//; s/[[:space:]]*$//')
test "$model" = {model_q}
test "$firmware" = {firmware_q}"""


def _resource_shape(package_files):
    files = sorted(item["path"][len("quiet-home/"):] for item in package_files
                   if item["path"].startswith("quiet-home/"))
    directories = sorted({parent.as_posix() for item in files
                          for parent in PurePosixPath(item).parents
                          if parent.as_posix() != "."})
    return files, directories


def _literal_lines(values):
    return "\n".join(values)


def _resource_verifier(package_files):
    files, directories = _resource_shape(package_files)
    file_branches = "\n".join(
        f"        {shlex.quote(name)}) expected_hash={shlex.quote(next(item['sha256'] for item in package_files if item['path'] == 'quiet-home/' + name))} ;;"
        for name in files
    )
    allowed_file_case = "\n".join(shlex.quote(name) + ") ;;" for name in files)
    allowed_dir_case = "\n".join(shlex.quote(name) + ") ;;" for name in directories)
    full_files = _literal_lines(files)
    full_dirs = _literal_lines(directories)
    return f"""verify_resource_tree() {{
    tree=$1
    complete=$2
    test -d "$tree" && test ! -L "$tree"
    links=$(cd "$tree" && find . -type l -print)
    test -z "$links"
    other=$(cd "$tree" && find . ! -type f ! -type d ! -type l -print)
    test -z "$other"
    actual_files=$(cd "$tree" && find . -type f -print | sed 's|^\\./||' | LC_ALL=C sort)
    actual_dirs=$(cd "$tree" && find . -type d -print | sed -n 's|^\\./||p' | LC_ALL=C sort)
    if [ "$complete" = yes ]; then
        test "$actual_files" = {shlex.quote(full_files)}
        test "$actual_dirs" = {shlex.quote(full_dirs)}
    else
        while IFS= read -r rel; do
            [ -z "$rel" ] && continue
            case "$rel" in
{allowed_file_case}
                *) echo 'Unexpected resource file; preserving it.' >&2; return 1 ;;
            esac
        done <<QUIET_FILES
$actual_files
QUIET_FILES
        while IFS= read -r rel; do
            [ -z "$rel" ] && continue
            case "$rel" in
{allowed_dir_case}
                *) echo 'Unexpected resource directory; preserving it.' >&2; return 1 ;;
            esac
        done <<QUIET_DIRS
$actual_dirs
QUIET_DIRS
    fi
    while IFS= read -r rel; do
        [ -z "$rel" ] && continue
        case "$rel" in
{file_branches}
            *) echo 'Unexpected resource file; preserving it.' >&2; return 1 ;;
        esac
        file="$tree/$rel"
        test -f "$file" && test ! -L "$file"
        check_hash "$file" "$expected_hash"
    done <<QUIET_CHECK_FILES
$actual_files
QUIET_CHECK_FILES
}}"""


def _backup_checks(backup_files):
    lines = []
    for name, digest in sorted(backup_files.items()):
        lines.extend((
            f'test -f "$D/{name}" && test ! -L "$D/{name}"',
            f'check_hash "$D/{name}" {shlex.quote(digest)}',
        ))
    return "\n".join(lines)


def _install_script(profile, launcher, app_rows, package_files, archive_hash, backup_name, stage_name,
                    entry_before_hash, entry_mode, root_mode, backup_files):
    model_q, firmware_q = shlex.quote(profile["model"]), shlex.quote(profile["firmware"])
    app_id_q = shlex.quote(launcher["app_id"])
    root_q = shlex.quote(launcher["root"])
    entry_q = shlex.quote(f"{launcher['root']}/bin/{launcher['dll']}")
    resource_q = shlex.quote(f"{launcher['root']}/res/quiet-home")
    root_owner_q = shlex.quote(launcher["owner"])
    file_owner_q = shlex.quote(launcher["owner"])
    uid_q = shlex.quote(str(launcher["uid"]))
    smack_q = shlex.quote(launcher["smack_label"])
    root_mode_q, entry_mode_q = shlex.quote(root_mode), shlex.quote(entry_mode)
    backup_q, archive_name_q = shlex.quote(backup_name), shlex.quote(stage_name)
    expected_members = _literal_lines([item["path"] for item in package_files])
    package_hash_rows = "\n".join(f"{item['path']} {item['sha256']}" for item in package_files)
    app_row_checks = []
    for index, (app_id, app_row) in enumerate(sorted(app_rows.items())):
        app_row_value = "|".join((app_id, *(app_row[field] for field in APP_FIELDS)))
        app_row_checks.extend((
            f"live_row_{index}=$(sqlite3 -readonly /opt/dbspace/.pkgmgr_parser.db \"SELECT app_id||'|'||app_disable||'|'||app_onboot||'|'||app_autorestart FROM package_app_info WHERE app_id={shlex.quote(app_id)};\")",
            f"test \"$live_row_{index}\" = {shlex.quote(app_row_value)}",
        ))
    checks = _backup_checks(backup_files)
    verifier = _resource_verifier(package_files)
    return f"""#!/bin/sh
# Generated offline from exact model/firmware, package, inventory, and private backup evidence.
# Manual launcher files only: no Home/Startup assignment, app launch, settings route, or cleanup.
set -eu
umask 077
test "$(id -u)" = 0
{identity_check(profile['model'], profile['firmware'])}
D=/opt/usr/share/quiet-tizen-backups/{backup_q}
ARCHIVE="$D/{archive_name_q}"
WORK="$D/.launcher-plan-{backup_q}"
ROOT={root_q}
ENTRY={entry_q}
RESOURCE={resource_q}
APP_ID={app_id_q}
LAUNCHER_UID={uid_q}
OWNER={file_owner_q}
SMACK={smack_q}

check_hash() {{
    actual=$(sha256sum "$1" | awk '{{print $1}}')
    test "$actual" = "$2"
}}
check_label() {{
    found=$(chsmack --if-access "$1" --name-only "$2")
    test "$found" = "$2"
}}
{verifier}

test -d "$D" && test ! -L "$D"
{checks}
test -f "$ARCHIVE" && test ! -L "$ARCHIVE"
check_hash "$ARCHIVE" {shlex.quote(archive_hash)}
test ! -e "$WORK" && test ! -L "$WORK"

test -d "$ROOT" && test ! -L "$ROOT"
test "$(readlink -f "$ROOT")" = "$ROOT"
test "$(stat -c '%u:%g' "$ROOT")" = {root_owner_q}
test "$(stat -c '%a' "$ROOT")" = {root_mode_q}
test -d "$ROOT/res" && test ! -L "$ROOT/res"
test -f "$ENTRY" && test ! -L "$ENTRY"
test "$(stat -c '%u:%g' "$ENTRY")" = {file_owner_q}
test "$(stat -c '%a' "$ENTRY")" = {entry_mode_q}
check_hash "$ENTRY" {shlex.quote(entry_before_hash)}
check_label "$SMACK" "$ENTRY"
test ! -e "$RESOURCE" && test ! -L "$RESOURCE"
test ! -e "$ROOT/bin/.native_image" && test ! -L "$ROOT/bin/.native_image"
test ! -e "$ROOT/bin/.{launcher['dll']}.{backup_name}.new" && test ! -L "$ROOT/bin/.{launcher['dll']}.{backup_name}.new"

{chr(10).join(app_row_checks)}

mkdir "$WORK"
actual_members=$(tar -tzf "$ARCHIVE" | LC_ALL=C sort)
test "$actual_members" = {shlex.quote(expected_members)}
tar -xzf "$ARCHIVE" -C "$WORK"
while IFS=' ' read -r rel expected; do
    [ -z "$rel" ] && continue
    candidate="$WORK/$rel"
    test -f "$candidate" && test ! -L "$candidate"
    check_hash "$candidate" "$expected"
done <<'QUIET_PACKAGE_FILES'
{package_hash_rows}
QUIET_PACKAGE_FILES
verify_resource_tree "$WORK/quiet-home" yes

BACKUP="$D/launcher-entry-before.dll"
test ! -e "$BACKUP" && test ! -L "$BACKUP"
( set -C; cat "$ENTRY" > "$BACKUP" )
check_hash "$BACKUP" {shlex.quote(entry_before_hash)}

cp -R "$WORK/quiet-home" "$ROOT/res/"
find "$RESOURCE" -type d -exec chown "$OWNER" {{}} \\;
find "$RESOURCE" -type d -exec chmod 755 {{}} \\;
find "$RESOURCE" -type f -exec chown "$OWNER" {{}} \\;
find "$RESOURCE" -type f -exec chmod 644 {{}} \\;
find "$RESOURCE" -exec chsmack -a "$SMACK" {{}} \\;
verify_resource_tree "$RESOURCE" yes
while IFS= read -r rel; do
    [ -z "$rel" ] && continue
    path="$RESOURCE/$rel"
    test "$(stat -c '%u:%g' "$path")" = "$OWNER"
    test "$(stat -c '%a' "$path")" = 644
    check_label "$SMACK" "$path"
done <<'QUIET_RESOURCE_FILES'
{_literal_lines(_resource_shape(package_files)[0])}
QUIET_RESOURCE_FILES
test "$(stat -c '%u:%g' "$RESOURCE")" = "$OWNER"
test "$(stat -c '%a' "$RESOURCE")" = 755
check_label "$SMACK" "$RESOURCE"
while IFS= read -r rel; do
    [ -z "$rel" ] && continue
    path="$RESOURCE/$rel"
    test "$(stat -c '%u:%g' "$path")" = "$OWNER"
    test "$(stat -c '%a' "$path")" = 755
    check_label "$SMACK" "$path"
done <<'QUIET_RESOURCE_DIRS'
{_literal_lines(_resource_shape(package_files)[1])}
QUIET_RESOURCE_DIRS

NEW_ENTRY="$ROOT/bin/.{launcher['dll']}.{backup_name}.new"
( set -C; cat "$WORK/{shlex.quote(launcher['dll'])}" > "$NEW_ENTRY" )
chown "$OWNER" "$NEW_ENTRY"
chmod "$ENTRY_MODE" "$NEW_ENTRY"
chsmack -a "$SMACK" "$NEW_ENTRY"
test "$(stat -c '%u:%g' "$NEW_ENTRY")" = "$OWNER"
test "$(stat -c '%a' "$NEW_ENTRY")" = {entry_mode_q}
check_label "$SMACK" "$NEW_ENTRY"
check_hash "$NEW_ENTRY" {shlex.quote(next(item['sha256'] for item in package_files if item['path'] == launcher['dll']))}
mv "$NEW_ENTRY" "$ENTRY"
test "$(stat -c '%u:%g' "$ENTRY")" = "$OWNER"
test "$(stat -c '%a' "$ENTRY")" = {entry_mode_q}
check_label "$SMACK" "$ENTRY"
check_hash "$ENTRY" {shlex.quote(next(item['sha256'] for item in package_files if item['path'] == launcher['dll']))}
echo "Manual launcher files installed for $APP_ID (configured UID $LAUNCHER_UID). Home/Startup and all other routing remain unchanged."
echo 'Review and launch manually through the already available application UI; this plan does not start the app.'
"""


def _restore_script(profile, launcher, package_files, archive_hash, backup_name, stage_name,
                    entry_before_hash, entry_mode, root_mode, backup_files):
    root_q = shlex.quote(launcher["root"])
    entry_q = shlex.quote(f"{launcher['root']}/bin/{launcher['dll']}")
    resource_q = shlex.quote(f"{launcher['root']}/res/quiet-home")
    owner_q = shlex.quote(launcher["owner"])
    smack_q = shlex.quote(launcher["smack_label"])
    root_mode_q, entry_mode_q = shlex.quote(root_mode), shlex.quote(entry_mode)
    backup_q, archive_name_q = shlex.quote(backup_name), shlex.quote(stage_name)
    new_hash = next(item["sha256"] for item in package_files if item["path"] == launcher["dll"])
    files, directories = _resource_shape(package_files)
    expected_files = _literal_lines(files)
    expected_dirs = _literal_lines(directories)
    checks = _backup_checks(backup_files)
    verifier = _resource_verifier(package_files)
    return f"""#!/bin/sh
# Generated exact inverse for one manual launcher phase. It never changes Home/Startup or settings routes.
set -eu
umask 077
test "$(id -u)" = 0
{identity_check(profile['model'], profile['firmware'])}
D=/opt/usr/share/quiet-tizen-backups/{backup_q}
ARCHIVE="$D/{archive_name_q}"
ROOT={root_q}
ENTRY={entry_q}
RESOURCE={resource_q}
OWNER={owner_q}
SMACK={smack_q}
BACKUP="$D/launcher-entry-before.dll"
CACHE="$ROOT/bin/.native_image"
CACHE_BACKUP="$D/launcher-native-image-after"

check_hash() {{
    actual=$(sha256sum "$1" | awk '{{print $1}}')
    test "$actual" = "$2"
}}
check_label() {{
    found=$(chsmack --if-access "$1" --name-only "$2")
    test "$found" = "$2"
}}
{verifier}

test -d "$D" && test ! -L "$D"
{checks}
test -f "$ARCHIVE" && test ! -L "$ARCHIVE"
check_hash "$ARCHIVE" {shlex.quote(archive_hash)}
test -d "$ROOT" && test ! -L "$ROOT"
test "$(readlink -f "$ROOT")" = "$ROOT"
test "$(stat -c '%u:%g' "$ROOT")" = {owner_q}
test "$(stat -c '%a' "$ROOT")" = {root_mode_q}
test -d "$ROOT/res" && test ! -L "$ROOT/res"
test -f "$BACKUP" && test ! -L "$BACKUP"
check_hash "$BACKUP" {shlex.quote(entry_before_hash)}
if [ -e "$ENTRY" ] || [ -L "$ENTRY" ]; then
    test -f "$ENTRY" && test ! -L "$ENTRY"
    test "$(stat -c '%u:%g' "$ENTRY")" = "$OWNER"
    test "$(stat -c '%a' "$ENTRY")" = {entry_mode_q}
    check_label "$SMACK" "$ENTRY"
    current_hash=$(sha256sum "$ENTRY" | awk '{{print $1}}')
    test "$current_hash" = {shlex.quote(new_hash)} || test "$current_hash" = {shlex.quote(entry_before_hash)}
else
    echo 'Configured launcher entry is missing; preserving state for manual recovery.' >&2
    exit 1
fi
if [ -e "$CACHE" ] || [ -L "$CACHE" ]; then
    test -d "$CACHE" && test ! -L "$CACHE"
    test ! -e "$CACHE_BACKUP" && test ! -L "$CACHE_BACKUP"
fi
if [ -e "$RESOURCE" ] || [ -L "$RESOURCE" ]; then
    test -d "$RESOURCE" && test ! -L "$RESOURCE"
    verify_resource_tree "$RESOURCE" no
fi
NEW_RESTORE="$ROOT/bin/.{launcher['dll']}.{backup_name}.restore"
test ! -e "$NEW_RESTORE" && test ! -L "$NEW_RESTORE"

if [ -e "$CACHE" ]; then
    mv "$CACHE" "$CACHE_BACKUP"
fi
if [ -e "$RESOURCE" ]; then
    rm -r "$RESOURCE"
fi
current_hash=$(sha256sum "$ENTRY" | awk '{{print $1}}')
if [ "$current_hash" = {shlex.quote(new_hash)} ]; then
    ( set -C; cat "$BACKUP" > "$NEW_RESTORE" )
    chown "$OWNER" "$NEW_RESTORE"
    chmod "$ENTRY_MODE" "$NEW_RESTORE"
    chsmack -a "$SMACK" "$NEW_RESTORE"
    test "$(stat -c '%u:%g' "$NEW_RESTORE")" = "$OWNER"
    test "$(stat -c '%a' "$NEW_RESTORE")" = {entry_mode_q}
    check_label "$SMACK" "$NEW_RESTORE"
    check_hash "$NEW_RESTORE" {shlex.quote(entry_before_hash)}
    mv "$NEW_RESTORE" "$ENTRY"
fi
check_hash "$ENTRY" {shlex.quote(entry_before_hash)}
test "$(stat -c '%u:%g' "$ENTRY")" = "$OWNER"
test "$(stat -c '%a' "$ENTRY")" = {entry_mode_q}
check_label "$SMACK" "$ENTRY"
echo 'Exact launcher entry and previously absent resource tree restored. Original backup retained.'
"""


def _new_output(path):
    requested = Path(path).expanduser()
    if not requested.is_absolute():
        requested = ROOT / requested
    _reject_symlink_chain(requested)
    private = PRIVATE.resolve(strict=False)
    resolved = requested.resolve(strict=False)
    if requested == PRIVATE or private not in resolved.parents:
        raise ValueError("Plans and private backups must be written below this checkout's .local/.")
    _reject_symlink_chain(requested, PRIVATE)
    if requested.exists() or requested.is_symlink():
        raise ValueError("Output directory already exists; preserve existing plans and backups.")
    return requested


def _write_new(path, data, mode):
    path = Path(path)
    with path.open("xb") as stream:
        stream.write(data)
    path.chmod(mode)


def build_plan(args):
    now = datetime.now(timezone.utc)
    profile_path = _regular_profile_input(args.profile)
    inventory_path = _private_input(args.inventory, "Current inventory")
    snapshot_path = _private_input(args.snapshot, "SQLite .backup snapshot")
    host_path = _private_input(args.host_evidence, "Private launcher-host evidence")
    if not BACKUP_NAME.fullmatch(args.backup_name):
        raise ValueError("Backup name must be a new safe numbered phase identifier.")
    if not STAGE_NAME.fullmatch(args.stage_name):
        raise ValueError("Stage name must be a single safe .tar.gz filename.")
    profile, launcher, config = _profile(profile_path)
    inventory, app_rows, collected = _inventory(inventory_path, profile, launcher, config, now)
    host, host_observed, entry, original_entry, root_mode, entry_mode, backup_files, snapshot_hash = _host_evidence(
        host_path, profile, launcher, snapshot_path, args.backup_name, now)
    if _hash_file(snapshot_path) != snapshot_hash:
        raise ValueError("Package registry snapshot changed while planning; reacquire inputs.")
    snapshot_stat = snapshot_path.stat()
    snapshot_age = (now - datetime.fromtimestamp(snapshot_stat.st_mtime, timezone.utc)).total_seconds()
    if not 0 <= snapshot_age <= MAX_EVIDENCE_AGE_SECONDS:
        raise ValueError("Package registry snapshot is stale or from the future; reacquire it.")
    _snapshot_rows(snapshot_path, app_rows)

    key = profile_key(profile)
    package_path = confined_generated_path(ROOT, "package", key)
    package_files = _package_files(package_path, profile, launcher, config)
    package_root = host["package_root"]
    entry_after_hash = next(item["sha256"] for item in package_files if item["path"] == launcher["dll"])
    out = _new_output(args.output)
    out.mkdir(parents=True, mode=0o700)
    archive_path = out / args.stage_name
    _write_archive(archive_path, package_path, package_files)
    archive_hash = _hash_file(archive_path)

    install = _install_script(
        profile, launcher, app_rows, package_files, archive_hash, args.backup_name,
        args.stage_name, entry["sha256"], entry_mode, root_mode, backup_files,
    )
    restore = _restore_script(
        profile, launcher, package_files, archive_hash, args.backup_name,
        args.stage_name, entry["sha256"], entry_mode, root_mode, backup_files,
    )
    _write_new(out / "install.sh", install.encode("utf-8"), 0o700)
    _write_new(out / "restore.sh", restore.encode("utf-8"), 0o700)
    manifest = {
        "schema": "quiet-tizen.launcher-plan.v1",
        "phase": "manual-launcher-install",
        "model": profile["model"],
        "firmware": profile["firmware"],
        "launcher_app_id": launcher["app_id"],
        "launcher_uid": launcher["uid"],
        "package_root": launcher["root"],
        "package_build_key": key,
        "package_app_row": app_rows[launcher["app_id"]],
        "target_app_rows": {app_id: row for app_id, row in app_rows.items() if app_id != launcher["app_id"]},
        "entry": {
            "path": entry["path"],
            "before_sha256": entry["sha256"],
            "original_backup_file": original_entry.name,
            "after_sha256": entry_after_hash,
            "owner": launcher["owner"],
            "mode": entry_mode,
            "smack_label": launcher["smack_label"],
        },
        "resource_path": f"{launcher['root']}/res/quiet-home",
        "resource_files": [item for item in package_files if item["path"].startswith("quiet-home/")],
        "package_files": package_files,
        "backup_path": host["backup"]["path"],
        "backup_files": backup_files,
        "stage_name": args.stage_name,
        "archive_sha256": archive_hash,
        "snapshot_sha256": snapshot_hash,
        "inventory_sha256": _hash_file(inventory_path),
        "host_evidence_sha256": _hash_file(host_path),
        "profile_sha256": _hash_file(profile_path),
        "inventory_collected_at": inventory["collected_at"],
        "host_observed_at": host_observed.isoformat().replace("+00:00", "Z"),
        "package_root_owner": package_root["owner"],
        "package_root_mode": root_mode,
        "entry_owner": entry["owner"],
        "entry_mode": entry_mode,
        "entry_smack_label": entry["smack_label"],
        "selectors_changed": [],
        "settings_routes_changed": [],
        "registry_flags_changed": [],
        "notes": [
            "Offline plan only; no device evidence or deployment approval is created.",
            "Manual launch phase only; Home/Startup, settings, cleanup, and package flags remain unchanged.",
            "Keep snapshot, host evidence, staged package, and generated inverse together.",
        ],
    }
    _write_new(out / "before.json", (json.dumps(manifest, indent=2) + "\n").encode("utf-8"), 0o600)
    _write_new(out / "install.sh.sha256", (_hash_file(out / "install.sh") + "  install.sh\n").encode(), 0o600)
    _write_new(out / "restore.sh.sha256", (_hash_file(out / "restore.sh") + "  restore.sh\n").encode(), 0o600)
    if _hash_file(snapshot_path) != snapshot_hash or _hash_file(inventory_path) != manifest["inventory_sha256"] or _hash_file(host_path) != manifest["host_evidence_sha256"]:
        raise ValueError("A private plan input changed while the plan was being generated.")
    return out, manifest


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True, help="Exact model/firmware profile; no default is safe")
    parser.add_argument("--inventory", type=Path, required=True, help="Fresh private quiet-tizen.inventory.v1 evidence")
    parser.add_argument("--snapshot", type=Path, required=True, help="Fresh immutable package_app_info SQLite .backup under .local/")
    parser.add_argument("--host-evidence", type=Path, required=True, help="Fresh private package path/UID/hash/SMACK/snapshot evidence")
    parser.add_argument("--backup-name", required=True, help="New unique numbered private backup phase name")
    parser.add_argument("--stage-name", required=True, help="Safe archive filename to stage beneath that backup directory")
    parser.add_argument("--output", type=Path, required=True, help="New plan directory below .local/")
    args = parser.parse_args()
    try:
        out, manifest = build_plan(args)
    except (OSError, ValueError, KeyError, sqlite3.Error, tarfile.TarError) as error:
        parser.error(str(error))
    print(f"Generated offline manual-launcher plan: {out.relative_to(ROOT)}")
    print(f"Exact profile: {manifest['model']} / {manifest['firmware']}; phase: {manifest['phase']}")
    print("Review install.sh, restore.sh, before.json, and the exact staged archive together.")
    print("This plan is not device qualification, deployment authorization, or proof of a hardware gate.")


if __name__ == "__main__":
    main()
