"""Reviewable deployment bindings, not an authentication or consent service.

Only real owner approval and independently verified device evidence may populate
these records. Synthetic fixtures exercise guards; they do not qualify a TV.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from profiles import require_capability, validate_profile


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                     separators=(',', ':')).encode()).hexdigest()


def profile_digest(profile):
    return canonical_digest(validate_profile(profile))


def _time(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('missing timezone')
        return parsed
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError('Evidence timestamps require an explicit timezone.') from error


def _receipt(ref, private, expected_status, profile):
    if not isinstance(ref, dict) or set(ref) != {'path', 'sha256'}:
        raise ValueError('Missing hashed private evidence receipt.')
    relative = ref['path']
    if not isinstance(relative, str) or Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise ValueError('Receipt path must remain below .local/.')
    path = (private / relative).resolve()
    if private.resolve() not in path.parents:
        raise ValueError('Receipt escapes private evidence directory.')
    if path.stat().st_size > 1048576:
        raise ValueError('Private evidence receipt exceeds its size bound.')
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != ref['sha256']:
        raise ValueError('Private evidence receipt hash/size mismatch.')
    value = json.loads(data)
    if (value.get('status') != expected_status or value.get('model') != profile['model'] or
            value.get('firmware') != profile['firmware']):
        raise ValueError('Evidence receipt status or exact device identity mismatch.')
    now = datetime.now(timezone.utc)
    age = (now - _time(value.get('observed_at'))).total_seconds()
    if not 0 <= age <= 86400:
        raise ValueError('Deployment evidence is stale or from the future; reacquire it.')
    artifacts = value.get('artifacts')
    if not isinstance(artifacts, list) or not 1 <= len(artifacts) <= 256:
        raise ValueError('Evidence receipt must bind actual private artifacts, not status flags alone.')
    seen = set()
    for artifact in artifacts:
        if (not isinstance(artifact, dict) or not {'path','sha256','size','role'} <= set(artifact) or
                set(artifact) - {'path','sha256','size','role','target'} or
                not isinstance(artifact['role'], str) or not artifact['role']):
            raise ValueError('Malformed evidence artifact manifest.')
        name = artifact['path']
        if not isinstance(name, str) or Path(name).is_absolute() or '..' in Path(name).parts or name in seen:
            raise ValueError('Duplicate or unconfined evidence artifact path.')
        seen.add(name)
        target = (private / name).resolve()
        if private.resolve() not in target.parents or not target.is_file():
            raise ValueError('Evidence artifact is missing or escapes private custody.')
        if (type(artifact['size']) is not int or not 0 < artifact['size'] <= 33554432 or
                target.stat().st_size != artifact['size'] or
                hashlib.sha256(target.read_bytes()).hexdigest() != artifact['sha256']):
            raise ValueError('Evidence artifact size/hash readback failed.')
    by_role = {}
    for artifact in artifacts:
        by_role.setdefault(artifact['role'], []).append(artifact)
    if expected_status == 'BACKUP_VERIFIED':
        originals = by_role.get('original', [])
        if not originals or any(not isinstance(a.get('target'), str) or not a['target'] for a in originals):
            raise ValueError('Backup manifest must bind original artifacts to exact mutation targets.')
    elif expected_status == 'ROLLBACK_REHEARSED':
        if len(by_role.get('inverse', [])) != 1 or len(by_role.get('rehearsal', [])) != 1:
            raise ValueError('Rollback needs one exact inverse script and its rehearsal artifact.')
        rehearsal = json.loads((private / by_role['rehearsal'][0]['path']).read_text())
        if (rehearsal.get('status') != 'ROLLBACK_REHEARSAL_PASS' or
                rehearsal.get('inverse_sha256') != by_role['inverse'][0]['sha256'] or
                rehearsal.get('model') != profile['model'] or rehearsal.get('firmware') != profile['firmware'] or
                not isinstance(rehearsal.get('source_commit'), str) or
                rehearsal['source_commit'] != value.get('source_commit') or
                not 0 <= (now - _time(rehearsal.get('observed_at'))).total_seconds() <= 86400):
            raise ValueError('Rollback rehearsal does not prove this exact inverse and identity.')
    elif expected_status == 'READ_ONLY_INVENTORY':
        if len(by_role.get('inventory', [])) != 1:
            raise ValueError('Inventory receipt must bind its actual machine-readable record.')
        inventory = json.loads((private / by_role['inventory'][0]['path']).read_text())
        if inventory.get('schema') != 'quiet-tizen.inventory.v1' or not 0 <= (now - _time(inventory.get('collected_at'))).total_seconds() <= 86400:
            raise ValueError('Current inventory schema/time is invalid.')
        for key in ('model','firmware'):
            fact = inventory.get('device', {}).get(key, {})
            if (fact.get('status') != 'PROVEN' or fact.get('value') != profile[key] or
                    not 0 <= (now - _time(fact.get('observed_at'))).total_seconds() <= 86400):
                raise ValueError('Inventory artifact contradicts exact observed identity.')
        registry = inventory.get('databases', {}).get('package_app_info', {})
        if registry.get('status') != 'PROVEN' or registry.get('value', {}).get('complete') is not True:
            raise ValueError('Current package inventory is incomplete.')
    return value


def check_plan(plan, profile, operation, private):
    if not isinstance(plan, dict):
        raise ValueError('Deployment plan must be an object.')
    p = validate_profile(profile)
    require_capability(p, 'root_session')
    capability = plan.get('capability')
    if capability not in ('backup_capture', 'launcher_install', 'home', 'settings_bridge', 'cleanup'):
        raise ValueError('A separate qualified deployment phase is required.')
    require_capability(p, capability)
    if capability != 'backup_capture':
        require_capability(p, 'rollback_ready')
    if capability == 'launcher_install':
        require_capability(p, 'package_host')
    if (plan.get('model') != p['model'] or plan.get('firmware') != p['firmware'] or
            plan.get('profile_sha256') != profile_digest(p) or
            plan.get('operation_sha256') != canonical_digest(operation)):
        raise ValueError('Deployment plan does not bind this exact profile and operation.')
    if plan.get('owner_approval') != 'explicit':
        raise ValueError('Persistent writes require the separate explicit owner deployment go-ahead.')
    if _time(plan.get('expires_at')) <= datetime.now(timezone.utc):
        raise ValueError('Deployment plan has expired.')
    private = Path(private)
    mutations = plan.get('mutations')
    if (not isinstance(mutations, list) or not mutations or
            any(not isinstance(t, str) or not t or '\0' in t or '\n' in t for t in mutations) or
            len(mutations) != len(set(mutations))):
        raise ValueError('A reviewed exact mutation list is required.')
    if capability == 'backup_capture':
        import re
        arguments = operation.get('arguments')
        if (operation.get('action') != 'snapshot' or not isinstance(arguments, list) or len(arguments) != 3 or
                arguments[:2] != [p['model'], p['firmware']] or
                not isinstance(arguments[2], str) or not re.fullmatch('[A-Za-z0-9_-]{1,80}', arguments[2]) or
                mutations != ['/opt/usr/share/quiet-tizen-backups', '/opt/usr/share/quiet-tizen-backups/' + arguments[2]]):
            raise ValueError('Backup capture may only create its project-owned parent if absent and one new numbered snapshot directory.')
        required_receipts = (('inventory','READ_ONLY_INVENTORY'),
                             ('preservation_baseline','NATIVE_AV_REMOTE_BASELINE_PASS'))
    else:
        required_receipts = (('backup','BACKUP_VERIFIED'), ('rollback','ROLLBACK_REHEARSED'),
                             ('inventory','READ_ONLY_INVENTORY'),
                             ('preservation_baseline','NATIVE_AV_REMOTE_BASELINE_PASS'))
    for key, status in required_receipts:
        receipt = _receipt(plan.get(key), private, status, p)
        if key == 'backup' and not set(mutations) <= {a.get('target') for a in receipt['artifacts'] if a['role'] == 'original'}:
            raise ValueError('Every planned mutation must have an independently hashed original backup.')
        if key == 'preservation_baseline':
            from check_acceptance import evaluate
            matrix = receipt.get('matrix')
            if (not isinstance(matrix, dict) or matrix.get('firmware') != p['firmware'] or
                    evaluate(matrix, baseline_only=True, expected_model=p['model'])['recommendation'] != 'GO'):
                raise ValueError('Native AV and existing Wi-Fi remote baseline matrix has not passed.')
            bound = {(a['path'], a['sha256']) for a in receipt['artifacts'] if a['role'] == 'hardware-observations'}
            for row in matrix['capabilities']:
                evidence = row['baseline']['evidence']
                if (evidence['source'], evidence['artifact_sha256']) not in bound:
                    raise ValueError('Baseline hardware reference is not a retained hashed private artifact.')
    return plan


def load_plan(path, profile, operation, private, expected_digest=None):
    path, private = Path(path).resolve(), Path(private).resolve()
    if private not in path.parents:
        raise ValueError('Deployment plans contain private state; use .local/.')
    if path.stat().st_size > 1048576:
        raise ValueError('Deployment plan too large.')
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_digest:
        raise ValueError('Confirm the exact reviewed deployment plan SHA-256, including all mutations and evidence refs.')
    return check_plan(json.loads(data), profile, operation, private)
