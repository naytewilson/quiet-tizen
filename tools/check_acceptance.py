"""Fail-closed native AV/remote release checklist; records do not manufacture tests."""
import argparse
import json
from pathlib import Path
import re
from datetime import datetime, timezone

REQUIRED = (
    'picture-4k-upscaling', 'picture-hdr', 'picture-modes-advanced',
    'gaming-game-mode-allm', 'gaming-console-input',
    'audio-internal-volume-mute', 'audio-modes-sync', 'audio-external-formats',
    'hdmi-every-port', 'hdmi-cec-naming', 'hdmi-hotplug', 'hdmi-arc-support',
    'bluetooth-audio', 'network-apps-discovery', 'streaming-drm-logins-hdr',
    'native-settings-crash-fallback', 'lifecycle-coldboot-restart',
    'lifecycle-standby-wake', 'lifecycle-switch-reconnect-crash',
    'wifi-remote-navigation-home-back', 'wifi-remote-volume-mute-power',
    'wifi-remote-app-input', 'wifi-remote-reconnect-no-repair',
)


def _fresh_timestamp(value):
    try:
        observed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if observed.tzinfo is None:
            return False
        age = (datetime.now(timezone.utc) - observed).total_seconds()
        return 0 <= age <= 86400
    except (AttributeError, ValueError, TypeError):
        return False


def _text(value, maximum=4096):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum and '\0' not in value


def evaluate(document, *, baseline_only=False, expected_model='UN65DU7200BXZA'):
    missing = []
    if (document.get('model') != expected_model or not isinstance(document.get('firmware'), str) or
            not re.fullmatch(r'T-[A-Z0-9]+-[0-9]{4}-[0-9]{4}\.[0-9]+', document['firmware'])):
        missing.append('current exact model/firmware')
    source_states = ('BASELINE_ON_DEVICE', 'TESTED_ON_DU7200') if baseline_only else ('TESTED_ON_DU7200',)
    if (document.get('source_status') not in source_states or
            not isinstance(document.get('source_commit'), str) or
            not re.fullmatch(r'[a-f0-9]{40}', document['source_commit'])):
        missing.append('exact tested source commit and device-tested evidence status')
    rows = document.get('capabilities')
    if not isinstance(rows, list):
        rows = []
    ids = [row.get('id') for row in rows if isinstance(row, dict)]
    if len(ids) != len(set(ids)):
        missing.append('duplicate capability rows')
    stages = ('baseline',) if baseline_only else ('baseline','post_install','restored')
    for name in REQUIRED:
        found = [row for row in rows if isinstance(row, dict) and row.get('id') == name]
        if len(found) != 1:
            missing.append(f'{name}: exactly one row required')
            continue
        row = found[0]
        for key in ('test_method', 'equipment', 'recovery'):
            if not _text(row.get(key)):
                missing.append(f'{name}: missing {key}')
        for stage in stages:
            result = row.get(stage, {})
            if not isinstance(result, dict):
                missing.append(f'{name}: {stage} malformed result')
                continue
            # NOT_SUPPORTED requires evidence from this hardware too. NOT_TESTABLE,
            # UNKNOWN, SKIP and a missing baseline never become a release pass.
            if result.get('status') not in ('PASS','NOT_SUPPORTED'):
                missing.append(f'{name}: {stage} not passed')
            evidence = result.get('evidence', {})
            if (not isinstance(evidence, dict) or evidence.get('status') != 'PROVEN' or
                    evidence.get('method') != 'actual-hardware' or not _text(evidence.get('source')) or
                    not _fresh_timestamp(evidence.get('observed_at')) or
                    not isinstance(evidence.get('artifact_sha256'), str) or
                    not re.fullmatch(r'[a-f0-9]{64}', evidence['artifact_sha256']) or
                    evidence.get('model') != document.get('model') or
                    evidence.get('firmware') != document.get('firmware')):
                missing.append(f'{name}: {stage} missing bound hardware evidence')
            if name == 'wifi-remote-reconnect-no-repair':
                pairing = evidence.get('remote_pairing', {}) if isinstance(evidence, dict) else {}
                if (result.get('status') != 'PASS' or not isinstance(pairing, dict) or
                        pairing.get('repaired') is not False or pairing.get('credentials_unchanged') is not True or
                        pairing.get('reconnected_after_reboot') is not True or
                        pairing.get('reconnected_after_standby') is not True or
                        pairing.get('topology') != 'ethernet-tv_wifi-client'):
                    missing.append(f'{name}: {stage} wired-TV/Wi-Fi-client pairing preservation/reconnect not proven')
        if not baseline_only and any(not isinstance(row.get(stage), dict) or not isinstance(row.get('baseline'), dict)
                                     or row[stage].get('status') != row['baseline'].get('status')
                                     for stage in ('post_install','restored')):
            missing.append(f'{name}: supported capability differs from baseline')
    if document.get('known_regressions') != []:
        missing.append('known regressions are present or undeclared')
    # Performance/accuracy claims require separate measured evidence; observable
    # functional equivalence does not prove identical latency, HDR accuracy or fidelity.
    return {'recommendation':'NO-GO' if missing else 'GO',
            'scope':'baseline' if baseline_only else 'production',
            'missing':missing,
            'evidence_policy':'Record validation requires independent inspection of referenced hardware artifacts; assertions are not authenticated by this checker.',
            'performance_equivalence':'UNMEASURED unless separately instrumented'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('matrix', type=Path)
    parser.add_argument('--baseline-only', action='store_true')
    args=parser.parse_args()
    result=evaluate(json.loads(args.matrix.read_text()),baseline_only=args.baseline_only)
    print(json.dumps(result,indent=2))
    return 0 if result['recommendation']=='GO' else 1


if __name__=='__main__':
    raise SystemExit(main())
