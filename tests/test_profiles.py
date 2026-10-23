import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import profiles


def inventory(model='MODEL_A', firmware='T-TEST-0000-1.0'):
    return {'profile_version': 1, 'model': model, 'firmware': firmware}


class ProfileTests(unittest.TestCase):
    def test_inventory_is_valid_but_cannot_authorize_mutation(self):
        p = profiles.validate_profile(inventory())
        self.assertEqual(p['groups'], {})
        for capability in profiles.CAPABILITIES:
            with self.subTest(capability=capability), self.assertRaises(ValueError):
                profiles.require_capability(p, capability)

    def test_all_model_firmware_cross_pairs_fail_closed(self):
        items = [profiles.validate_profile(inventory('MODEL_A', 'T-A-0000-1.0')),
                 profiles.validate_profile(inventory('MODEL_B', 'T-B-0000-1.0'))]
        for m in items:
            self.assertEqual(profiles.check_identity(m['model'], m['firmware'], items), m)
            for f in items:
                if m is not f:
                    with self.assertRaises(ValueError):
                        profiles.check_identity(m['model'], f['firmware'], items)
        with self.assertRaises(ValueError):
            profiles.check_identity('UNKNOWN', 'T-A-0000-1.0', items)
        with self.assertRaises(ValueError):
            profiles.check_identity('MODEL_A', None, items)

    def test_duplicate_identity_and_duplicate_json_keys_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for name in ('one.json', 'two.json'):
                (root / name).write_text(json.dumps(inventory()))
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                profiles.load_profiles(root)
            (root / 'one.json').write_text('{"profile_version":1,"model":"A","model":"B","firmware":"X"}')
            with self.assertRaises(ValueError):
                profiles.load_profile(root / 'one.json')

    def test_malformed_schema_paths_and_capability_truthiness_rejected(self):
        changes = ({'profile_version': True}, {'model': 'has spaces'}, {'firmware': ''},
                   {'registry': '/etc/passwd'}, {'groups': []},
                   {'capabilities': {'cleanup': 'true'}}, {'capabilities': {'unknown': True}},
                   {'launcher': {'app_id':'com.example.app','dll':'../bad.dll'}},
                   {'launcher': {'app_id':'com.example.app','dll':'App.dll','root':'/opt/usr/apps/other'}},
                   {'launcher_config': {'inputs':[{'id':True,'label':'HDMI'}], 'default_source':True}},
                   {'launcher_config': {'inputs':[{'id':1,'label':'HDMI'}], 'default_source':1,
                                        'settings':['game-mode']}},
                   {'launcher_config': {'inputs':[{'id':1,'label':'HDMI'}], 'default_source':1,
                                        'apps':['com.test.app'+str(i) for i in range(65)]}},
                   {'unexpected': 'field'})
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                profiles.validate_profile({**inventory(), **change})

    def test_protect_native_host_essential_and_owner_selected_apps(self):
        for app in ('org.tizen.menu', 'com.example.host', 'com.owner.streaming'):
            p = {**inventory(), 'launcher':{'app_id':'com.example.host','dll':'Host.dll'},
                 'protected_app_ids':['com.owner.streaming'],
                 'groups':{'advertising':{'description':'synthetic', 'app_ids':[app]}}}
            with self.subTest(app=app), self.assertRaises(ValueError):
                profiles.validate_profile(p)

    def test_duplicate_cleanup_apps_and_missing_marker_group_rejected(self):
        p = {**inventory(), 'groups':{'a':{'app_ids':['com.example.optional']},
                                     'b':{'app_ids':['com.example.optional']}}}
        with self.assertRaises(ValueError):
            profiles.validate_profile(p)
        with self.assertRaises(ValueError):
            profiles.validate_profile({**inventory(), 'stop_markers':[{'unit':'a.service','marker':'a','group':'missing'}]})

    def test_capability_needs_bound_proven_evidence(self):
        p = {**inventory(), 'capabilities':{'cleanup':True}}
        with self.assertRaises(ValueError):
            profiles.validate_profile(p)

    def test_live_firmware_disagreement_is_never_overridden(self):
        self.assertEqual(profiles.resolve_firmware('T-A-0000-1.0', 'T-A-0000-1.0'), 'T-A-0000-1.0')
        with self.assertRaisesRegex(ValueError, 'contradict'):
            profiles.resolve_firmware('T-A-0000-1.0', 'T-B-0000-1.0')
        self.assertIsNone(profiles.resolve_firmware(None, None))
        self.assertEqual(profiles.resolve_firmware(None, 'T-A-0000-1.0'), 'T-A-0000-1.0')


if __name__ == '__main__':
    unittest.main()
