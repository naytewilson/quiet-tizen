"""Read-only producer for private launcher-host and artifact evidence.

The collector deliberately records partial evidence. It never runs the root
probe, captures or injects a command, transfers a file, or uses profile values
as observations. Artifact manifests are emitted only for stable, bounded
hash/size observations tied to the exact explicit SDB target and profile.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import PurePosixPath
import re
import shlex
from typing import Any

from inventory import _run_read, collect_inventory
from profiles import validate_profile


HOST_SCHEMA = "quiet-tizen.launcher-host.v1"
SNAPSHOT_SCHEMA = "quiet-tizen.snapshot-artifacts.v1"
SHA256_RE = re.compile(r"[a-f0-9]{64}")
MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{2,63}")
FIRMWARE_RE = re.compile(r"T-[A-Z0-9]+-[0-9]{4}-[0-9]{4}\.[0-9]+")
OWNER_RE = re.compile(r"[0-9]{1,5}:[0-9]{1,5}")
MODE_RE = re.compile(r"[0-7]{3,4}")
SMACK_RE = re.compile(r"[A-Za-z0-9_:.-]{1,200}")
PHASE_RE = re.compile(r"[A-Za-z0-9_-]{1,80}")
MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
MAX_PROBE_OUTPUT_BYTES = 64 * 1024
MAX_SNAPSHOT_REPORT_BYTES = 16 * 1024

FRAMEWORKS = (
    "Tizen.NUI.dll",
    "Tizen.Applications.Common.dll",
    "Tizen.System.dll",
    "Tizen.dll",
)
SNAPSHOT_FILES = (
    ".pkgmgr_parser.db",
    ".app-context.db",
    ".webappservice.db",
    "preferences-before.txt",
)
BACKUP_ROOT = "/opt/usr/share/quiet-tizen-backups"
FRAMEWORK_ROOT = "/usr/share/dotnet.tizen"


def _timestamp(value: datetime | str | None = None) -> str:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("Timestamp must be ISO-8601 with an explicit timezone.") from error
    else:
        raise ValueError("Timestamp must be an aware datetime or ISO-8601 string.")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Timestamp must be ISO-8601 with an explicit timezone.")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _age_is_fresh(observed_at: str, now: str, *, maximum_seconds: int = 86400) -> bool:
    try:
        observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        current = datetime.fromisoformat(now.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return False
    if observed.tzinfo is None or current.tzinfo is None:
        return False
    age = (current.astimezone(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds()
    return 0 <= age <= maximum_seconds


def _serial_is_explicit(serial: Any) -> bool:
    if not isinstance(serial, str):
        return False
    match = re.fullmatch(r"((?:[0-9]{1,3}\.){3}[0-9]{1,3}):26101", serial)
    if not match:
        return False
    try:
        ipaddress.IPv4Address(match.group(1))
    except ipaddress.AddressValueError:
        return False
    return True


def _profile_digest(profile: dict[str, Any]) -> str:
    encoded = json.dumps(profile, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _evidence(status: str, source: str | None, observed_at: str | None,
              error: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"status": status, "source": source, "observed_at": observed_at}
    if error:
        result["error"] = error
    return result


def _unknown(error: str | None = None, *, source: str | None = None,
             observed_at: str | None = None) -> dict[str, Any]:
    return _evidence("UNKNOWN", source, observed_at, error)


def _framework_command() -> str:
    # The only walked target tree is the four fixed Tizen framework assembly
    # names under the runtime tree. We require a unique regular file and stable
    # before/after size and digest observations before treating it as PROVEN.
    quoted_root = shlex.quote(FRAMEWORK_ROOT)
    names = " ".join(shlex.quote(name) for name in FRAMEWORKS)
    return f"""set -eu
for name in {names}; do
  found=$(find {quoted_root} -maxdepth 8 -type f -name \"$name\" -print 2>/dev/null) || exit 1
  count=$(printf '%s\\n' \"$found\" | awk 'NF {{ n++ }} END {{ print n+0 }}')
  if [ \"$count\" != 1 ]; then
    if [ \"$count\" = 0 ]; then state=ABSENT; else state=AMBIGUOUS; fi
    printf 'FRAMEWORK|%s|%s|%s||||\\n' \"$name\" \"$state\" \"$count\"
    continue
  fi
  path=$found
  case \"$path\" in {quoted_root}/*) ;; *) printf 'FRAMEWORK|%s|INVALID|0||||\\n' \"$name\"; continue;; esac
  if [ -L \"$path\" ] || [ ! -f \"$path\" ]; then
    printf 'FRAMEWORK|%s|INVALID|1|%s||||\\n' \"$name\" \"$path\"
    continue
  fi
  canonical=$(readlink -f \"$path\" 2>/dev/null || true)
  if [ \"$canonical\" != \"$path\" ]; then
    printf 'FRAMEWORK|%s|INVALID|1|%s||||\\n' \"$name\" \"$path\"
    continue
  fi
  size1=$(wc -c < \"$path\" | tr -d '[:space:]') || exit 1
  hash1=$(sha256sum \"$path\") || exit 1
  hash1=${{hash1%% *}}
  size2=$(wc -c < \"$path\" | tr -d '[:space:]') || exit 1
  hash2=$(sha256sum \"$path\") || exit 1
  hash2=${{hash2%% *}}
  if [ \"$size1\" = \"$size2\" ] && [ \"$hash1\" = \"$hash2\" ]; then state=PROVEN; else state=UNSTABLE; fi
  printf 'FRAMEWORK|%s|%s|1|%s|%s|%s|%s\\n' \"$name\" \"$state\" \"$path\" \"$size2\" \"$hash2\" ''
done"""


def _host_paths_command(root: str, entry: str, resource: str, native_image: str) -> str:
    qr = shlex.quote(root)
    qe = shlex.quote(entry)
    qresource = shlex.quote(resource)
    qnative = shlex.quote(native_image)
    return f"""set -eu
root={qr}
entry={qe}
resource={qresource}
cache={qnative}
label=''
read_label() {{
  if command -v getfattr >/dev/null 2>&1; then
    label=$(getfattr --only-values -n security.SMACK64 \"$1\" 2>/dev/null) || label=''
  else
    label=''
  fi
}}
if [ -L \"$root\" ]; then
  printf 'ROOT|PRESENT|0|1|||\\n'
elif [ -d \"$root\" ]; then
  canonical=$(readlink -f \"$root\" 2>/dev/null || true)
  if [ \"$canonical\" != \"$root\" ]; then
    printf 'ROOT|UNKNOWN|0|0|||\\n'
  else
    owner=$(stat -c '%u:%g' \"$root\" 2>/dev/null || true)
    mode=$(stat -c '%a' \"$root\" 2>/dev/null || true)
    read_label \"$root\"
    printf 'ROOT|PRESENT|1|0|%s|%s|%s\\n' \"$owner\" \"$mode\" \"$label\"
  fi
elif [ -e \"$root\" ]; then
  printf 'ROOT|PRESENT|0|0|||\\n'
else
  printf 'ROOT|UNKNOWN|0|0|||\\n'
fi

root_canonical=$(readlink -f \"$root\" 2>/dev/null || true)
if [ -L \"$root\" ] || [ ! -d \"$root\" ] || [ \"$root_canonical\" != \"$root\" ] || [ -L \"$root/bin\" ] || [ ! -d \"$root/bin\" ] || [ ! -r \"$root/bin\" ] || [ ! -x \"$root/bin\" ]; then
  printf 'ENTRY|UNKNOWN|0|0||||||\\n'
elif [ -L \"$entry\" ]; then
  printf 'ENTRY|PRESENT|0|1||||||\\n'
elif [ -f \"$entry\" ]; then
  root_canonical=$(readlink -f \"$root\" 2>/dev/null || true)
  bin_canonical=$(readlink -f \"$root/bin\" 2>/dev/null || true)
  entry_canonical=$(readlink -f \"$entry\" 2>/dev/null || true)
  if [ \"$root_canonical\" != \"$root\" ] || [ \"$bin_canonical\" != \"$root/bin\" ] || [ \"$entry_canonical\" != \"$entry\" ]; then
    printf 'ENTRY|UNKNOWN|0|0||||||\\n'
  else
    owner=$(stat -c '%u:%g' \"$entry\" 2>/dev/null || true)
    mode=$(stat -c '%a' \"$entry\" 2>/dev/null || true)
    size1=$(wc -c < \"$entry\" 2>/dev/null | tr -d '[:space:]' || true)
    hash1=$(sha256sum \"$entry\" 2>/dev/null || true); hash1=${{hash1%% *}}
    size2=$(wc -c < \"$entry\" 2>/dev/null | tr -d '[:space:]' || true)
    hash2=$(sha256sum \"$entry\" 2>/dev/null || true); hash2=${{hash2%% *}}
    read_label \"$entry\"
    if [ -n \"$size1\" ] && [ \"$size1\" = \"$size2\" ] && [ -n \"$hash1\" ] && [ \"$hash1\" = \"$hash2\" ]; then state=PROVEN; else state=UNSTABLE; fi
    printf 'ENTRY|PRESENT|1|0|%s|%s|%s|%s|%s|%s\\n' \"$state\" \"$owner\" \"$mode\" \"$size2\" \"$hash2\" \"$label\"
  fi
elif [ -e \"$entry\" ]; then
  printf 'ENTRY|PRESENT|0|0||||||\\n'
else
  printf 'ENTRY|UNKNOWN|0|0||||||\\n'
fi

for pair in resource:\"$resource\" native_image:\"$cache\"; do
  kind=${{pair%%:*}}; path=${{pair#*:}}; parent=${{path%/*}}
  root_canonical=$(readlink -f \"$root\" 2>/dev/null || true)
  parent_canonical=$(readlink -f \"$parent\" 2>/dev/null || true)
  if [ -L \"$root\" ] || [ ! -d \"$root\" ] || [ \"$root_canonical\" != \"$root\" ] || [ -L \"$parent\" ] || [ ! -d \"$parent\" ] || [ ! -r \"$parent\" ] || [ ! -x \"$parent\" ] || [ \"$parent_canonical\" != \"$parent\" ]; then
    printf 'PATH|%s|UNKNOWN|UNKNOWN\\n' \"$kind\"
  elif [ -L \"$path\" ]; then
    printf 'PATH|%s|PRESENT|SYMLINK\\n' \"$kind\"
  elif [ -e \"$path\" ]; then
    path_canonical=$(readlink -f \"$path\" 2>/dev/null || true)
    if [ \"$path_canonical\" = \"$path\" ]; then
      printf 'PATH|%s|PRESENT|NOT_SYMLINK\\n' \"$kind\"
    else
      printf 'PATH|%s|UNKNOWN|UNKNOWN\\n' \"$kind\"
    fi
  else
    printf 'PATH|%s|ABSENT|NOT_SYMLINK\\n' \"$kind\"
  fi
done"""


def _uid_process_command(entry: str) -> str:
    qentry = shlex.quote(entry)
    return f"""set -eu
entry={qentry}
scanned=0; denied=0; truncated=0
for cmdfile in /proc/[0-9]*/cmdline; do
  [ -e \"$cmdfile\" ] || continue
  scanned=$((scanned+1))
  if [ \"$scanned\" -gt 4096 ]; then truncated=1; break; fi
  if [ ! -r \"$cmdfile\" ]; then denied=$((denied+1)); continue; fi
  if tr '\\000' '\\n' < \"$cmdfile\" | grep -F -x -q \"$entry\"; then
    pid=${{cmdfile#/proc/}}; pid=${{pid%/cmdline}}
    status=/proc/$pid/status
    if [ -r \"$status\" ]; then
      uid_line=$(sed -n 's/^Uid:[[:space:]]*//p' \"$status\" 2>/dev/null || true)
      case \"$uid_line\" in *[!0-9\\ ]*) uid_line='' ;; esac
      set -- $uid_line
      if [ \"$#\" = 4 ]; then
        printf 'UIDPROC|%s|%s|%s|%s|%s\\n' \"$pid\" \"$1\" \"$2\" \"$3\" \"$4\"
      else
        printf 'UIDPROC|%s|UNKNOWN\\n' \"$pid\"
      fi
    else
      printf 'UIDPROC|%s|UNKNOWN\\n' \"$pid\"
    fi
  fi
done
printf 'UIDSCAN|%s|%s|%s\\n' \"$scanned\" \"$denied\" \"$truncated\"
"""


def _parse_framework_rows(raw: str, profile: dict[str, Any], target: str,
                           observed_at: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    artifacts: dict[str, Any] = {}
    manifests: list[dict[str, Any]] = []
    rows: dict[str, list[str]] = {}
    for line in raw.splitlines():
        parts = line.split("|")
        if len(parts) != 8 or parts[0] != "FRAMEWORK" or parts[1] not in FRAMEWORKS:
            continue
        rows.setdefault(parts[1], []).append(parts)

    for name in FRAMEWORKS:
        matches = rows.get(name, [])
        if len(matches) != 1:
            artifacts[name] = {"status": "UNKNOWN", "path": None, "size": None,
                               "sha256": None, "observed_at": observed_at,
                               "error": "ROW_MISSING_OR_DUPLICATED"}
            continue
        _, _, state, count, path, size_raw, digest, extra = matches[0]
        # The command emits eight columns; keep this validation strict so a
        # malformed/no-op shell result cannot become an artifact manifest.
        if extra:
            valid_row = False
        else:
            valid_row = True
        size = None
        if size_raw.isdigit():
            size = int(size_raw)
        if (not valid_row or state != "PROVEN" or count != "1" or
                not _safe_framework_path(path, name) or size is None or
                size <= 0 or size > MAX_ARTIFACT_BYTES or not SHA256_RE.fullmatch(digest)):
            artifacts[name] = {"status": "UNKNOWN", "path": None, "size": None,
                               "sha256": None, "observed_at": observed_at,
                               "error": "ARTIFACT_FACTS_UNPROVEN_OR_OUT_OF_BOUNDS"}
            continue
        artifacts[name] = {"status": "PROVEN", "path": path, "size": size,
                           "sha256": digest, "observed_at": observed_at,
                           "source": "fixed read-only SDB find/stat/hash command"}
        manifests.append(_artifact_manifest(
            profile, target, path, f"artifacts/{_path_stamp(observed_at)}/framework/{name}",
            "framework", digest, size, observed_at,
        ))
    return artifacts, manifests


def _safe_framework_path(path: str, name: str) -> bool:
    if not isinstance(path, str) or len(path) > 1024 or any(c in path for c in "\r\n\0|"):
        return False
    posix = PurePosixPath(path)
    root_parts = PurePosixPath(FRAMEWORK_ROOT).parts
    return (posix.is_absolute() and len(posix.parts) > len(root_parts) and
            ".." not in posix.parts and str(posix) == path and
            posix.parts[:len(root_parts)] == root_parts and posix.name == name and
            all(re.fullmatch(r"[A-Za-z0-9._+-]+", part)
                for part in posix.parts[len(root_parts):]))


def _path_stamp(timestamp: str) -> str:
    return re.sub(r"[^0-9TZ]", "", timestamp)


def _artifact_manifest(profile: dict[str, Any], target: str, remote: str,
                       destination: str, kind: str, digest: str, size: int,
                       observed_at: str) -> dict[str, Any]:
    return {
        "model": profile["model"],
        "firmware": profile["firmware"],
        "target": target,
        "remote": remote,
        "destination": destination,
        "kind": kind,
        "sha256": digest,
        "size": size,
        "observed_at": observed_at,
    }


def _parse_host_rows(raw: str, host: dict[str, Any], observed_at: str) -> dict[str, Any]:
    rows: dict[str, list[list[str]]] = {}
    for line in raw.splitlines():
        parts = line.split("|")
        if parts and parts[0] in {"ROOT", "ENTRY", "PATH"}:
            rows.setdefault(parts[0], []).append(parts)

    root_rows = rows.get("ROOT", [])
    if len(root_rows) == 1 and len(root_rows[0]) == 7:
        _, state, is_directory, is_symlink, owner, mode, label = root_rows[0]
        if state == "PRESENT" and is_directory in ("0", "1") and is_symlink in ("0", "1"):
            host["package_root"].update({
                "is_directory": is_directory == "1",
                "is_symlink": is_symlink == "1",
                "owner": owner if OWNER_RE.fullmatch(owner) else None,
                "mode": mode if MODE_RE.fullmatch(mode) else None,
                "smack_label": label if SMACK_RE.fullmatch(label) else None,
            })
            _mark(host, "package_root.is_directory", "PROVEN", "fixed read-only SDB stat/test", observed_at)
            _mark(host, "package_root.is_symlink", "PROVEN", "fixed read-only SDB test", observed_at)
            _mark(host, "package_root.owner", "PROVEN" if host["package_root"]["owner"] else "UNKNOWN",
                  "fixed read-only SDB stat" if host["package_root"]["owner"] else None, observed_at,
                  None if host["package_root"]["owner"] else "STAT_UNAVAILABLE")
            _mark(host, "package_root.mode", "PROVEN" if host["package_root"]["mode"] else "UNKNOWN",
                  "fixed read-only SDB stat" if host["package_root"]["mode"] else None, observed_at,
                  None if host["package_root"]["mode"] else "STAT_UNAVAILABLE")
            _mark(host, "package_root.smack_label", "PROVEN" if host["package_root"]["smack_label"] else "UNKNOWN",
                  "read-only getfattr security.SMACK64" if host["package_root"]["smack_label"] else None,
                  observed_at, None if host["package_root"]["smack_label"] else "SMACK_READ_UNAVAILABLE")
        elif state == "ABSENT":
            _mark(host, "package_root.is_directory", "PROVEN", "fixed read-only SDB test", observed_at)
            _mark(host, "package_root.is_symlink", "PROVEN", "fixed read-only SDB test", observed_at)
            host["package_root"].update({"is_directory": False, "is_symlink": False})
        else:
            _unknown_group(host, "package_root", "PATH_STATE_UNKNOWN", observed_at)
    else:
        _unknown_group(host, "package_root", "HOST_ROW_MISSING_OR_INVALID", observed_at)

    entry_rows = rows.get("ENTRY", [])
    if len(entry_rows) == 1 and len(entry_rows[0]) == 10:
        _, state, is_regular, is_symlink, hash_state, owner, mode, size_raw, digest, label = entry_rows[0]
        state_observed = state in ("PRESENT", "ABSENT")
        host["entry"]["is_regular"] = is_regular == "1" if state_observed and is_regular in ("0", "1") else None
        host["entry"]["is_symlink"] = is_symlink == "1" if state_observed and is_symlink in ("0", "1") else None
        _mark(host, "entry.is_regular", "PROVEN" if host["entry"]["is_regular"] is not None else "UNKNOWN",
              "fixed read-only SDB test" if host["entry"]["is_regular"] is not None else None, observed_at)
        _mark(host, "entry.is_symlink", "PROVEN" if host["entry"]["is_symlink"] is not None else "UNKNOWN",
              "fixed read-only SDB test" if host["entry"]["is_symlink"] is not None else None, observed_at)
        host["entry"]["owner"] = owner if OWNER_RE.fullmatch(owner) else None
        host["entry"]["mode"] = mode if MODE_RE.fullmatch(mode) else None
        host["entry"]["smack_label"] = label if SMACK_RE.fullmatch(label) else None
        host["entry"]["size"] = int(size_raw) if size_raw.isdigit() else None
        host["entry"]["sha256"] = digest if SHA256_RE.fullmatch(digest) else None
        entry_facts_proven = (state == "PRESENT" and is_regular == "1" and is_symlink == "0" and
                              hash_state == "PROVEN" and host["entry"]["size"] is not None and
                              host["entry"]["sha256"] is not None)
        for field, value, error, source in (
            ("entry.owner", host["entry"]["owner"], "STAT_UNAVAILABLE", "fixed read-only SDB stat"),
            ("entry.mode", host["entry"]["mode"], "STAT_UNAVAILABLE", "fixed read-only SDB stat"),
            ("entry.size", host["entry"]["size"], "SIZE_UNAVAILABLE", "fixed read-only SDB wc -c"),
            ("entry.sha256", host["entry"]["sha256"], "HASH_UNAVAILABLE", "fixed read-only SDB sha256sum"),
            ("entry.smack_label", host["entry"]["smack_label"], "SMACK_READ_UNAVAILABLE",
             "read-only getfattr security.SMACK64"),
        ):
            _mark(host, field, "PROVEN" if value is not None else "UNKNOWN",
                  source if value is not None else None, observed_at, None if value is not None else error)
        if entry_facts_proven and host["entry"]["size"] <= MAX_ARTIFACT_BYTES:
            host["entry"]["hash_size_status"] = "PROVEN"
        else:
            host["entry"]["hash_size_status"] = "UNKNOWN"
        if state == "ABSENT":
            _mark(host, "entry.exists", "PROVEN", "fixed read-only SDB test", observed_at)
        elif state == "PRESENT":
            _mark(host, "entry.exists", "PROVEN", "fixed read-only SDB test", observed_at)
        else:
            _mark(host, "entry.exists", "UNKNOWN", None, observed_at, "PATH_STATE_UNKNOWN")
    else:
        entry_facts_proven = False
        _unknown_group(host, "entry", "HOST_ROW_MISSING_OR_INVALID", observed_at)

    path_rows = rows.get("PATH", [])
    path_by_kind: dict[str, list[list[str]]] = {}
    for row in path_rows:
        if len(row) == 4:
            path_by_kind.setdefault(row[1], []).append(row)
    for kind, host_key in (("resource", "resource_path"), ("native_image", "native_image")):
        matches = path_by_kind.get(kind, [])
        if len(matches) != 1:
            _mark(host, f"{host_key}.exists", "UNKNOWN", None, observed_at, "PATH_ROW_MISSING_OR_DUPLICATED")
            _mark(host, f"{host_key}.is_symlink", "UNKNOWN", None, observed_at, "PATH_ROW_MISSING_OR_DUPLICATED")
            continue
        _, _, state, link_state = matches[0]
        if state == "ABSENT" and link_state == "NOT_SYMLINK":
            host[host_key].update({"exists": False, "is_symlink": False})
            _mark(host, f"{host_key}.exists", "PROVEN", "fixed read-only SDB test", observed_at)
            _mark(host, f"{host_key}.is_symlink", "PROVEN", "fixed read-only SDB test", observed_at)
        elif state == "PRESENT" and link_state in ("SYMLINK", "NOT_SYMLINK"):
            host[host_key].update({"exists": True, "is_symlink": link_state == "SYMLINK"})
            _mark(host, f"{host_key}.exists", "PROVEN", "fixed read-only SDB test", observed_at)
            _mark(host, f"{host_key}.is_symlink", "PROVEN", "fixed read-only SDB test", observed_at)
        else:
            _mark(host, f"{host_key}.exists", "UNKNOWN", None, observed_at, "PATH_STATE_UNKNOWN")
            _mark(host, f"{host_key}.is_symlink", "UNKNOWN", None, observed_at, "PATH_STATE_UNKNOWN")
    return host


def _mark(host: dict[str, Any], name: str, status: str, source: str | None,
          observed_at: str | None, error: str | None = None) -> None:
    host["field_evidence"][name] = _evidence(status, source, observed_at, error)


def _unknown_group(host: dict[str, Any], group: str, error: str,
                   observed_at: str | None) -> None:
    if group == "package_root":
        fields = ("is_directory", "is_symlink", "owner", "mode", "smack_label")
    elif group == "entry":
        fields = ("exists", "is_regular", "is_symlink", "owner", "mode", "size", "sha256", "smack_label")
    else:
        fields = ()
    for field in fields:
        _mark(host, f"{group}.{field}", "UNKNOWN", None, observed_at, error)


def _parse_uid_rows(raw: str, host: dict[str, Any], observed_at: str) -> None:
    scans = []
    processes = []
    malformed = False
    for line in raw.splitlines():
        parts = line.split("|")
        if parts[0] == "UIDSCAN":
            scans.append(parts)
        elif parts[0] == "UIDPROC":
            processes.append(parts)
        else:
            malformed = True
    if len(scans) != 1 or len(scans[0]) != 4:
        _mark(host, "launcher_uid", "UNKNOWN", None, observed_at, "UID_SCAN_MARKER_INVALID")
        return
    scan = scans[0]
    if (not all(value.isdigit() for value in scan[1:3]) or scan[3] not in ("0", "1") or
            int(scan[2]) != 0 or scan[3] != "0" or malformed):
        _mark(host, "launcher_uid", "UNKNOWN", None, observed_at, "UID_SCAN_INCOMPLETE")
        return
    if len(processes) != 1 or len(processes[0]) != 6:
        _mark(host, "launcher_uid", "UNKNOWN", None, observed_at,
              "MATCHING_PROCESS_COUNT_NOT_ONE" if processes else "MATCHING_PROCESS_NOT_RUNNING")
        return
    _, pid, *uid_values = processes[0]
    if (not pid.isdigit() or not all(value.isdigit() for value in uid_values) or
            any(int(value) > 65535 for value in uid_values)):
        _mark(host, "launcher_uid", "UNKNOWN", None, observed_at, "PROCESS_UID_INVALID")
        return
    numeric_uids = [int(value) for value in uid_values]
    host["launcher_uid_process"] = {
        "pid": int(pid),
        "uids": {"real": numeric_uids[0], "effective": numeric_uids[1],
                 "saved": numeric_uids[2], "filesystem": numeric_uids[3]},
    }
    if len(set(numeric_uids)) != 1:
        _mark(host, "launcher_uid", "UNKNOWN", "read-only /proc status for exact entry-path process",
              observed_at, "PROCESS_UID_FIELDS_DISAGREE")
        return
    host["launcher_uid"] = numeric_uids[1]
    _mark(host, "launcher_uid", "PROVEN", "read-only /proc status for exact entry-path process", observed_at)


def _probe_sources(probe_evidence: Any, *, target: str, model: str,
                   firmware: str, now: str) -> tuple[dict[str, Any] | None, str | None]:
    """Verify the current opaque ``sources=`` Probe result without decoding IDs."""
    if not isinstance(probe_evidence, dict):
        return None, "PROBE_OUTPUT_UNAVAILABLE"
    expected = {"schema", "target", "model", "firmware", "observed_at", "stdout", "sha256"}
    if set(probe_evidence) != expected or probe_evidence.get("schema") != "quiet-tizen.probe-output.v1":
        return None, "PROBE_ENVELOPE_INVALID"
    stdout = probe_evidence.get("stdout")
    if not isinstance(stdout, str) or len(stdout.encode("utf-8", errors="replace")) > MAX_PROBE_OUTPUT_BYTES:
        return None, "PROBE_OUTPUT_LIMIT"
    if hashlib.sha256(stdout.encode("utf-8")).hexdigest() != probe_evidence.get("sha256"):
        return None, "PROBE_OUTPUT_HASH_MISMATCH"
    if (probe_evidence.get("target") != target or probe_evidence.get("model") != model or
            probe_evidence.get("firmware") != firmware):
        return None, "PROBE_IDENTITY_CONFLICT"
    observed = probe_evidence.get("observed_at")
    try:
        observed_stamp = _timestamp(observed)
    except ValueError:
        return None, "PROBE_TIMESTAMP_INVALID"
    if not _age_is_fresh(observed_stamp, now):
        return None, "PROBE_STALE_OR_FUTURE"
    rows = [line[len("sources="):]
            for line in stdout.splitlines() if line.startswith("sources=")]
    if len(rows) != 1 or not rows[0] or len(rows[0]) > 4096:
        return None, "PROBE_OPAQUE_SOURCE_LINE_MISSING_OR_INVALID"
    return {"sources_raw": rows[0], "stdout_sha256": probe_evidence["sha256"],
            "target": target, "model": model, "firmware": firmware,
            "observed_at": _timestamp(probe_evidence["observed_at"])}, None


def collect(client: Any, profile: dict[str, Any], *, now: datetime | str | None = None,
            probe_evidence: Any = None) -> dict[str, Any]:
    """Collect bounded read-only launcher-host facts for one explicit SDB client.

    ``profile`` must already be a profile object accepted by the repository's
    exact-profile validator. The function does not choose a profile or target,
    does not use profile defaults as device facts, and never attempts root.
    """
    validated = validate_profile(profile)
    stamp = _timestamp(now)
    inventory = collect_inventory(client)
    target_record = inventory.get("target", {})
    target = target_record.get("serial") if isinstance(target_record, dict) else None
    target_proven = (isinstance(target_record, dict) and target_record.get("status") == "PROVEN" and
                     _serial_is_explicit(target))
    model_item = inventory.get("device", {}).get("model", {})
    firmware_item = inventory.get("device", {}).get("firmware", {})
    observed_model = model_item.get("value") if isinstance(model_item, dict) and model_item.get("status") == "PROVEN" else None
    observed_firmware = firmware_item.get("value") if isinstance(firmware_item, dict) and firmware_item.get("status") == "PROVEN" else None
    model_proven = isinstance(observed_model, str) and bool(MODEL_RE.fullmatch(observed_model))
    firmware_proven = isinstance(observed_firmware, str) and bool(FIRMWARE_RE.fullmatch(observed_firmware))
    identity_matches = (target_proven and model_proven and firmware_proven and
                        observed_model == validated["model"] and observed_firmware == validated["firmware"])
    identity_conflict = (target_proven and model_proven and firmware_proven and not identity_matches)
    inventory_at = inventory.get("collected_at")
    observed_at = inventory_at if isinstance(inventory_at, str) else stamp

    launcher = validated.get("launcher") if isinstance(validated.get("launcher"), dict) else {}
    root = launcher.get("root") if isinstance(launcher.get("root"), str) else None
    dll = launcher.get("dll") if isinstance(launcher.get("dll"), str) else None
    entry_path = f"{root}/bin/{dll}" if root and dll else None
    resource_path = f"{root}/res/quiet-home" if root else None
    native_image_path = f"{root}/bin/.native_image" if root else None

    host: dict[str, Any] = {
        "schema": HOST_SCHEMA,
        "status": "PARTIAL_EVIDENCE",
        "observed_at": stamp,
        "model": observed_model if model_proven else None,
        "firmware": observed_firmware if firmware_proven else None,
        "target": {"serial": target if target_proven else None,
                   "status": "PROVEN" if target_proven else "UNKNOWN",
                   "source": target_record.get("source") if target_proven else None,
                   "observed_at": target_record.get("observed_at") if target_proven else None},
        "profile": {"model": validated["model"], "firmware": validated["firmware"],
                    "sha256": _profile_digest(validated)},
        "identity_status": "PROVEN" if identity_matches else "CONFLICT" if identity_conflict else "UNKNOWN",
        "launcher_uid": None,
        "launcher_uid_process": None,
        "source_inputs": None,
        "probe_observation": {"status": "UNKNOWN", "sources_raw": None,
                              "stdout_sha256": None, "observed_at": None},
        "package_root": {"path": root, "is_directory": None, "is_symlink": None,
                         "owner": None, "mode": None, "smack_label": None},
        "entry": {"path": entry_path, "is_regular": None, "is_symlink": None,
                  "owner": None, "mode": None, "size": None, "sha256": None,
                  "smack_label": None, "original_backup": None, "hash_size_status": "UNKNOWN"},
        "resource_path": {"path": resource_path, "exists": None, "is_symlink": None},
        "native_image": {"path": native_image_path, "exists": None, "is_symlink": None},
        "backup": {"path": None, "files": None},
        "framework_artifacts": {},
        "artifact_manifests": [],
        "field_evidence": {},
        "errors": [],
        "qualification": {"status": "UNKNOWN", "eligible": False,
                           "reason": "Read-only artifact inspection is not exact-profile or installation qualification."},
    }
    for name, item, value, proven in (
        ("model", model_item, observed_model, model_proven),
        ("firmware", firmware_item, observed_firmware, firmware_proven),
    ):
        if proven:
            host["field_evidence"][name] = _evidence(
                "PROVEN", item.get("source"), item.get("observed_at", observed_at))
        else:
            error = item.get("error") if isinstance(item, dict) else "INVENTORY_FIELD_MISSING"
            host["field_evidence"][name] = _unknown(error or "INVENTORY_FIELD_UNKNOWN", observed_at=observed_at)
    host["field_evidence"]["target"] = (
        _evidence("PROVEN", target_record.get("source"), target_record.get("observed_at"))
        if target_proven else _unknown("EXPLICIT_TARGET_UNAVAILABLE", observed_at=observed_at))
    host["field_evidence"]["launcher_uid"] = _unknown(
        "EXACT_ENTRY_PROCESS_NOT_OBSERVED", observed_at=observed_at)
    host["field_evidence"]["source_inputs"] = _unknown(
        "PROBE_OUTPUT_UNAVAILABLE", observed_at=observed_at)
    host["field_evidence"]["package_root.path"] = (
        _evidence("PROFILE_DECLARED", "selected validated profile", stamp) if root else
        _unknown("PROFILE_ROOT_UNDECLARED", observed_at=stamp))
    host["field_evidence"]["entry.path"] = (
        _evidence("PROFILE_DECLARED", "selected validated profile", stamp) if entry_path else
        _unknown("PROFILE_ENTRY_UNDECLARED", observed_at=stamp))
    for key, value in (("resource_path.path", resource_path), ("native_image.path", native_image_path)):
        host["field_evidence"][key] = (
            _evidence("PROFILE_DECLARED", "selected validated profile", stamp) if value else
            _unknown("PROFILE_ROOT_UNDECLARED", observed_at=stamp))
    for field in ("is_directory", "is_symlink", "owner", "mode", "smack_label"):
        host["field_evidence"][f"package_root.{field}"] = _unknown("HOST_METADATA_UNAVAILABLE", observed_at=observed_at)
    for field in ("exists", "is_regular", "is_symlink", "owner", "mode", "size", "sha256", "smack_label", "original_backup"):
        host["field_evidence"][f"entry.{field}"] = _unknown(
            "PROFILE_ENTRY_UNDECLARED" if not entry_path else "HOST_METADATA_UNAVAILABLE", observed_at=observed_at)
    for field in ("exists", "is_symlink"):
        for key in ("resource_path", "native_image"):
            host["field_evidence"][f"{key}.{field}"] = _unknown(
                "PROFILE_ROOT_UNDECLARED" if not root else "HOST_METADATA_UNAVAILABLE", observed_at=observed_at)
    host["field_evidence"]["backup.path"] = _unknown("NO_SNAPSHOT_REPORT_SUPPLIED", observed_at=observed_at)
    host["field_evidence"]["backup.files"] = _unknown("NO_SNAPSHOT_REPORT_SUPPLIED", observed_at=observed_at)

    if identity_conflict:
        host["errors"].append("PROFILE_IDENTITY_CONFLICT")
    elif not identity_matches:
        host["errors"].append("EXACT_TARGET_IDENTITY_UNAVAILABLE")

    # Framework hash/size reads use a separate fixed command. Even when
    # identity is mismatched they may be shown as host observations, but no
    # pull manifest may be created for that profile/target pair.
    if target_proven:
        framework_raw, framework_error, framework_at = _run_read(
            client, "framework_artifacts", _framework_command())
    else:
        framework_raw, framework_error, framework_at = None, "EXPLICIT_TARGET_UNAVAILABLE", observed_at
    if framework_error:
        host["framework_artifacts"] = {
            name: {"status": "UNKNOWN", "path": None, "size": None, "sha256": None,
                   "observed_at": framework_at, "error": framework_error}
            for name in FRAMEWORKS
        }
        host["errors"].append(f"FRAMEWORK_READ_{framework_error}")
    else:
        artifacts, manifests = _parse_framework_rows(framework_raw or "", validated,
                                                     target if target_proven else "", framework_at)
        host["framework_artifacts"] = artifacts
        if identity_matches and target_proven:
            host["artifact_manifests"].extend(manifests)
        for name, artifact in artifacts.items():
            host["field_evidence"][f"framework.{name}"] = _evidence(
                artifact["status"], artifact.get("source"), artifact.get("observed_at"), artifact.get("error"))

    # Host paths are queried only from explicit profile paths and only after
    # the observed target model/firmware exactly matches the selected profile.
    if identity_matches and target_proven and root and entry_path and resource_path and native_image_path:
        host_raw, host_error, host_at = _run_read(
            client, "host_paths", _host_paths_command(root, entry_path, resource_path, native_image_path))
        if host_error:
            host["errors"].append(f"HOST_PATH_READ_{host_error}")
            _mark(host, "launcher_uid", "UNKNOWN", None, host_at, "HOST_PATH_READ_FAILED")
        else:
            _parse_host_rows(host_raw or "", host, host_at)
            entry = host["entry"]
            if (entry.get("hash_size_status") == "PROVEN" and
                    isinstance(entry.get("sha256"), str) and SHA256_RE.fullmatch(entry["sha256"]) and
                    type(entry.get("size")) is int and 0 < entry["size"] <= MAX_ARTIFACT_BYTES):
                host["artifact_manifests"].append(_artifact_manifest(
                    validated, target, entry_path,
                    f"artifacts/{_path_stamp(host_at)}/original-entry/{dll}",
                    "original-entry", entry["sha256"], entry["size"], host_at,
                ))
            uid_raw, uid_error, uid_at = _run_read(
                client, "launcher_uid", _uid_process_command(entry_path))
            if uid_error:
                host["errors"].append(f"LAUNCHER_UID_READ_{uid_error}")
                _mark(host, "launcher_uid", "UNKNOWN", None, uid_at, uid_error)
            else:
                _parse_uid_rows(uid_raw or "", host, uid_at)
    elif not root or not entry_path:
        host["errors"].append("PROFILE_LAUNCHER_PATH_UNAVAILABLE")
        _mark(host, "launcher_uid", "UNKNOWN", None, observed_at, "PROFILE_ENTRY_UNDECLARED")

    if probe_evidence is not None:
        observation, probe_error = _probe_sources(
            probe_evidence, target=target if target_proven else "",
            model=observed_model or "", firmware=observed_firmware or "", now=stamp)
        if observation is not None and identity_matches:
            host["probe_observation"] = {"status": "PROVEN", **observation}
            host["field_evidence"]["probe_observation.sources_raw"] = _evidence(
                "PROVEN", "bound raw read-only Probe output; opaque source API value",
                observation["observed_at"])
            host["field_evidence"]["source_inputs"] = _unknown(
                "PROBE_SOURCE_FORMAT_OPAQUE_IDS_UNAVAILABLE", observed_at=observation["observed_at"])
        else:
            host["field_evidence"]["source_inputs"] = _unknown(
                probe_error or "PROFILE_IDENTITY_CONFLICT", observed_at=observed_at)
            host["errors"].append(f"PROBE_{probe_error or 'IDENTITY_CONFLICT'}")

    return host


def from_snapshot_report(report: dict[str, Any], profile: dict[str, Any], *,
                         now: datetime | str | None = None) -> dict[str, Any]:
    """Create fixed backup pull manifests from the existing snapshot report.

    The report itself must name the exact backup paths emitted by
    ``recipes/snapshot.sh``. The caller supplies the just-observed target and
    identity from the read-only inventory; no path is synthesized from those
    values, and arbitrary paths or filenames are rejected.
    """
    validated = validate_profile(profile)
    expected_fields = {"schema", "model", "firmware", "target", "phase", "observed_at", "stdout"}
    if (not isinstance(report, dict) or set(report) != expected_fields or
            report.get("schema") != "quiet-tizen.snapshot-report.v1"):
        raise ValueError("Snapshot report envelope is malformed.")
    target = report.get("target")
    model = report.get("model")
    firmware = report.get("firmware")
    phase = report.get("phase")
    stdout = report.get("stdout")
    stamp = _timestamp(report.get("observed_at"))
    current = _timestamp(now)
    if not _age_is_fresh(stamp, current):
        raise ValueError("Snapshot report is stale or from the future; reacquire the exact phase.")
    if not _serial_is_explicit(target):
        raise ValueError("Snapshot manifests require the exact explicit SDB IPv4 target serial.")
    if model != validated["model"] or firmware != validated["firmware"]:
        raise ValueError("Snapshot report identity does not match the selected exact model/firmware profile.")
    if not MODEL_RE.fullmatch(model) or not FIRMWARE_RE.fullmatch(firmware):
        raise ValueError("Snapshot report identity fields are malformed.")
    if not isinstance(phase, str) or not PHASE_RE.fullmatch(phase):
        raise ValueError("Snapshot phase must be an exact simple phase name.")
    if not isinstance(stdout, str) or len(stdout.encode("utf-8", errors="replace")) > MAX_SNAPSHOT_REPORT_BYTES:
        raise ValueError("Snapshot report is missing or exceeds the bounded parser input.")

    expected_root = f"{BACKUP_ROOT}/{phase}"
    retained = f"Verified, immutable snapshot retained at: {expected_root}"
    if stdout.count(retained) != 1:
        raise ValueError("Snapshot report must contain exactly one matching retained backup path.")
    artifacts: dict[str, tuple[str, int, str]] = {}
    for line in stdout.splitlines():
        if not line.startswith("ARTIFACT|"):
            continue
        parts = line.split("|")
        if len(parts) != 4:
            raise ValueError("Malformed snapshot artifact row.")
        _, remote, size_raw, digest = parts
        posix = PurePosixPath(remote)
        if (not posix.is_absolute() or ".." in posix.parts or str(posix) != remote or
                len(posix.parts) != 7 or posix.parts[:5] != ("/", "opt", "usr", "share", "quiet-tizen-backups") or
                posix.parts[5] != phase or posix.parts[6] not in SNAPSHOT_FILES):
            raise ValueError("Snapshot report contains a path outside the exact backup phase/file allow-list.")
        filename = posix.parts[6]
        if filename in artifacts:
            raise ValueError("Snapshot report contains a duplicate artifact filename.")
        if not size_raw.isdigit():
            raise ValueError("Snapshot artifact size is malformed.")
        size = int(size_raw)
        if not 0 < size <= MAX_ARTIFACT_BYTES:
            raise ValueError("Snapshot artifact size is outside the privileged pull bound.")
        if not SHA256_RE.fullmatch(digest):
            raise ValueError("Snapshot artifact digest is malformed.")
        artifacts[filename] = (remote, size, digest)
    if set(artifacts) != set(SNAPSHOT_FILES):
        raise ValueError("Snapshot report must contain exactly the four known immutable backup files.")

    manifests = []
    file_hashes = {}
    for filename in SNAPSHOT_FILES:
        remote, size, digest = artifacts[filename]
        file_hashes[filename] = digest
        manifests.append(_artifact_manifest(
            validated, target, remote, f"artifacts/backups/{phase}/{filename}",
            "backup", digest, size, stamp,
        ))
    return {
        "schema": SNAPSHOT_SCHEMA,
        "status": "PROVEN",
        "observed_at": stamp,
        "target": target,
        "model": model,
        "firmware": firmware,
        "report_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
        "backup": {"path": expected_root, "files": file_hashes},
        "artifact_manifests": manifests,
    }
