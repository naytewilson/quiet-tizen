#!/usr/bin/env python3
"""Explicit-target inventory and separately gated temporary-root operations."""
import argparse
import asyncio
import base64
import gzip
import hashlib
import ipaddress
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import secrets
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor"))
from samsung_tv_root.archive_root import probe
from samsung_tv_root.sdb import SdbClient, find_sdb
from profiles import (load_profiles, load_profile, check_identity, resolve_firmware,
                      require_capability)

# Backward-compatible identity fixture for callers; the CLI never selects this as a default.
PROFILE = load_profile(ROOT / "profiles/un75u8000ffxza-1300.9.json")
STAGE = "/home/owner/share/tmp/sdk_tools/quiet-tizen"


def private_path(path):
    path = Path(path).expanduser().resolve()
    private = (ROOT / ".local").resolve()
    if path == private or private not in path.parents:
        raise ValueError("Private files must be inside this checkout's .local/ directory.")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def save_private(path, data):
    path = private_path(path)
    # Preserve original evidence rather than silently replacing a prior snapshot.
    with path.open('xb') as stream:
        stream.write(data)
    path.chmod(0o600)


def observed_model(client):
    client.connect()
    client.require_device()
    rows = [line.split() for line in client.run(("devices",), check=True).stdout.splitlines()]
    rows = [row for row in rows if row and row[0] == client.serial]
    if len(rows) != 1 or len(rows[0]) < 3:
        raise ValueError("Cannot identify the explicitly selected device from SDB.")
    return rows[0][-1]


def script_command(script, arguments, *, token=None):
    """Decode in process memory, hash exact bytes, run and report execution.

    No pipefail dependency: the decoder is the final pipeline command, its status
    is checked before executing anything, and the decoded bytes must hash correctly.
    A zero-exit decoder that emits nothing is therefore also a failure.
    """
    if not isinstance(script, bytes) or not script or len(script) > 45000 or b'\0' in script:
        raise ValueError('Script must be 1..45000 bytes and contain no NUL.')
    token = token or secrets.token_hex(16)
    if not re.fullmatch(r'[a-f0-9]{16,64}', token):
        raise ValueError('Invalid execution sentinel token.')
    if any(not isinstance(a, str) or '\0' in a for a in arguments):
        raise ValueError('Invalid script argument.')
    encoded = base64.b64encode(script).decode('ascii')
    digest = hashlib.sha256(script).hexdigest()
    # The final dot preserves all trailing newlines through command substitution.
    # No decoded script file is created, so SIGKILL/timeout cannot strand one.
    wrapped = f'''umask 077
qt_script=$(printf '%s' {shlex.quote(encoded)} | base64 -d && printf '.') || exit 71
qt_script=${{qt_script%?}}
qt_hash=$(printf '%s' "$qt_script" | sha256sum) || exit 72
test "${{qt_hash%% *}}" = {digest} || exit 73
printf 'QTSCRIPT:{token}:BEGIN\\n'
printf '%s' "$qt_script" | /bin/sh -s -- {shlex.join(arguments)}
qt_rc=$?
printf '\\nQTSCRIPT:{token}:END:%s\\n' "$qt_rc"
exit "$qt_rc"
'''
    if len(wrapped.encode()) > 64000:
        raise ValueError('Script and arguments exceed the bounded root command channel.')
    return wrapped


def verify_script_output(stdout, token, exit_code):
    prefix = f'QTSCRIPT:{token}:'
    if not isinstance(stdout, str) or stdout.count(prefix) != 2:
        raise ValueError('Script execution sentinel missing, duplicated or truncated; exit 0 alone is not proof.')
    match = re.fullmatch(re.escape(prefix + 'BEGIN\n') + r'(.*)\n' +
                         re.escape(prefix + 'END:') + r'([0-9]+)\n', stdout, re.DOTALL)
    if not match or int(match[2]) != exit_code or exit_code != 0:
        raise ValueError('Script did not complete successfully with a matching execution sentinel.')
    return match[1]


def pull_command(remote):
    if not isinstance(remote, str) or not remote.startswith("/") or any(c in remote for c in ('\0', '\n', '\r')):
        raise ValueError("Use an absolute, single-line TV file path.")
    # The upstream command channel captures at most 1 MiB per stream.
    # A compressed transfer is bounded before emitting any base64.
    return f"""set -eu
test -f {shlex.quote(remote)}
test ! -L {shlex.quote(remote)}
qt_parent={shlex.quote(str(PurePosixPath(remote).parent))}
while [ "$qt_parent" != / ]; do
    test ! -L "$qt_parent"
    qt_parent=${{qt_parent%/*}}
    test -n "$qt_parent" || qt_parent=/
done
test "$(stat -c %s {shlex.quote(remote)})" -le 33554432
# The authenticated agent supplies its exact owned session staging directory.
# probe's finally removes and verifies this directory even after child SIGKILL.
test -n "${{QUIET_TIZEN_SESSION_DIR:-}}"
t=$(mktemp "${{QUIET_TIZEN_SESSION_DIR}}quiet-tizen-pull.XXXXXX")
trap 'rm -f "$t"' EXIT HUP INT TERM
before=$(sha256sum {shlex.quote(remote)})
gzip -c {shlex.quote(remote)} > "$t"
test "$(wc -c < "$t")" -le 760000 || {{ echo 'File too large; split a snapshot into smaller files first.' >&2; exit 1; }}
after=$(sha256sum {shlex.quote(remote)})
test "$before" = "$after" || {{ echo 'Source changed during transfer.' >&2; exit 1; }}
printf '%s\\n' "${{before%% *}}"
base64 "$t"
"""


def decode_pull(stdout):
    if len(stdout) > 1013400:
        raise ValueError('Compressed transfer exceeds the bounded channel.')
    digest, encoded = stdout.split("\n", 1)
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Missing transfer digest.")
    compressed = base64.b64decode("".join(encoded.split()), validate=True)
    if len(compressed) > 760000:
        raise ValueError('Compressed transfer exceeds the bounded channel.')
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as archive:
        data = archive.read(33554433)
    if len(data) > 33554432 or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("Transfer size or digest mismatch; file was not saved.")
    return data


def pull_artifact(path, profile, remote, destination, target):
    """Bind privileged reads to inventoried framework, backup or original files."""
    from deployment import _time
    path = private_path(path)
    if path.stat().st_size > 16384:
        raise ValueError('Artifact manifest too large.')
    manifest = json.loads(path.read_text())
    expected = {'model','firmware','remote','destination','kind','sha256','size','observed_at','target'}
    if not isinstance(manifest, dict) or set(manifest) != expected:
        raise ValueError('Malformed private artifact manifest.')
    if (manifest['model'] != profile['model'] or manifest['firmware'] != profile['firmware'] or
            manifest['remote'] != remote or manifest['target'] != target):
        raise ValueError('Artifact manifest contradicts exact identity or remote path.')
    local = private_path(destination)
    if local.exists() or manifest['destination'] != str(local.relative_to(ROOT / '.local')):
        raise ValueError('Artifact destination differs or already exists; preserve original evidence.')
    if (type(manifest['size']) is not int or not 0 <= manifest['size'] <= 33554432 or
            not isinstance(manifest['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', manifest['sha256'])):
        raise ValueError('Artifact needs a bounded expected size and complete SHA-256.')
    age = (datetime.now(timezone.utc) - _time(manifest['observed_at'])).total_seconds()
    if not 0 <= age <= 86400:
        raise ValueError('Artifact observation is stale or from the future.')
    parts = PurePosixPath(remote).parts
    if '..' in parts or str(PurePosixPath(remote)) != remote:
        raise ValueError('Artifact path is not canonical.')
    kind = manifest['kind']
    frameworks = {'Tizen.NUI.dll','Tizen.Applications.Common.dll','Tizen.System.dll','Tizen.dll'}
    backup_names = {'.pkgmgr_parser.db','.app-context.db','.webappservice.db',
                    'preferences-before.txt','launcher-entry-before.dll'}
    host = profile.get('launcher', {})
    allowed = (
        kind == 'framework' and remote.startswith('/usr/share/dotnet.tizen/') and parts[-1] in frameworks
        or kind == 'backup' and len(parts) == 7 and parts[:5] == ('/','opt','usr','share','quiet-tizen-backups')
        and re.fullmatch('[A-Za-z0-9_-]{1,80}', parts[5]) and parts[6] in backup_names
        or kind == 'original-entry' and host.get('root') and host.get('dll')
        and remote == host['root'] + '/bin/' + host['dll']
    )
    if not allowed:
        raise ValueError('Privileged pull is outside the qualified framework/backup/original artifact scope.')
    return manifest


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="The owner's TV IPv4 address; never auto-discovered")
    parser.add_argument("--firmware", help="Operator-verified version from the TV's About screen")
    parser.add_argument('--profile', type=Path, help='Exact validated profile; never defaults to another model')
    parser.add_argument('--acknowledge-temporary-root', action='store_true',
                        help='Separately opt into the authenticated archive-root attempt; never part of doctor')
    parser.add_argument('--deployment-plan', type=Path, help='Private operation-bound owner approval and recovery record')
    parser.add_argument('--confirm-operation-sha256', help='Confirm the exact separately reviewed persistent operation digest')
    parser.add_argument('--confirm-plan-sha256', help='Confirm exact reviewed plan bytes, mutation list and artifact references')
    parser.add_argument('--artifact-manifest', type=Path, help='Private exact-path/destination/hash/size manifest for a privileged pull')
    parser.add_argument('--probe-evidence', type=Path, help='Optional existing private read-only Probe capture for inspect; never executes a probe')
    parser.add_argument('--describe-operation', action='store_true',
                        help='Print the local operation/profile hashes for review, without contacting a TV')
    parser.add_argument("--sdb", help="Path to Samsung's separately installed SDB")
    parser.add_argument("--callback-host", help="This computer's LAN IPv4 if route detection is wrong")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("doctor", help="Supported read-only SDB inventory only; no injection, payload or root attempt")
    sub.add_parser('inspect', help='Fixed supported-SDB artifact/host metadata and pull-manifest producer; never attempts root')
    sub.add_parser("root-check", help="Explicit temporary-root attempt; stages payloads and verifies cleanup")
    snapshot = sub.add_parser('snapshot', help='Separately approved new numbered backup capture; never changes system settings')
    snapshot.add_argument('name', help='Unique immutable snapshot phase name')
    run = sub.add_parser("exec", help="Execute one reviewed command or short script using temporary root")
    source = run.add_mutually_exclusive_group(required=True)
    source.add_argument("--command")
    source.add_argument("--script", type=Path)
    run.add_argument("--arg", action="append", default=[], help="Positional argument passed to --script")
    pull = sub.add_parser("pull", help="Read and verify one bounded file into ignored .local/")
    pull.add_argument("remote")
    pull.add_argument("local", type=Path)
    push = sub.add_parser("push", help="Upload one file to the SDK staging directory, not to system paths")
    push.add_argument("local", type=Path)
    push.add_argument("name", help="Simple staging filename")
    args = parser.parse_args()
    ipaddress.IPv4Address(args.host)
    if args.callback_host:
        ipaddress.IPv4Address(args.callback_host)
    # Invalid or unqualified requests fail before opening a device connection.
    profile = load_profile(args.profile) if args.profile else None
    if args.action != 'doctor' and profile is None:
        raise ValueError('Select an explicit exact profile; run read-only doctor first.')
    if args.action not in ('doctor','inspect') and not args.acknowledge_temporary_root and not args.describe_operation:
        raise ValueError('This operation invokes temporary root; explicit acknowledgement is required.')
    operation = {'action': args.action}
    command = None
    if args.action == "root-check":
        if not profile.get('runtime'):
            raise ValueError('No compatible runtime has been inventoried for this root route.')
        command = 'id; test "$(id -u)" = 0'
    elif args.action == "pull":
        require_capability(profile, 'root_session')
        if not args.artifact_manifest:
            raise ValueError('Privileged pull requires a qualified exact artifact manifest.')
        artifact = pull_artifact(args.artifact_manifest, profile, args.remote, args.local, f'{args.host}:26101')
        private_path(args.local)
        command = pull_command(args.remote)
    elif args.action == 'exec' and args.script:
        script = args.script.read_bytes()
        operation.update(script_sha256=hashlib.sha256(script).hexdigest(), arguments=args.arg)
        # Validate size, NULs and arguments before any network operation.
        script_command(script, args.arg)
    elif args.action == 'exec':
        if args.arg:
            raise ValueError("--arg requires --script.")
        command = args.command
        if not command or '\0' in command or len(command.encode()) > 45000:
            raise ValueError('Use a bounded nonempty command.')
        operation.update(command_sha256=hashlib.sha256(command.encode()).hexdigest(), arguments=[])
    elif args.action == 'push':
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,100}', args.name):
            raise ValueError('Use a simple staging filename without directory components.')
        if not args.local.is_file() or not 0 < args.local.stat().st_size <= 33554432:
            raise ValueError('Upload must be a nonempty bounded file (32 MiB maximum).')
        digest = hashlib.sha256(args.local.read_bytes()).hexdigest()
        remote = PurePosixPath(STAGE) / args.name
        operation.update(file_sha256=digest, remote=str(remote))
    elif args.action == 'snapshot':
        if not re.fullmatch('[A-Za-z0-9_-]{1,80}', args.name):
            raise ValueError('Use a unique simple snapshot phase name.')
        script = (ROOT / 'recipes/snapshot.sh').read_bytes()
        snapshot_arguments = [profile['model'], profile['firmware'], args.name]
        script_command(script, snapshot_arguments)
        operation.update(script_sha256=hashlib.sha256(script).hexdigest(), arguments=snapshot_arguments)
    if args.describe_operation:
        if args.action not in ('exec', 'push', 'snapshot'):
            raise ValueError('Operation descriptions apply only to exec/push deployment drafts.')
        from deployment import profile_digest, canonical_digest
        print(json.dumps({'status':'DRAFT_UNAUTHORIZED', 'model':profile['model'], 'firmware':profile['firmware'],
                          'profile_sha256':profile_digest(profile), 'operation':operation,
                          'operation_sha256':canonical_digest(operation)}, indent=2))
        return
    if args.action in ('exec', 'push', 'snapshot'):
        if not args.deployment_plan:
            raise ValueError('Persistent operations require an operation-bound deployment plan and separate owner go-ahead.')
        from deployment import load_plan
        from deployment import canonical_digest
        if args.confirm_operation_sha256 != canonical_digest(operation):
            raise ValueError('Confirm the exact separately reviewed operation SHA-256 before any persistent write.')
        plan = load_plan(args.deployment_plan, profile, operation, ROOT / '.local', args.confirm_plan_sha256)
        if (args.action == 'snapshot') != (plan['capability'] == 'backup_capture'):
            raise ValueError('Backup-capture authority cannot execute an arbitrary command or upload.')
    if args.sdb:
        os.environ['SDB'] = str(Path(args.sdb).expanduser().resolve())
    client = SdbClient(find_sdb(), args.host, timeout=15)
    if args.action == 'inspect':
        from inspect_artifacts import collect
        probe_evidence = None
        if args.probe_evidence:
            probe_path = private_path(args.probe_evidence)
            if probe_path.stat().st_size > 131072:
                raise ValueError('Probe evidence exceeds the bounded private input.')
            probe_evidence = json.loads(probe_path.read_text())
        inspection = collect(client, profile, probe_evidence=probe_evidence)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        output_path = ROOT / '.local/inventory' / f'inspect-{stamp}.json'
        save_private(output_path, (json.dumps(inspection, indent=2) + '\n').encode())
        for index, manifest in enumerate(inspection.get('artifact_manifests', [])):
            manifest_path = ROOT / '.local/inventory' / f'artifact-{stamp}-{index}.json'
            save_private(manifest_path, (json.dumps(manifest, indent=2) + '\n').encode())
        print(f'Read-only artifact/host evidence saved: {output_path.relative_to(ROOT)}')
        print('Missing metadata remains UNKNOWN. No root attempt or host qualification is implied.')
        return
    if args.action != 'doctor':
        model = observed_model(client)
        if model != profile['model']:
            raise ValueError('SDB model contradicts selected profile; no firmware command or root attempted.')
    from inventory import collect_inventory
    facts = collect_inventory(client)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    inventory_log = ROOT / '.local/inventory' / f'{stamp}.json'
    save_private(inventory_log, (json.dumps(facts, indent=2) + '\n').encode())
    device = facts.get('device', {})
    observed = device.get('firmware', {})
    actual_firmware = observed.get('value') if observed.get('status') == 'PROVEN' else None
    observed_product_model = device.get('model', {})
    if (args.action != 'doctor' and (observed_product_model.get('status') != 'PROVEN' or
            observed_product_model.get('value') != model)):
        raise ValueError('Current model evidence is missing or contradicts SDB identity; stop before root.')
    firmware = resolve_firmware(actual_firmware, args.firmware)
    if args.action == 'doctor':
        print(f'Read-only inventory saved: {inventory_log.relative_to(ROOT)}')
        print('No root route attempted. Inventory does not qualify installation, Home, settings or cleanup.')
        print('Firmware:', actual_firmware or 'UNKNOWN; obtain the current About This TV value.')
        return
    check_identity(model, firmware, [profile])
    if args.action in ('exec', 'push', 'snapshot') and not actual_firmware:
        raise ValueError('Persistent writes require live firmware readback; an operator override is insufficient.')
    if args.action == 'push':
        # Stage only after the plan passes. Each root command has its own log and sentinel.
        run_root(args, model, f'set -eu\ntest ! -e {shlex.quote(str(remote))}\nmkdir -p {shlex.quote(STAGE)}'.encode())
        client.push(args.local.resolve(), remote)
        output = run_root(args, model, f'sha256sum {shlex.quote(str(remote))}'.encode())
        if output.split()[0] != digest:
            raise ValueError('Uploaded file digest mismatch; inspect the private staging receipt before retrying.')
        print('Staging readback hash verified; remove only the named staged file after deployment.')
        return
    if args.action == 'snapshot':
        output = run_root(args, model, script, snapshot_arguments)
        from inspect_artifacts import from_snapshot_report
        now = datetime.now(timezone.utc)
        stamp = now.strftime('%Y%m%dT%H%M%S.%fZ')
        snapshot_report = {'schema':'quiet-tizen.snapshot-report.v1', 'model':model,
                           'firmware':firmware, 'target':client.serial, 'phase':args.name,
                           'observed_at':now.isoformat(), 'stdout':output}
        parsed = from_snapshot_report(snapshot_report, profile)
        save_private(ROOT / '.local/inventory' / f'snapshot-report-{stamp}.json',
                     (json.dumps(snapshot_report, indent=2) + '\n').encode())
        save_private(ROOT / '.local/inventory' / f'snapshot-artifacts-{stamp}.json',
                     (json.dumps(parsed, indent=2) + '\n').encode())
        for index, manifest in enumerate(parsed['artifact_manifests']):
            save_private(ROOT / '.local/inventory' / f'snapshot-pull-{stamp}-{index}.json',
                         (json.dumps(manifest, indent=2) + '\n').encode())
    elif args.action == 'exec' and args.script:
        output = run_root(args, model, script, args.arg)
    else:
        output = run_root(args, model, command.encode())
    if args.action == 'pull':
        data = decode_pull(output)
        if len(data) != artifact['size'] or hashlib.sha256(data).hexdigest() != artifact['sha256']:
            raise ValueError('Artifact readback differs from the inventoried size/hash; file was not saved.')
        save_private(args.local, data)
        print('Verified file saved inside .local/.')
    else:
        print(output, end='')


def run_root(args, model, script, arguments=()):
    token = secrets.token_hex(16)
    command = script_command(script, arguments, token=token)
    result = asyncio.run(probe(args.host, model=model, callback_host=args.callback_host,
                              payloads=ROOT / '.local/build/payloads', commands=(command,), command_timeout=30))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    log = ROOT / ".local/sessions" / f"{stamp}.json"
    save_private(log, (json.dumps(result, indent=2) + "\n").encode())
    if (result.get('authenticated') is not True or type(result.get('uid')) is not int or
            type(result.get('euid')) is not int or result['uid'] != 0 or result['euid'] != 0):
        raise ValueError("The callback did not prove UID/EUID 0.")
    if result.get('system_files_unchanged') is not True or result.get('staging_cleaned') is not True:
        raise ValueError("Root session cleanup checks failed; inspect the private session log.")
    commands = result.get('commands')
    if not isinstance(commands, list) or len(commands) != 1:
        raise ValueError('Root response is incomplete; inspect the private receipt.')
    response = commands[0]
    if (not isinstance(response, dict) or type(response.get('exit_code')) is not int or
            type(response.get('timed_out')) is not bool or
            not isinstance(response.get('stdout'), str) or not isinstance(response.get('stderr'), str)):
        raise ValueError('Malformed root command response.')
    if response["stderr"]:
        print(response["stderr"], file=sys.stderr, end="")
    if response["exit_code"] or response["timed_out"]:
        raise RuntimeError(f"Command failed ({response['exit_code']}, timeout={response['timed_out']}). "
                           f"Private log: {log.relative_to(ROOT)}")
    output = verify_script_output(response['stdout'], token, response['exit_code'])
    print('Authenticated UID/EUID 0 session completed; staging cleanup and unchanged system hashes checked.')
    return output


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as error:
        print(f"Stopped: {error}", file=sys.stderr)
        sys.exit(1)
