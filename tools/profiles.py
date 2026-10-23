"""Exact identity and evidence gates. A profile describes facts, never owner consent."""
import copy
from datetime import datetime
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
CAPABILITIES = frozenset(('root_session', 'backup_capture', 'package_host', 'launcher_build',
                          'rollback_ready', 'launcher_install', 'home',
                          'settings_bridge', 'cleanup'))
SETTINGS = frozenset(('brightness', 'game-mode', 'sleep-timer', 'volume', 'mute',
                      'advanced', 'network', 'bluetooth'))
ESSENTIAL_APPS = frozenset(('org.tizen.menu', 'com.samsung.tv.quicklaunch',
                           'com.samsung.tv.csfs'))
REGISTRIES = frozenset(('/opt/dbspace/.pkgmgr_parser.db',))
PRESERVATION = frozenset(('native-av', 'network-remote', 'native-settings', 'updates', 'streaming-data'))
KEYS = frozenset(('profile_version', 'model', 'firmware', 'os', 'kernel', 'runtime',
                  'registry', 'launcher', 'launcher_config', 'groups', 'stop_markers',
                  'runtime_only_masks', 'preferences', 'was_cache', 'capabilities',
                  'evidence', 'protected_app_ids', 'settings_bridge', 'notes'))


def _text(value, pattern, name):
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ValueError(f'Invalid {name}.')
    return value


def app_id(value):
    return _text(value, r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', 'application ID')


def _unique_strings(values, name, validator=app_id):
    if not isinstance(values, list):
        raise ValueError(f'{name} must be an array.')
    for value in values:
        validator(value)
    if len(values) != len(set(values)):
        raise ValueError(f'Duplicate {name}.')
    return values


def validate_profile(value):
    if not isinstance(value, dict) or set(value) - KEYS:
        raise ValueError('Unknown or malformed profile schema fields.')
    p = copy.deepcopy(value)
    if type(p.get('profile_version')) is not int or p['profile_version'] != 1:
        raise ValueError('Unsupported profile_version; expected integer 1.')
    _text(p.get('model'), r'[A-Z0-9][A-Z0-9_]{0,63}', 'model')
    _text(p.get('firmware'), r'[A-Za-z0-9][A-Za-z0-9_.-]{2,127}', 'firmware')
    for key in ('os', 'kernel', 'runtime', 'notes'):
        if key in p and (not isinstance(p[key], str) or not p[key] or '\n' in p[key] or '\0' in p[key]):
            raise ValueError(f'Invalid {key}.')
    if 'registry' in p and p['registry'] not in REGISTRIES:
        raise ValueError('Unapproved registry path; qualify a separate schema implementation.')
    protected = set(ESSENTIAL_APPS)
    protected.update(_unique_strings(p.get('protected_app_ids', []), 'protected_app_ids'))
    if 'launcher' in p:
        host = p['launcher']
        if not isinstance(host, dict) or set(host) - {'app_id', 'dll', 'root', 'uid', 'owner', 'smack_label'}:
            raise ValueError('Malformed launcher schema.')
        app_id(host.get('app_id'))
        _text(host.get('dll'), r'[A-Za-z][A-Za-z0-9_.-]{0,95}\.dll', 'entry DLL')
        # The root must name precisely this package, with no traversal/symlink ambiguity.
        if 'root' in host and host['root'] != '/opt/usr/apps/' + host['app_id']:
            raise ValueError('Launcher root does not match package ID.')
        if 'uid' in host and (type(host['uid']) is not int or not 1 <= host['uid'] <= 65535):
            raise ValueError('Invalid launcher UID.')
        if 'owner' in host:
            _text(host['owner'], r'[0-9]{1,5}:[0-9]{1,5}', 'launcher owner')
        if 'smack_label' in host:
            _text(host['smack_label'], r'[A-Za-z0-9_:.-]{1,200}', 'SMACK label')
        protected.add(host['app_id'])
    if 'launcher_config' in p:
        c = p['launcher_config']
        if not isinstance(c, dict) or set(c) - {'inputs', 'default_source', 'settings', 'apps', 'viewer_app', 'native_settings_app', 'setting_options'}:
            raise ValueError('Malformed launcher_config.')
        inputs = c.get('inputs')
        if not isinstance(inputs, list) or not 1 <= len(inputs) <= 3:
            raise ValueError('Provide one to three explicitly observed input cards.')
        ids = []
        for source in inputs:
            if not isinstance(source, dict) or set(source) != {'id', 'label'}:
                raise ValueError('Invalid input card schema.')
            if type(source['id']) is not int or not 0 <= source['id'] <= 65535:
                raise ValueError('Invalid source ID.')
            _text(source['label'], r'[^\x00-\x1f\x7f]{1,80}', 'input label')
            ids.append(source['id'])
        if len(ids) != len(set(ids)) or type(c.get('default_source')) is not int or c['default_source'] not in ids:
            raise ValueError('Default source must be a unique configured input.')
        settings = _unique_strings(c.get('settings', []), 'settings')
        if set(settings) - SETTINGS:
            raise ValueError('Unsupported settings API; hide it until qualified.')
        _unique_strings(c.get('apps', []), 'catalog app IDs')
        if len(c.get('apps', [])) > 64:
            raise ValueError('Catalog allow-list exceeds the 64-app cache bound.')
        options = c.get('setting_options', {})
        if not isinstance(options, dict) or set(options) - {'game-mode', 'sleep-timer'}:
            raise ValueError('Unknown private setting value map.')
        required_options = set(settings) & {'game-mode', 'sleep-timer'}
        if set(options) != required_options:
            raise ValueError('Each configured enum setting requires its observed ordered value map.')
        for values in options.values():
            _unique_strings(values, 'setting options',
                            lambda s: _text(s, r'[A-Za-z0-9_.-]{1,32}', 'setting option'))
            if not 2 <= len(values) <= 32:
                raise ValueError('Setting option list must have 2..32 observed values.')
        for target in ('viewer_app', 'native_settings_app'):
            if target in c:
                app_id(c[target])
    groups = p.setdefault('groups', {})
    if not isinstance(groups, dict):
        raise ValueError('groups must be an object; inventory profiles may use {}.')
    all_apps = set()
    for name, group in groups.items():
        _text(name, r'[a-z][a-z0-9-]{0,39}', 'cleanup group')
        if not isinstance(group, dict) or set(group) - {'app_ids', 'description', 'dependency_evidence'}:
            raise ValueError('Invalid cleanup group schema.')
        ids = _unique_strings(group.get('app_ids'), 'cleanup app IDs')
        if set(ids) & (all_apps | protected):
            raise ValueError('Duplicate, essential, protected or launcher-host cleanup target.')
        all_apps.update(ids)
        if 'dependency_evidence' in group:
            d = group['dependency_evidence']
            if (not isinstance(d, dict) or d.get('status') != 'PROVEN' or
                    d.get('model') != p['model'] or d.get('firmware') != p['firmware'] or
                    not isinstance(d.get('source'), str) or not d['source'] or
                    not isinstance(d.get('preserves'), list) or
                    not all(isinstance(v, str) for v in d['preserves']) or
                    set(d['preserves']) != PRESERVATION):
                raise ValueError('Cleanup dependency evidence must prove exact-identity preservation.')
            _text(d.get('source_sha256'), r'[a-f0-9]{64}', 'dependency evidence SHA-256')
            try:
                if datetime.fromisoformat(d['observed_at'].replace('Z', '+00:00')).tzinfo is None:
                    raise ValueError('timezone missing')
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError('Dependency evidence needs a timestamp with timezone.') from error
    markers = p.get('stop_markers', [])
    if not isinstance(markers, list):
        raise ValueError('stop_markers must be an array.')
    units = set()
    for marker in markers:
        if not isinstance(marker, dict) or set(marker) != {'unit', 'marker', 'group'}:
            raise ValueError('Invalid stop marker.')
        _text(marker['unit'], r'[A-Za-z0-9_.@-]+\.service', 'service unit')
        _text(marker['marker'], r'[A-Za-z0-9_.-]+', 'stop marker name')
        if not isinstance(marker['group'], str) or marker['group'] not in groups or marker['unit'] in units:
            raise ValueError('Stop marker requires an existing group and unique unit.')
        units.add(marker['unit'])
    _unique_strings(p.get('runtime_only_masks', []), 'runtime masks',
                    lambda s: _text(s, r'[A-Za-z0-9_.@-]+\.service', 'unit'))
    preferences = p.get('preferences', [])
    if not isinstance(preferences, list):
        raise ValueError('preferences must be an array.')
    pref_keys = set()
    for pref in preferences:
        if not isinstance(pref, dict) or set(pref) != {'key', 'type', 'value'}:
            raise ValueError('Invalid preference schema.')
        _text(pref['key'], r'db/[A-Za-z0-9_/-]{1,180}', 'preference key')
        if pref['key'] in pref_keys or pref['type'] not in ('bool','int','string') or not isinstance(pref['value'], str):
            raise ValueError('Invalid or duplicate preference.')
        pref_keys.add(pref['key'])
        if '\0' in pref['value'] or '\n' in pref['value']:
            raise ValueError('Invalid preference value.')
    if 'was_cache' in p and (not isinstance(p['was_cache'], dict) or
            set(p['was_cache']) != {'table','row','json_path','after'} or
            p['was_cache']['table'] != 'WASConfigTable' or
            p['was_cache']['row'] != 'RESERVED_18' or
            p['was_cache']['json_path'] != '$.rsp.preloadByCountry' or
            p['was_cache']['after'] != 'false'):
        raise ValueError('Unknown WAS cache schema; qualify independently.')
    if 'settings_bridge' in p:
        bridge = p['settings_bridge']
        if (not isinstance(bridge, dict) or set(bridge) != {'registry', 'table', 'routes'} or
                bridge['registry'] != '/opt/dbspace/.app-context.db' or bridge['table'] != 'key_filter' or
                not isinstance(bridge['routes'], list) or not bridge['routes'] or 'launcher' not in p):
            raise ValueError('Invalid or unqualified settings bridge schema.')
        matches = set()
        for route in bridge['routes']:
            if (not isinstance(route, dict) or set(route) != {'match', 'after'} or
                    not isinstance(route['match'], dict) or set(route['match']) != {'appid', 'key'} or
                    not isinstance(route['after'], dict) or set(route['after']) != {'target_selector', 'args'}):
                raise ValueError('Settings routes require exact semantic selectors and fields.')
            match, after = route['match'], route['after']
            app_id(match['appid'])
            if match['key'] != 'XF86More':
                raise ValueError('Only the observed More route is eligible; preserve native Settings and every other remote key.')
            pair = (match['appid'], match['key'])
            if pair in matches:
                raise ValueError('Duplicate settings route.')
            matches.add(pair)
            if after['target_selector'] != 'package:' + p['launcher']['app_id']:
                raise ValueError('Settings route must target the qualified launcher.')
            _text(after['args'], r'[^\x00-\x1f\x7f]{1,240}', 'settings arguments')
    caps = p.setdefault('capabilities', {})
    evidence = p.setdefault('evidence', {})
    if not isinstance(caps, dict) or set(caps) - CAPABILITIES or any(type(v) is not bool for v in caps.values()):
        raise ValueError('Capabilities must be explicitly named booleans.')
    if not isinstance(evidence, dict):
        raise ValueError('Evidence must be an object.')
    for name, enabled in caps.items():
        if not enabled:
            continue
        e = evidence.get(name)
        if not isinstance(e, dict) or e.get('status') != 'PROVEN' or not e.get('source'):
            raise ValueError(f'{name} needs bound PROVEN evidence.')
        if e.get('model') != p['model'] or e.get('firmware') != p['firmware']:
            raise ValueError('Capability evidence contradicts exact profile identity.')
        try:
            observed = datetime.fromisoformat(e['observed_at'].replace('Z', '+00:00'))
            if observed.tzinfo is None:
                raise ValueError('Evidence timestamp needs timezone.')
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError('Capability needs an observation timestamp with timezone.') from error
        if e.get('scope') not in ('offline', 'device') or (name != 'launcher_build' and e['scope'] != 'device'):
            raise ValueError('Offline evidence cannot qualify device capabilities.')
    if caps.get('launcher_build') and not {'launcher', 'launcher_config'} <= set(p):
        raise ValueError('Launcher build needs an explicit host and configuration.')
    if caps.get('cleanup') and (not groups or any('dependency_evidence' not in g for g in groups.values())):
        raise ValueError('Qualified cleanup requires dependency evidence for every group.')
    return p


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError(f'Duplicate JSON key: {key}')
        result[key] = value
    return result


def load_profile(path):
    return validate_profile(json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=_pairs))


def load_profiles(directory=ROOT / 'profiles'):
    items = [load_profile(path) for path in sorted(Path(directory).glob('*.json'))]
    identities = [(p['model'], p['firmware']) for p in items]
    if len(identities) != len(set(identities)):
        raise ValueError('Duplicate model/firmware profiles; select one authoritative definition.')
    return items


def check_identity(model, firmware, profiles=None):
    candidates = load_profiles() if profiles is None else profiles
    matches = [p for p in candidates if p['model'] == model and p['firmware'] == firmware]
    if len(matches) != 1:
        raise ValueError('Exact model/firmware profile is missing or ambiguous; inventory first.')
    return validate_profile(matches[0])


def resolve_firmware(observed, declared):
    if observed and declared and observed != declared:
        raise ValueError('Declared firmware contradicts live firmware; no override is allowed.')
    return observed or declared


def require_capability(profile, name):
    p = validate_profile(profile)
    if name not in CAPABILITIES or p['capabilities'].get(name) is not True:
        raise ValueError(f'{name} is not qualified by this exact profile; inventory evidence grants no mutation authority.')
    return p
