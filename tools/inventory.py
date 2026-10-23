"""Bounded read-only inventory through an explicit Samsung SDB target."""
from __future__ import annotations

from datetime import datetime, timezone
import ipaddress
import re
import secrets
import shlex
from typing import Any

SCHEMA = "quiet-tizen.inventory.v1"
COMMAND_TIMEOUT = 4.0
MAX_OUTPUT_BYTES = 128 * 1024
MAX_PACKAGE_ROWS = 256
MAX_KEY_FILTER_ROWS = 128
_FIRMWARE_RE = re.compile(r"T-[A-Z0-9]+-[0-9]{4}-[0-9]{4}\.[0-9]+")
_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{2,63}")
_ASSEMBLIES = ("Tizen.NUI.dll", "Tizen.Applications.Common.dll", "Tizen.System.dll", "Tizen.dll")
_TOOLS = (
    "sqlite3", "vconftool", "dotnet", "getcap", "tar", "systemctl",
    "base64", "sha256sum", "mktemp", "gzip", "find", "readlink", "awk", "sed",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_firmware(raw: str) -> str | None:
    """Return exactly one unambiguous Tizen firmware Version field."""
    if not isinstance(raw, str):
        return None
    values = []
    for line in raw.splitlines():
        match = re.fullmatch(
            r"\s*(?:Version|FirmwareVersion)\s*=\s*(T-[A-Z0-9]+-[0-9]{4}-[0-9]{4}\.[0-9]+);?\s*",
            line,
        )
        if match and _FIRMWARE_RE.fullmatch(match.group(1)):
            values.append(match.group(1))
        elif re.fullmatch(r"\s*(?:Version|FirmwareVersion)\s*=.*", line):
            return None
    return values[0] if len(values) == 1 else None


def _parse_model(raw: str) -> str | None:
    values = []
    for line in raw.splitlines():
        match = re.fullmatch(r"\s*ModelName\s*=\s*(.*?)\s*", line)
        if match and _MODEL_RE.fullmatch(match.group(1)):
            values.append(match.group(1))
        elif re.fullmatch(r"\s*ModelName\s*=.*", line):
            return None
    return values[0] if len(values) == 1 else None


def _field(value: Any = None, *, status: str = "UNKNOWN", source: str | None = None,
           observed_at: str | None = None, error: str | None = None) -> dict[str, Any]:
    result = {"status": status, "value": value, "source": source, "observed_at": observed_at}
    if error:
        result["error"] = error
    return result


def _unknown_inventory(collected_at: str) -> dict[str, Any]:
    def unknown(value: Any = None) -> dict[str, Any]:
        return _field(value)

    return {
        "schema": SCHEMA,
        "collected_at": collected_at,
        "consistency": "sequential_non_atomic",
        "target": {"serial": None, "status": "UNKNOWN", "source": None, "observed_at": None},
        "device": {"model": unknown(), "firmware": unknown()},
        "platform": {
            "kernel": unknown(), "os_id": unknown(), "os_version_id": unknown(),
            "runtime_paths": unknown([]),
            "runtime_files": unknown({
                "libcoreclr.so": [], "System.Private.CoreLib.dll": [],
            }),
        },
        "framework": {"assemblies": {name: unknown() for name in _ASSEMBLIES}},
        "capabilities": {
            "tools": {name: unknown() for name in _TOOLS},
            "file_capabilities": unknown([]),
        },
        "selectors": {"home_appid": unknown(), "startup_appid": unknown()},
        "databases": {
            "package_app_info": unknown({
                "table": "package_app_info", "schema_sql": None, "columns": [],
                "rows": [], "row_count": None, "complete": False,
            }),
            "key_filter": unknown({
                "table": "key_filter", "schema_sql": None, "columns": [],
                "rows": [], "row_count": None, "complete": False,
                "selected_keys": ["XF86More", "XF86SysMenu"],
            }),
        },
        "write_qualification": {
            "status": "UNKNOWN", "eligible": False,
            "reason": "Inventory records evidence only; it does not qualify or authorize writes.",
        },
        "errors": [],
    }


def _wrapped(name: str, body: str, token: str) -> tuple[str, str, str]:
    begin = f"__QTINV_{name}_{token}_BEGIN__"
    end = f"__QTINV_{name}_{token}_END__"
    body_complete = f"__QTINV_{name}_{token}_BODY_COMPLETE__"
    # A nonzero body exit omits the inner marker; output is buffered under a hard cap.
    script = (
        "set -e\n"
        f"printf '%s\\n' '{begin}'\n"
        "{ /bin/sh -c " + shlex.quote("set -e\n" + body) + " && "
        "printf '%s\\n' '" + body_complete + "'; } 2>/dev/null | "
        "awk -v complete='" + body_complete + "' -v limit=" + str(MAX_OUTPUT_BYTES) +
        " '{ if ($0 == complete) { ended=1; next }; if (ended) bad=1; "
        "n += length($0) + 1; if (n > limit || NR > 10000) bad=1; "
        "if (!bad) lines[++count]=$0 } END { if (!ended || bad) exit 76; "
        "for (i=1; i<=count; i++) print lines[i] }'\n"
        f"printf '%s\\n' '{end}'\n"
    )
    return script, begin, end


def _run_read(client: Any, name: str, body: str) -> tuple[str | None, str | None, str]:
    observed_at = _now()
    token = secrets.token_hex(8)
    script, begin, end = _wrapped(name, body, token)
    try:
        result = client.run(("-s", client.serial, "shell", script),
                            check=False, timeout=COMMAND_TIMEOUT)
    except Exception:
        return None, "COMMAND_ERROR", observed_at
    stdout = getattr(result, "stdout", None)
    stderr = getattr(result, "stderr", "")
    if not isinstance(stdout, str) or len(stdout.encode("utf-8", errors="replace")) > MAX_OUTPUT_BYTES + 256:
        return None, "OUTPUT_LIMIT", observed_at
    if stderr and len(str(stderr).encode("utf-8", errors="replace")) > 4096:
        return None, "OUTPUT_LIMIT", observed_at
    if getattr(result, "returncode", None) != 0:
        return None, "COMMAND_EXIT_NONZERO", observed_at
    if stdout.count(begin) != 1 or stdout.count(end) != 1:
        return None, "MARKER_INVALID", observed_at
    match = re.fullmatch(re.escape(begin) + r"\n(.*?)\n" + re.escape(end) + r"\n?",
                         stdout, re.DOTALL)
    if not match:
        return None, "MARKER_INVALID", observed_at
    return match.group(1), None, observed_at


def _error(inventory: dict[str, Any], operation: str, code: str) -> None:
    inventory["errors"].append({"operation": operation, "code": code, "observed_at": _now()})


def _set(field: dict[str, Any], value: Any, at: str) -> None:
    field.clear()
    field.update(_field(value, status="PROVEN",
                        source="SDB explicit-target shell; bounded sentinel-verified output",
                        observed_at=at))


def _fail(field: dict[str, Any], at: str, code: str,
          inventory: dict[str, Any], operation: str) -> None:
    field.clear()
    field.update(_field(error=code, source="SDB explicit-target read", observed_at=at))
    _error(inventory, operation, code)


def _parse_columns(lines: list[str], prefix: str) -> list[dict[str, Any]]:
    result = []
    for line in lines:
        part = line.split("|")
        if len(part) != 7 or part[0] != prefix:
            continue
        try:
            result.append({"cid": int(part[1]), "name": part[2], "type": part[3],
                           "not_null": part[4] == "1", "default": part[5] or None,
                           "primary_key": part[6] == "1"})
        except ValueError:
            continue
    return result


def _schema(lines: list[str]) -> str | None:
    value = next((line.partition("|")[2] for line in lines if line.startswith("TABLE_SCHEMA|")), None)
    return value if value and len(value) <= 8192 else None


def _parse_package_db(raw: str) -> dict[str, Any] | None:
    lines = raw.splitlines()
    schema = _schema(lines)
    columns = _parse_columns(lines, "COLUMN")
    expected = {"app_id", "app_disable", "app_onboot", "app_autorestart"}
    if not schema or not expected.issubset({item["name"] for item in columns}):
        return None
    rows = []
    for line in lines:
        part = line.split("|")
        if len(part) == 5 and part[0] == "APP_ROW":
            if any(len(item) > 512 for item in part[1:]):
                return None
            rows.append(dict(zip(("app_id", "app_disable", "app_onboot", "app_autorestart"), part[1:])))
    total = next((line for line in lines if line.startswith("APP_TOTAL|")), None)
    try:
        count = int(total.split("|", 1)[1]) if total else -1
    except ValueError:
        return None
    if count < 0 or len(rows) != min(count, MAX_PACKAGE_ROWS):
        return None
    return {"table": "package_app_info", "schema_sql": schema, "columns": columns,
            "rows": rows[:MAX_PACKAGE_ROWS], "row_count": count,
            "complete": count <= MAX_PACKAGE_ROWS}


def _parse_key_filter_db(raw: str) -> dict[str, Any] | None:
    lines = raw.splitlines()
    schema = _schema(lines)
    columns = _parse_columns(lines, "COLUMN")
    expected = {"appid", "key", "target_selector", "args"}
    if not schema or not expected.issubset({item["name"] for item in columns}):
        return None
    rows = []
    for line in lines:
        part = line.split("|", 4)
        if len(part) == 5 and part[0] == "KEY_ROW":
            if any(len(item) > 1024 for item in part[1:]):
                return None
            rows.append(dict(zip(("app_id", "key", "target_selector", "args"), part[1:])))
    total = next((line for line in lines if line.startswith("KEY_TOTAL|")), None)
    try:
        count = int(total.split("|", 1)[1]) if total else -1
    except ValueError:
        return None
    if count < 0 or len(rows) != min(count, MAX_KEY_FILTER_ROWS):
        return None
    return {"table": "key_filter", "schema_sql": schema, "columns": columns,
            "rows": rows[:MAX_KEY_FILTER_ROWS], "row_count": count,
            "complete": count <= MAX_KEY_FILTER_ROWS,
            "selected_keys": ["XF86More", "XF86SysMenu"]}


_COMMANDS = {
    "prd_info": (
        "test -r /etc/prd_info.ini\n"
        "sed -n -e '/^[[:space:]]*ModelName[[:space:]]*=/p' "
        "-e '/^[[:space:]]*Version[[:space:]]*=/p' "
        "-e '/^[[:space:]]*FirmwareVersion[[:space:]]*=/p' /etc/prd_info.ini"
    ),
    "platform": (
        "printf 'KERNEL|'\n"
        "uname -srmo\n"
        "if [ -r /etc/os-release ]; then\n"
        "sed -n -E 's/^(ID|VERSION_ID)=(.*)$/OS_\\1|\\2/p' /etc/os-release\n"
        "fi"
    ),
    "runtime": (
        "if [ -e /usr/share/dotnet.tizen/netcoreapp ]; then\n"
        "path=$(readlink -f /usr/share/dotnet.tizen/netcoreapp) || exit 1\n"
        "if [ -n \"$path\" ]; then printf 'RUNTIME_PATH|%s\\n' \"$path\"; fi\n"
        "fi\n"
        "for p in /usr/share/dotnet.tizen/netcoreapp/* "
        "/usr/share/dotnet/shared/Microsoft.NETCore.App/*; do\n"
        "if [ -d \"$p\" ]; then printf 'RUNTIME_PATH|%s\\n' \"$p\"; fi\n"
        "done\n"
        "if [ -d /usr/share/dotnet.tizen/netcoreapp ]; then\n"
        "find /usr/share/dotnet.tizen/netcoreapp -maxdepth 8 -type f "
        "\\( -name libcoreclr.so -o -name System.Private.CoreLib.dll \\) "
        "-print 2>/dev/null\n"
        "fi"
    ),
    "framework": (
        "for name in Tizen.NUI.dll Tizen.Applications.Common.dll Tizen.System.dll Tizen.dll; do\n"
        "found=$(find /usr/share/dotnet.tizen -maxdepth 8 -type f "
        "-name \"$name\" -print -quit 2>/dev/null) || exit 1\n"
        "if [ -n \"$found\" ]; then printf 'ASSEMBLY|%s|present|%s\\n' "
        "\"$name\" \"$found\"; else printf 'ASSEMBLY|%s|absent\\n' \"$name\"; fi\n"
        "done"
    ),
    "capabilities": (
        "for name in sqlite3 vconftool dotnet getcap tar systemctl base64 sha256sum mktemp gzip find readlink awk sed; do\n"
        "path=$(command -v \"$name\" 2>/dev/null || true)\n"
        "if [ -n \"$path\" ]; then printf 'TOOL|%s|%s\\n' \"$name\" \"$path\"; "
        "else printf 'TOOL|%s|absent\\n' \"$name\"; fi\n"
        "done\n"
        "if command -v getcap >/dev/null 2>&1; then\n"
        "for p in /usr/bin/dotnet /usr/bin/tar /usr/sbin/sdbd-tarlauncher; do\n"
        "if [ -e \"$p\" ]; then value=$(getcap \"$p\" 2>/dev/null) || "
        "{ printf 'FILECAP_ERROR|%s\\n' \"$p\"; continue; }; "
        "if [ -n \"$value\" ]; then printf 'FILECAP|%s|%s\\n' \"$p\" \"$value\"; "
        "else printf 'FILECAP|%s|none\\n' \"$p\"; fi; "
        "else printf 'FILECAP|%s|absent\\n' \"$p\"; fi\n"
        "done\n"
        "fi"
    ),
    "package_db": (
        "db=/opt/dbspace/.pkgmgr_parser.db\n"
        "test -r \"$db\"\n"
        "sqlite3 -readonly -batch -noheader -separator '|' \"$db\" "
        "\"SELECT 'TABLE_SCHEMA|' || replace(sql,char(10),' ') FROM sqlite_master "
        "WHERE type='table' AND name='package_app_info';\"\n"
        "sqlite3 -readonly -batch -noheader -separator '|' \"$db\" "
        "\"SELECT 'COLUMN|' || cid || '|' || name || '|' || type || '|' || "
        "\"notnull\" || '|' || coalesce(dflt_value,'') || '|' || pk "
        "FROM pragma_table_info('package_app_info');\"\n"
        "sqlite3 -readonly -batch -noheader -separator '|' \"$db\" "
        "\"SELECT 'APP_ROW|' || coalesce(app_id,'') || '|' || coalesce(app_disable,'') || '|' || "
        "coalesce(app_onboot,'') || '|' || coalesce(app_autorestart,'') "
        "FROM package_app_info ORDER BY app_id LIMIT 256;\"\n"
        "sqlite3 -readonly -batch -noheader -separator '|' \"$db\" "
        "\"SELECT 'APP_TOTAL|' || count(*) FROM package_app_info;\""
    ),
    "app_context_db": (
        "db=/opt/dbspace/.app-context.db\n"
        "test -r \"$db\"\n"
        "sqlite3 -readonly -batch -noheader -separator '|' \"$db\" "
        "\"SELECT 'TABLE_SCHEMA|' || replace(sql,char(10),' ') FROM sqlite_master "
        "WHERE type='table' AND name='key_filter';\"\n"
        "sqlite3 -readonly -batch -noheader -separator '|' \"$db\" "
        "\"SELECT 'COLUMN|' || cid || '|' || name || '|' || type || '|' || "
        "\"notnull\" || '|' || coalesce(dflt_value,'') || '|' || pk "
        "FROM pragma_table_info('key_filter');\"\n"
        "sqlite3 -readonly -batch -noheader -separator '|' \"$db\" "
        "\"SELECT 'KEY_ROW|' || coalesce(appid,'') || '|' || coalesce(key,'') || '|' || "
        "coalesce(target_selector,'') || '|' || coalesce(args,'') FROM key_filter "
        "WHERE key IN ('XF86More','XF86SysMenu') ORDER BY appid,key LIMIT 128;\"\n"
        "sqlite3 -readonly -batch -noheader -separator '|' \"$db\" "
        "\"SELECT 'KEY_TOTAL|' || count(*) FROM key_filter "
        "WHERE key IN ('XF86More','XF86SysMenu');\""
    ),
    "home_selector": (
        "command -v vconftool >/dev/null 2>&1\n"
        "vconftool get db/appfw/home_appid\n"
        "vconftool get db/appfw/startup_appid"
    ),
}


def _parse_platform(raw: str) -> dict[str, Any]:
    result: dict[str, Any] = {"runtime_paths": []}
    for line in raw.splitlines():
        if "|" not in line:
            continue
        key, value = line.split("|", 1)
        if key == "KERNEL" and value:
            result["kernel"] = value
        elif key == "OS_ID" and value:
            result["os_id"] = value.strip('"')
        elif key == "OS_VERSION_ID" and value:
            result["os_version_id"] = value.strip('"')
        elif key == "RUNTIME_PATH" and value and len(result["runtime_paths"]) < 64:
            result["runtime_paths"].append(value)
    return result


def _parse_runtime(raw: str) -> tuple[list[str], dict[str, list[str]]]:
    paths: list[str] = []
    files: dict[str, list[str]] = {
        "libcoreclr.so": [], "System.Private.CoreLib.dll": [],
    }
    for line in raw.splitlines():
        kind, sep, value = line.partition("|")
        if sep and kind == "RUNTIME_PATH" and value and len(value) <= 1024 and len(paths) < 64:
            paths.append(value)
        elif not sep and len(line) <= 1024 and len(files.get(line.rsplit("/", 1)[-1], [])) < 8:
            name = line.rsplit("/", 1)[-1]
            if name in files:
                files[name].append(line)
    return paths, files


def _parse_framework(raw: str) -> dict[str, str | None]:
    result = {}
    for line in raw.splitlines():
        part = line.split("|", 3)
        if len(part) >= 3 and part[0] == "ASSEMBLY" and part[1] in _ASSEMBLIES:
            if part[2] == "present":
                result[part[1]] = part[3] if len(part) == 4 and part[3] else "present"
            elif part[2] == "absent":
                result[part[1]] = None
    return result


def _parse_capabilities(
    raw: str,
) -> tuple[dict[str, bool], list[dict[str, str]] | None]:
    tools: dict[str, bool] = {}
    caps: list[dict[str, str]] | None = []
    for line in raw.splitlines():
        part = line.split("|", 2)
        if len(part) == 3 and part[0] == "TOOL" and part[1] in _TOOLS:
            tools[part[1]] = part[2] != "absent"
        elif part[0] == "FILECAP_ERROR":
            caps = None
        elif len(part) == 3 and part[0] == "FILECAP" and len(part[1]) <= 128 and len(part[2]) <= 256:
            if caps is None:
                continue
            value = part[2]
            prefix = part[1] + " "
            if value.startswith(prefix):
                value = value[len(prefix):]
            caps.append({"path": part[1], "value": value})
    return tools, caps


def _devices_model(client: Any, serial: str) -> tuple[str | None, str | None, str]:
    at = _now()
    try:
        result = client.run(("devices",), check=False, timeout=COMMAND_TIMEOUT)
    except Exception:
        return None, "DEVICES_COMMAND_ERROR", at
    stdout = getattr(result, "stdout", None)
    if not isinstance(stdout, str) or len(stdout.encode("utf-8", errors="replace")) > 16 * 1024:
        return None, "DEVICES_OUTPUT_LIMIT", at
    if getattr(result, "returncode", None) != 0:
        return None, "DEVICES_COMMAND_NONZERO", at
    rows = [line.split() for line in stdout.splitlines()]
    selected = [row for row in rows if row and row[0] == serial]
    if len(selected) != 1 or len(selected[0]) < 3 or selected[0][1] != "device":
        return None, "DEVICES_IDENTITY_UNAVAILABLE", at
    model = selected[0][-1]
    if not _MODEL_RE.fullmatch(model):
        return None, "DEVICES_MODEL_INVALID", at
    return model, None, at


def collect_inventory(client: Any) -> dict[str, Any]:
    """Collect selected read-only facts; never qualifies or authorizes writes."""
    inventory = _unknown_inventory(_now())
    serial = getattr(client, "serial", None)
    match = re.fullmatch(r"((?:[0-9]{1,3}\.){3}[0-9]{1,3}):([0-9]{1,5})", serial or "")
    if not match or match.group(2) != "26101":
        _error(inventory, "connection", "INVALID_EXPLICIT_TARGET")
        return inventory
    try:
        ipaddress.IPv4Address(match.group(1))
    except ipaddress.AddressValueError:
        _error(inventory, "connection", "INVALID_EXPLICIT_TARGET")
        return inventory
    try:
        client.connect()
        client.require_device()
    except Exception:
        _error(inventory, "connection", "DEVICE_NOT_READY")
        return inventory

    inventory["target"] = {
        "serial": serial, "status": "PROVEN",
        "source": "explicit SDB target after require_device",
        "observed_at": _now(),
    }
    devices_model, devices_error, devices_at = _devices_model(client, serial)
    results = {name: _run_read(client, name, body) for name, body in _COMMANDS.items()}

    raw, error, at = results["prd_info"]
    if error:
        _fail(inventory["device"]["firmware"], at, error, inventory, "prd_info")
        prd_model = None
        prd_model_error = error
    else:
        prd_model = _parse_model(raw or "")
        prd_model_error = None if prd_model else "PARSE_FAILED"
        firmware = parse_firmware(raw or "")
        if firmware:
            _set(inventory["device"]["firmware"], firmware, at)
        else:
            _fail(inventory["device"]["firmware"], at, "PARSE_FAILED", inventory, "prd_info_firmware")
    if prd_model and devices_model and prd_model != devices_model:
        _fail(inventory["device"]["model"], devices_at, "IDENTITY_CONFLICT",
              inventory, "model_identity")
    elif prd_model:
        _set(inventory["device"]["model"], prd_model, at)
        inventory["device"]["model"]["source"] = (
            "SDB /etc/prd_info.ini ModelName for explicit target"
            + (" and matching selected SDB devices row" if devices_model else "")
        )
    elif devices_model:
        _set(inventory["device"]["model"], devices_model, devices_at)
        inventory["device"]["model"]["source"] = "SDB devices row for exact selected serial"
        if prd_model_error:
            _error(inventory, "prd_info_model", prd_model_error)
    else:
        _fail(inventory["device"]["model"], devices_at, devices_error or prd_model_error or "MODEL_UNKNOWN",
              inventory, "model_identity")

    raw, error, at = results["platform"]
    values = {} if error else _parse_platform(raw or "")
    for key in ("kernel", "os_id", "os_version_id"):
        if values.get(key):
            _set(inventory["platform"][key], values[key], at)
        else:
            _fail(inventory["platform"][key], at, error or "PARSE_FAILED", inventory, "platform_" + key)

    raw, error, at = results["runtime"]
    if error:
        for key in ("runtime_paths", "runtime_files"):
            _fail(inventory["platform"][key], at, error, inventory, "runtime")
    else:
        paths, files = _parse_runtime(raw or "")
        _set(inventory["platform"]["runtime_paths"], paths, at)
        _set(inventory["platform"]["runtime_files"], files, at)

    raw, error, at = results["framework"]
    frameworks = {} if error else _parse_framework(raw or "")
    for name, field in inventory["framework"]["assemblies"].items():
        if name in frameworks:
            _set(field, {"present": frameworks[name] is not None, "path": frameworks[name]}, at)
        else:
            _fail(field, at, error or "PARSE_FAILED", inventory, "framework_" + name)

    raw, error, at = results["capabilities"]
    tools, caps = ({}, None) if error else _parse_capabilities(raw or "")
    for name, field in inventory["capabilities"]["tools"].items():
        if name in tools:
            _set(field, {"available": tools[name]}, at)
        else:
            _fail(field, at, error or "PARSE_FAILED", inventory, "capability_" + name)
    if caps is None or not tools.get("getcap", False):
        _fail(inventory["capabilities"]["file_capabilities"], at,
              error or ("TOOL_UNAVAILABLE" if not tools.get("getcap", False) else "FILECAP_READ_FAILED"),
              inventory, "file_capabilities")
    else:
        _set(inventory["capabilities"]["file_capabilities"], caps, at)

    raw, error, at = results["home_selector"]
    selectors = {}
    if not error:
        for line in (raw or "").splitlines():
            match = re.fullmatch(r"db/appfw/(home_appid|startup_appid):\s*([A-Za-z0-9_.-]{1,192})", line.strip())
            if match:
                selectors[match.group(1)] = match.group(2)
    for name in ("home_appid", "startup_appid"):
        if name in selectors:
            _set(inventory["selectors"][name], selectors[name], at)
        else:
            _fail(inventory["selectors"][name], at, error or "PARSE_FAILED", inventory, "selector_" + name)

    for operation, key, parser in (
        ("package_db", "package_app_info", _parse_package_db),
        ("app_context_db", "key_filter", _parse_key_filter_db),
    ):
        raw, error, at = results[operation]
        value = None if error else parser(raw or "")
        field = inventory["databases"][key]
        if value is None:
            _fail(field, at, error or "PARSE_FAILED", inventory, operation)
        else:
            _set(field, value, at)
            if not value["complete"]:
                field["status"] = "UNKNOWN"
                field["error"] = "ROW_LIMIT"
                _error(inventory, operation, "ROW_LIMIT")
    return inventory
