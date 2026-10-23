#!/usr/bin/env python3
"""Generate a reviewed XF86More route update and exact inverse from fresh evidence.

XF86SysMenu remains a protected native route: inventory and snapshots bind it,
the generated transaction guards it, and no generated UPDATE targets it.
This planner does not install or replace an application assembly or run on a TV.
"""
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

from profiles import load_profile, require_capability

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = (ROOT / ".local").resolve()
REGISTRY = "/opt/dbspace/.app-context.db"
TABLE = "key_filter"
FIELDS = ("appid", "key", "target_selector", "args")
ALLOWED_KEYS = frozenset(("XF86More",))
INVENTORY_KEYS = frozenset(("XF86More", "XF86SysMenu"))


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_json(path, label):
    try:
        with Path(path).open("r", encoding="utf-8") as stream:
            return json.load(stream, object_pairs_hook=_unique_pairs)
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


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _timestamp(value, label):
    if not isinstance(value, str):
        raise ValueError(f"{label} needs a timezone-aware observation time.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} needs a timezone-aware observation time.") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{label} needs a timezone-aware observation time.")


def _inventory_identity(inventory, profile):
    if not isinstance(inventory, dict) or inventory.get("schema") != "quiet-tizen.inventory.v1":
        raise ValueError("A current quiet-tizen.inventory.v1 record is required.")
    _timestamp(inventory.get("collected_at"), "Current inventory")
    device = inventory.get("device")
    if not isinstance(device, dict):
        raise ValueError("Current inventory has no device identity.")
    for field in ("model", "firmware"):
        fact = device.get(field)
        if not isinstance(fact, dict) or fact.get("status") != "PROVEN":
            raise ValueError(f"Current inventory {field} is not PROVEN.")
        if fact.get("value") != profile.get(field):
            raise ValueError(f"Current inventory {field} does not match the exact profile.")
        _timestamp(fact.get("observed_at", inventory.get("collected_at")), f"Current {field}")
    return inventory


def _columns_from_inventory(data):
    columns = data.get("columns")
    if not isinstance(columns, list) or not columns:
        raise ValueError("Current key_filter evidence has no complete column schema.")
    names = []
    normalized = []
    for column in columns:
        if not isinstance(column, dict) or set(column) != {
                "cid", "name", "type", "not_null", "default", "primary_key"}:
            raise ValueError("Current key_filter column schema is malformed.")
        name = column["name"]
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError("Current key_filter column name is invalid.")
        names.append(name)
        normalized.append((column["cid"], name, column["type"], column["not_null"],
                           column["default"], column["primary_key"]))
    if len(names) != len(set(names)) or not set(FIELDS) <= set(names):
        raise ValueError("Current key_filter schema has duplicate columns or lacks semantic fields.")
    if [item[0] for item in normalized] != list(range(len(normalized))):
        raise ValueError("Current key_filter schema column order is incomplete.")
    return normalized


def _validate_inventory_rows(inventory, routes):
    try:
        envelope = inventory["databases"]["key_filter"]
        data = envelope["value"]
    except (KeyError, TypeError) as error:
        raise ValueError("Current inventory is missing key_filter evidence.") from error
    if not isinstance(envelope, dict) or envelope.get("status") != "PROVEN":
        raise ValueError("Current key_filter evidence is not PROVEN.")
    if not isinstance(data, dict) or data.get("table") != TABLE:
        raise ValueError("Current key_filter evidence has an unexpected table.")
    if data.get("complete") is not True:
        raise ValueError("Current key_filter evidence is incomplete; settings routing remains blocked.")
    rows = data.get("rows")
    if not isinstance(rows, list) or type(data.get("row_count")) is not int or data["row_count"] != len(rows):
        raise ValueError("Current key_filter row count is incomplete or inconsistent.")
    selected_keys = data.get("selected_keys")
    if not isinstance(selected_keys, list) or set(selected_keys) != INVENTORY_KEYS:
        raise ValueError("Current key_filter evidence must cover both Settings/More and native System Menu keys.")
    _columns_from_inventory(data)
    observed = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"app_id", "key", "target_selector", "args"}:
            raise ValueError("Current key_filter row is malformed.")
        pair = (row["app_id"], row["key"])
        if not isinstance(pair[0], str) or not isinstance(pair[1], str) or pair[1] not in INVENTORY_KEYS:
            raise ValueError("Current key_filter row is outside the bounded inventory key set.")
        if pair in observed:
            raise ValueError(f"Current key_filter has duplicate semantic route {pair[0]} / {pair[1]}.")
        for field in ("target_selector", "args"):
            value = row[field]
            if (not isinstance(value, str) or len(value) > 512 or
                    any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in value)):
                raise ValueError(f"Current key_filter {field} is not a bounded printable string.")
        observed[pair] = (row["target_selector"], row["args"])
    expected = {(route["match"]["appid"], route["match"]["key"]) for route in routes}
    if len(expected) != len(routes):
        raise ValueError("Profile has duplicate semantic settings routes.")
    return observed, _columns_from_inventory(data), data.get("schema_sql")


def _snapshot_rows(snapshot, expected_columns, expected_schema_sql):
    try:
        uri = Path(snapshot).as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            if conn.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise ValueError("Immutable settings snapshot quick_check failed.")
            schema = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchall()
            if len(schema) != 1 or not isinstance(schema[0][0], str):
                raise ValueError("Snapshot does not contain exactly one key_filter table definition.")
            if expected_schema_sql and schema[0][0] != expected_schema_sql:
                raise ValueError("Snapshot key_filter definition differs from current inventory schema.")
            actual_columns = conn.execute(f"PRAGMA table_info({TABLE})").fetchall()
            columns = [(int(cid), name, col_type, int(not_null), default, int(primary_key))
                       for cid, name, col_type, not_null, default, primary_key in actual_columns]
            if columns != expected_columns:
                raise ValueError("Snapshot key_filter columns differ from current inventory evidence.")
            before = {}
            placeholders = ",".join("?" for _ in INVENTORY_KEYS)
            found = conn.execute(
                f"SELECT appid,key,target_selector,args FROM key_filter WHERE key IN ({placeholders})",
                tuple(sorted(INVENTORY_KEYS)),
            ).fetchall()
            for appid, key, selector, args in found:
                pair = (appid, key)
                if (not isinstance(appid, str) or key not in INVENTORY_KEYS or pair in before):
                    raise ValueError("Snapshot contains a malformed or duplicate selected semantic route.")
                for field, value in (("target_selector", selector), ("args", args)):
                    if (not isinstance(value, str) or len(value) > 512 or
                            any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in value)):
                        raise ValueError(f"Snapshot {field} is not a bounded printable string.")
                before[pair] = (selector, args)
            return before
    except sqlite3.Error as error:
        raise ValueError(f"Cannot verify settings snapshot: {error}") from error


def _sql_literal(value):
    if not isinstance(value, str) or len(value) > 512 or any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in value):
        raise ValueError("Settings value is not a bounded printable string.")
    return "'" + value.replace("'", "''") + "'"


def _pair_sql(appid, key):
    return f"appid={_sql_literal(appid)} AND key={_sql_literal(key)}"


def make_sql(routes, before, restore=False):
    """Create an atomic exact-semantic transaction; every guard precedes every update."""
    lines = ["BEGIN IMMEDIATE;", "CREATE TEMP TABLE quiet_guard(ok INTEGER CHECK(ok=1));"]
    expected_now = {}
    desired = {}
    route_pairs = {(route["match"]["appid"], route["match"]["key"]) for route in routes}
    for route in routes:
        pair = (route["match"]["appid"], route["match"]["key"])
        old = before[pair]
        after = (route["after"]["target_selector"], route["after"]["args"])
        expected_now[pair], desired[pair] = (after, old) if restore else (old, after)
        condition = " AND ".join(f"{field} IS {_sql_literal(value)}"
                                 for field, value in zip(("target_selector", "args"), expected_now[pair]))
        lines.append(f"INSERT INTO quiet_guard SELECT count(*)=1 FROM key_filter WHERE {_pair_sql(*pair)} AND {condition};")
    # Guard every selected but unmodified key route. In particular XF86SysMenu
    # remains bound to the native Settings app for apply and inverse phases.
    for pair, values in sorted(before.items()):
        if pair in route_pairs:
            continue
        condition = " AND ".join(f"{field} IS {_sql_literal(value)}"
                                 for field, value in zip(("target_selector", "args"), values))
        lines.append(f"INSERT INTO quiet_guard SELECT count(*)=1 FROM key_filter WHERE {_pair_sql(*pair)} AND {condition};")
    for route in routes:
        pair = (route["match"]["appid"], route["match"]["key"])
        values = desired[pair]
        lines.append(f"UPDATE key_filter SET target_selector={_sql_literal(values[0])},args={_sql_literal(values[1])} "
                     f"WHERE {_pair_sql(*pair)} AND target_selector IS {_sql_literal(expected_now[pair][0])} "
                     f"AND args IS {_sql_literal(expected_now[pair][1])};")
        lines.append("INSERT INTO quiet_guard SELECT changes()=1;")
    lines.extend(("DROP TABLE quiet_guard;", "COMMIT;"))
    return "\n".join(lines) + "\n"


def _identity_check(model, firmware):
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


def make_shell(sql, model, firmware, registry=REGISTRY):
    quoted = shlex.quote(registry)
    return f"""#!/bin/sh
# Generated offline from an exact profile, current inventory, and immutable SQLite .backup.
set -eu
test "$(id -u)" = 0
{_identity_check(model, firmware)}
test -f {quoted} && test ! -L {quoted}
test "$(sqlite3 -readonly {quoted} 'PRAGMA quick_check;')" = ok
sqlite3 -bail {quoted} <<'QUIET_SQL'
{sql}QUIET_SQL
test "$(sqlite3 -readonly {quoted} 'PRAGMA quick_check;')" = ok
echo 'Exact semantic settings routes updated. No application files or unrelated routes were changed.'
"""


def _validate_routes(profile):
    bridge = profile.get("settings_bridge")
    if not isinstance(bridge, dict) or bridge.get("registry") != REGISTRY or bridge.get("table") != TABLE:
        raise ValueError("Profile lacks the exact approved settings registry schema.")
    routes = bridge.get("routes")
    if not isinstance(routes, list) or not routes:
        raise ValueError("Profile has no explicitly observed settings routes.")
    for route in routes:
        if not isinstance(route, dict) or set(route) != {"match", "after"}:
            raise ValueError("Malformed settings route.")
        match, after = route["match"], route["after"]
        if not isinstance(match, dict) or set(match) != {"appid", "key"}:
            raise ValueError("Settings route needs exact observed appid and key fields.")
        if not isinstance(after, dict) or set(after) != {"target_selector", "args"}:
            raise ValueError("Settings route needs an explicit target selector and request.")
        if match.get("key") not in ALLOWED_KEYS:
            raise ValueError("Only explicitly observed Settings/More keys may be planned.")
        if after.get("target_selector") != "package:" + profile["launcher"]["app_id"]:
            raise ValueError("Settings route target differs from the profile's exact launcher app ID.")
        _sql_literal(after.get("target_selector"))
        _sql_literal(after.get("args"))
    return routes


def validate_settings_request(profile_path, inventory_path, snapshot_path):
    profile = load_profile(profile_path)
    require_capability(profile, "settings_bridge")
    require_capability(profile, "rollback_ready")
    routes = _validate_routes(profile)
    inventory = _inventory_identity(_read_json(inventory_path, "current inventory"), profile)
    observed, columns, schema_sql = _validate_inventory_rows(inventory, routes)
    before = _snapshot_rows(snapshot_path, columns, schema_sql)
    for route in routes:
        pair = (route["match"]["appid"], route["match"]["key"])
        if pair not in observed:
            raise ValueError(f"{pair[0]} / {pair[1]}: current inventory requires exactly one semantic route.")
    if observed != before:
        raise ValueError("Current key_filter inventory differs from the immutable snapshot, including protected routes.")
    return profile, inventory, routes, before


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True, help="Exact model/firmware profile; no default is safe")
    parser.add_argument("--inventory", type=Path, required=True, help="Fresh read-only quiet-tizen.inventory.v1 evidence")
    parser.add_argument("--snapshot", type=Path, required=True, help="Immutable .backup database under .local/")
    parser.add_argument("--output", type=Path, required=True, help="New plan directory below .local/")
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        parser.error("Output directory already exists; preserve prior rollback records.")
    out = args.output.resolve()
    if PRIVATE not in out.parents:
        parser.error("Plans contain private rollback state; write them below this checkout's .local/.")
    try:
        inventory_path = _private_input(args.inventory, "Current inventory")
        snapshot_path = _private_input(args.snapshot, "SQLite .backup snapshot")
        profile, inventory, routes, before = validate_settings_request(
            args.profile, inventory_path, snapshot_path)
    except (OSError, sqlite3.Error, ValueError) as error:
        parser.error(str(error))
    out.mkdir(parents=True, mode=0o700)
    registry = profile["settings_bridge"]["registry"]
    for action in ("apply", "restore"):
        sql = make_sql(routes, before, restore=(action == "restore"))
        (out / f"{action}.sql").write_text(sql, encoding="utf-8")
        (out / f"{action}.sh").write_text(
            make_shell(sql, profile["model"], profile["firmware"], registry), encoding="utf-8")
    manifest = {
        "model": profile["model"], "firmware": profile["firmware"],
        "profile_sha256": sha256_file(args.profile),
        "inventory_sha256": sha256_file(inventory_path),
        "snapshot_sha256": sha256_file(snapshot_path),
        "collected_at": inventory["collected_at"],
        "registry": registry,
        "table": TABLE,
        "routes": [
            {"appid": route["match"]["appid"], "key": route["match"]["key"],
             "before": dict(zip(("target_selector", "args"), before[(route["match"]["appid"], route["match"]["key"])])),
             "after": route["after"]}
            for route in routes
        ],
        "preserved_routes": [
            {"appid": appid, "key": key,
             "target_selector": values[0], "args": values[1]}
            for (appid, key), values in sorted(before.items())
            if (appid, key) not in {
                (route["match"]["appid"], route["match"]["key"]) for route in routes
            }
        ],
        "apply_sha256": sha256_file(out / "apply.sh"),
        "restore_sha256": sha256_file(out / "restore.sh"),
    }
    (out / "before.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Generated an exact {len(routes)}-route plan in {out.relative_to(ROOT)}.")
    print("Review the apply and inverse scripts. These are offline plans, not deployment authorization.")


if __name__ == "__main__":
    main()
