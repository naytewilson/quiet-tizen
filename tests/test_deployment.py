import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import deployment


class DeploymentTests(unittest.TestCase):
    def test_unqualified_profile_never_accepts_an_approval_boolean(self):
        from profiles import load_profile
        p = load_profile(ROOT / 'profiles/un75u8000ffxza-1300.9.json')
        with self.assertRaises(ValueError):
            deployment.check_plan({'owner_approval':'explicit'}, p, {'action':'exec'}, ROOT / '.local')

    def fixture(self, root):
        p = {'profile_version':1, 'model':'TEST_MODEL', 'firmware':'T-TEST-0000-1000.0',
             'capabilities':{}, 'evidence':{}}
        now = datetime.now(timezone.utc)
        for name in ('root_session','rollback_ready','home'):
            p['capabilities'][name] = True
            p['evidence'][name] = {'status':'PROVEN','scope':'device','source':'synthetic fixture only',
                'model':p['model'],'firmware':p['firmware'],'observed_at':now.isoformat()}
        operation = {'action':'exec','command_sha256':'a'*64, 'arguments':[]}
        plan = {'model':p['model'],'firmware':p['firmware'],'profile_sha256':deployment.profile_digest(p),
                'operation_sha256':deployment.canonical_digest(operation),'capability':'home',
                'mutations':['vconf:db/appfw/home_appid'],
                'owner_approval':'explicit','expires_at':(now+timedelta(minutes=5)).isoformat()}
        def artifact(name,role,data,target=None):
            path=root/name
            path.write_bytes(data)
            ref={'path':name,'role':role,'size':len(data),'sha256':hashlib.sha256(data).hexdigest()}
            if target:ref['target']=target
            return ref
        for key, status in (('backup','BACKUP_VERIFIED'),('rollback','ROLLBACK_REHEARSED'),
                            ('inventory','READ_ONLY_INVENTORY'),
                            ('preservation_baseline','NATIVE_AV_REMOTE_BASELINE_PASS')):
            receipt = {'model':p['model'],'firmware':p['firmware'],'status':status,
                       'observed_at':now.isoformat(), 'source_commit':'f'*40}
            if key=='backup':
                receipt['artifacts']=[artifact('original.json','original',b'{"original":"fixture"}',plan['mutations'][0])]
            elif key=='rollback':
                inverse=artifact('inverse.sh','inverse',b'#!/bin/sh\n# synthetic inverse fixture\n')
                result={'status':'ROLLBACK_REHEARSAL_PASS','inverse_sha256':inverse['sha256'],
                        'model':p['model'],'firmware':p['firmware'],
                        'observed_at':now.isoformat(),'source_commit':'f'*40}
                receipt['artifacts']=[inverse,artifact('rehearsal.json','rehearsal',json.dumps(result).encode())]
            elif key=='inventory':
                facts={'schema':'quiet-tizen.inventory.v1','collected_at':now.isoformat(),
                       'device':{k:{'status':'PROVEN','value':p[k],'observed_at':now.isoformat()} for k in ('model','firmware')},
                       'databases':{'package_app_info':{'status':'PROVEN','value':{'complete':True,'rows':[],'row_count':0}}}}
                receipt['artifacts']=[artifact('facts.json','inventory',json.dumps(facts).encode())]
            if key=='preservation_baseline':
                from check_acceptance import REQUIRED
                source=artifact('hardware-observations.json','hardware-observations',b'SYNTHETIC OBSERVATION FIXTURE ONLY')
                receipt['artifacts']=[source]
                observed={'status':'PROVEN','method':'actual-hardware','source':source['path'],
                          'observed_at':now.isoformat(),'model':p['model'],'firmware':p['firmware'],
                          'artifact_sha256':source['sha256'],
                          'remote_pairing':{'repaired':False,'credentials_unchanged':True,
                            'reconnected_after_reboot':True,'reconnected_after_standby':True,
                            'topology':'ethernet-tv_wifi-client'}}
                receipt['matrix']={'model':p['model'],'firmware':p['firmware'],'known_regressions':[],
                    'source_status':'BASELINE_ON_DEVICE','source_commit':'f'*40,
                    'capabilities':[{'id':name,'test_method':'fixture','equipment':'fixture',
                                     'recovery':'fixture','baseline':{'status':'PASS','evidence':observed}} for name in REQUIRED]}
            path = root / (key + '.json')
            path.write_text(json.dumps(receipt))
            plan[key] = {'path':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        return p, operation, plan

    def test_bound_plan_and_receipts_are_required(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p, operation, plan = self.fixture(root)
            self.assertEqual(deployment.check_plan(plan,p,operation,root)['capability'],'home')
            for key,value in (('model','OTHER'),('firmware','T-OTHER-2.0'),
                              ('profile_sha256','b'*64),('operation_sha256','b'*64),
                              ('owner_approval',True),('expires_at','2001-01-01T00:00:00+00:00')):
                altered = {**plan,key:value}
                with self.subTest(key=key),self.assertRaises(ValueError):
                    deployment.check_plan(altered,p,operation,root)

    def test_receipt_tamper_and_escape_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            p,operation,plan=self.fixture(root)
            (root/'backup.json').write_text('{}')
            with self.assertRaises(ValueError):
                deployment.check_plan(plan,p,operation,root)
            p,operation,plan=self.fixture(root)
            plan['rollback']['path']='../elsewhere.json'
            with self.assertRaises(ValueError):
                deployment.check_plan(plan,p,operation,root)

    def test_inventory_receipt_is_not_backup_proof(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            p,operation,plan=self.fixture(root)
            plan['backup']=plan['inventory']
            with self.assertRaises(ValueError):
                deployment.check_plan(plan,p,operation,root)

    def test_underlying_original_inverse_and_inventory_artifacts_are_not_just_flags(self):
        for name in ('original.json','inverse.sh','facts.json','hardware-observations.json'):
            with tempfile.TemporaryDirectory() as d:
                root=Path(d);p,operation,plan=self.fixture(root)
                (root/name).write_bytes(b'tampered')
                with self.subTest(name=name),self.assertRaises(ValueError):
                    deployment.check_plan(plan,p,operation,root)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p,operation,plan=self.fixture(root)
            plan['mutations'].append('/unbacked/target')
            with self.assertRaises(ValueError):
                deployment.check_plan(plan,p,operation,root)

    def test_plan_confirmation_binds_mutations_and_all_receipt_references(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p,operation,plan=self.fixture(root)
            path=root/'plan.json';path.write_text(json.dumps(plan))
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            deployment.load_plan(path,p,operation,root,digest)
            plan['mutations'].append('vconf:unapproved')
            path.write_text(json.dumps(plan))
            with self.assertRaisesRegex(ValueError,'SHA-256'):
                deployment.load_plan(path,p,operation,root,digest)

    def test_first_snapshot_capture_cannot_be_used_for_arbitrary_exec_or_existing_state_changes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p,operation,plan=self.fixture(root)
            p['capabilities']['backup_capture']=True
            p['evidence']['backup_capture']={**p['evidence']['root_session']}
            operation={'action':'snapshot','script_sha256':'a'*64,'arguments':[p['model'],p['firmware'],'original_01']}
            plan.update(capability='backup_capture',mutations=['/opt/usr/share/quiet-tizen-backups','/opt/usr/share/quiet-tizen-backups/original_01'],
                        operation_sha256=deployment.canonical_digest(operation),profile_sha256=deployment.profile_digest(p))
            del plan['backup'];del plan['rollback']
            self.assertEqual(deployment.check_plan(plan,p,operation,root)['capability'],'backup_capture')
            operation['action']='exec'
            plan['operation_sha256']=deployment.canonical_digest(operation)
            with self.assertRaises(ValueError):
                deployment.check_plan(plan,p,operation,root)


if __name__=='__main__':
    unittest.main()
