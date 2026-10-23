#!/usr/bin/env python3
"""Generate reviewed apply/restore SQL from YOUR fresh, consistent registry snapshot."""
import argparse
from contextlib import closing
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3

from profiles import load_profile, require_capability, PRESERVATION

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = (ROOT / ".local").resolve()
FIELDS = ("app_disable", "app_onboot", "app_autorestart")
DISABLED = ("true", "false", "false")
DB = "/opt/dbspace/.pkgmgr_parser.db"


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unique_json_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"Duplicate JSON key: {key}")
        value[key] = item
    return value


def _read_json(path, label):
    try:
        with Path(path).open("r", encoding="utf-8") as stream:
            return json.load(stream, object_pairs_hook=_unique_json_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read {label}: {error}") from error


def _private_input(path, label):
    original = Path(path).expanduser()
    if original.is_symlink():
        raise ValueError(f"{label} must be a regular, non-symlink file below .local/.")
    resolved = original.resolve()
    if PRIVATE not in resolved.parents or not resolved.is_file():
        raise ValueError(f"{label} must be a regular file below this checkout's .local/.")
    return resolved


def _timestamp(value, name):
    if not isinstance(value, str):
        raise ValueError(f"{name} is missing a timezone-aware timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{name} is missing a timezone-aware timestamp.") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{name} is missing a timezone-aware timestamp.")
    return parsed


def _evidence_source(evidence):
    source = evidence.get("source")
    expected = evidence.get("source_sha256")
    if not isinstance(source, str) or not source or Path(source).is_absolute():
        raise ValueError("Cleanup dependency evidence source must be a relative file path.")
    if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise ValueError("Cleanup dependency evidence needs a lowercase SHA-256.")
    path = (ROOT / source).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError as error:
        raise ValueError("Cleanup dependency evidence source must stay inside this checkout.") from error
    if not path.is_file() or sha256_file(path) != expected:
        raise ValueError("Cleanup dependency evidence source is missing or its SHA-256 changed.")
    _timestamp(evidence.get("observed_at"), "Cleanup dependency evidence")
    return path


def _inventory_identity(inventory, profile):
    if not isinstance(inventory, dict) or inventory.get("schema") != "quiet-tizen.inventory.v1":
        raise ValueError("A current quiet-tizen.inventory.v1 record is required.")
    _timestamp(inventory.get("collected_at"), "Current inventory")
    device = inventory.get("device")
    if not isinstance(device, dict):
        raise ValueError("Current inventory has no device identity evidence.")
    for field in ("model", "firmware"):
        evidence = device.get(field)
        if not isinstance(evidence, dict) or evidence.get("status") != "PROVEN":
            raise ValueError(f"Current inventory {field} is not PROVEN.")
        if evidence.get("value") != profile[field]:
            raise ValueError(f"Current inventory {field} does not match the exact profile.")
        _timestamp(evidence.get("observed_at", inventory.get("collected_at")), f"Current {field} evidence")
    return inventory


def _current_app_rows(inventory, app_ids):
    try:
        report = inventory["databases"]["package_app_info"]
        data = report["value"]
        rows = data["rows"]
    except (KeyError, TypeError) as error:
        raise ValueError("Current inventory is missing package_app_info rows.") from error
    if not isinstance(report, dict) or report.get("status") != "PROVEN" or not isinstance(data, dict) or not isinstance(rows, list):
        raise ValueError("Current package_app_info rows are not PROVEN.")
    if data.get("complete") is not True:
        raise ValueError("Current package_app_info inventory is incomplete; cleanup remains blocked.")
    result = {}
    for app in app_ids:
        found = [row for row in rows if isinstance(row, dict) and row.get("app_id") == app]
        if len(found) != 1:
            raise ValueError(f"{app}: current inventory requires exactly one matching row, found {len(found)}.")
        result[app] = tuple(found[0].get(field) for field in FIELDS)
    return result


def validate_cleanup_request(profile_path, inventory_path, snapshot_path, groups, owner_opt_ins):
    profile = load_profile(profile_path)
    require_capability(profile, "cleanup")
    require_capability(profile, "rollback_ready")
    if profile.get("registry") != DB:
        raise ValueError(f"Cleanup planning requires the exact approved registry path {DB}.")
    if not groups or len(groups) != len(set(groups)):
        raise ValueError("Select one or more unique cleanup groups.")
    if not owner_opt_ins or len(owner_opt_ins) != len(set(owner_opt_ins)):
        raise ValueError("Repeat --owner-opt-in once for every selected group.")
    if set(owner_opt_ins) != set(groups):
        raise ValueError("Owner opt-in groups must match the selected cleanup groups exactly.")
    ids = []
    evidence = {}
    for name in groups:
        group = profile.get("groups", {}).get(name)
        if group is None:
            raise ValueError(f"Unknown cleanup group: {name}")
        selected = group.get("app_ids")
        if not selected:
            raise ValueError(f"Cleanup group {name} has no approved app IDs.")
        dependency = group.get("dependency_evidence")
        if not isinstance(dependency, dict):
            raise ValueError(f"Cleanup group {name} has no dependency evidence.")
        if (dependency.get("status") != "PROVEN" or dependency.get("model") != profile["model"] or
                dependency.get("firmware") != profile["firmware"] or
                set(dependency.get("preserves", [])) != PRESERVATION):
            raise ValueError(f"Cleanup group {name} lacks exact-identity dependency preservation evidence.")
        evidence[name] = {
            "source": dependency.get("source"),
            "source_sha256": dependency.get("source_sha256"),
            "evidence_path": str(_evidence_source(dependency).relative_to(ROOT)),
        }
        ids.extend(selected)
    if len(ids) != len(set(ids)):
        raise ValueError("Selected cleanup groups contain duplicate application IDs.")
    inventory = _inventory_identity(_read_json(inventory_path, "current inventory"), profile)
    current = _current_app_rows(inventory, ids)
    rows = read_rows(Path(snapshot_path), ids)
    snapshot_values = {app: values for app, values in rows}
    for app in ids:
        if current[app] != snapshot_values[app]:
            raise ValueError(f"{app}: current inventory and snapshot registry values differ; take a fresh snapshot.")
    return profile, rows, inventory, evidence


def literal(value):
    if value is None:
        return "NULL"
    if value not in ("true", "false"):
        raise ValueError(f"Unexpected registry boolean representation: {value!r}")
    return "'" + value + "'"


def validate_id(app_id):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", app_id):
        raise ValueError("Invalid application ID.")
    return "'" + app_id + "'"


def read_rows(database, ids):
    # Read only. A caller must use SQLite's .backup, never copy a live WAL database.
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        if conn.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise ValueError("Snapshot integrity check failed.")
        rows = []
        for app_id in ids:
            validate_id(app_id)
            found = conn.execute(
                "SELECT app_disable,app_onboot,app_autorestart FROM package_app_info WHERE app_id=?",
                (app_id,),
            ).fetchall()
            if len(found) != 1:
                raise ValueError(f"{app_id}: expected one row, found {len(found)}. Review this model's inventory.")
            for value in found[0]:
                literal(value)
            rows.append((app_id, tuple(found[0])))
        return rows


def make_sql(rows, restore=False):
    # Guard every observed field before changing anything. SQLite rolls back the
    # transaction if a guard fails and the sqlite3 -bail connection closes.
    lines = ["BEGIN IMMEDIATE;", "CREATE TEMP TABLE quiet_guard(ok INTEGER CHECK(ok=1));"]
    for app_id, original in rows:
        expected = DISABLED if restore else original
        conditions = " AND ".join(f"{key} IS {literal(val)}" for key, val in zip(FIELDS, expected))
        lines.append(f"INSERT INTO quiet_guard SELECT count(*)=1 FROM package_app_info "
                     f"WHERE app_id={validate_id(app_id)} AND {conditions};")
    for app_id, original in rows:
        values = original if restore else DISABLED
        assignments = ",".join(f"{key}={literal(val)}" for key, val in zip(FIELDS, values))
        lines.append(f"UPDATE package_app_info SET {assignments} WHERE app_id={validate_id(app_id)};")
    lines += ["DROP TABLE quiet_guard;", "COMMIT;"]
    return "\n".join(lines) + "\n"


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


def make_shell(sql, model, firmware, db=DB):
    quoted = shlex.quote(db)
    return f"""#!/bin/sh
# Generated from the operator's snapshot. Run through tools/tv.py exec --script.
set -eu
test "$(id -u)" = 0
{identity_check(model, firmware)}
test -f {quoted} && test ! -L {quoted}
test "$(sqlite3 -readonly {quoted} 'PRAGMA quick_check;')" = ok
sqlite3 -bail {quoted} <<'QUIET_SQL'
{sql}QUIET_SQL
test "$(sqlite3 -readonly {quoted} 'PRAGMA quick_check;')" = ok
echo 'Registry transaction complete. This does not terminate already-running processes.'
"""


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--snapshot", type=Path, required=True)
    p.add_argument("--inventory", type=Path, required=True,
                   help="Fresh read-only quiet-tizen.inventory.v1 evidence for this exact TV")
    p.add_argument("--profile", type=Path, required=True, help="Exact model/firmware profile; no default is safe")
    p.add_argument("--groups", nargs="+", required=True)
    p.add_argument("--owner-opt-in", action="append", required=True,
                   help="Explicitly opt in to one selected cleanup group; repeat for every group")
    p.add_argument("--output", type=Path, required=True, help="New directory below .local/")
    args = p.parse_args()
    out = args.output.resolve()
    if PRIVATE not in out.parents:
        p.error("Plans contain private rollback state; write them below this checkout's .local/.")
    if args.output.exists() or args.output.is_symlink():
        p.error("Output directory already exists; preserve previous rollback records.")
    try:
        inventory_path = _private_input(args.inventory, "Current inventory")
        snapshot_path = _private_input(args.snapshot, "SQLite .backup snapshot")
        profile, rows, inventory, group_evidence = validate_cleanup_request(
            args.profile, inventory_path, snapshot_path, args.groups, args.owner_opt_in)
    except (OSError, sqlite3.Error, ValueError) as error:
        p.error(str(error))
    out.mkdir(parents=True, mode=0o700)
    for action in ("apply", "restore"):
        sql = make_sql(rows, restore=action == "restore")
        (out / f"{action}.sql").write_text(sql)
        (out / f"{action}.sh").write_text(make_shell(sql, profile["model"], profile["firmware"]))
    manifest = {"model": profile["model"], "firmware": profile["firmware"], "groups": args.groups,
                "snapshot_sha256": sha256_file(snapshot_path),
                "inventory_sha256": sha256_file(inventory_path),
                "profile_sha256": sha256_file(args.profile),
                "collected_at": inventory["collected_at"],
                "dependency_evidence": group_evidence,
                "apps": [{"app_id": app, **dict(zip(FIELDS, vals))} for app, vals in rows]}
    (out / "before.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Generated {len(rows)} targeted changes and inverse operations in {out.relative_to(ROOT)}.")
    print("Review apply.sql and restore.sql. Preserve the inventory, source-evidence and snapshot hashes.")
    print("These plans only edit registry flags; markers, running apps and preferences are separate phases.")


if __name__ == "__main__":
    main()
