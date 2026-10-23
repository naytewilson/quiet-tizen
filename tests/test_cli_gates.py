from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import tv


class CliGateTests(unittest.TestCase):
    def test_exec_and_push_without_profile_never_contact_device(self):
        for action in (['exec','--command','true'], ['push','absent.tar.gz','package.tar.gz']):
            with self.subTest(action=action), patch('sys.argv',['tv.py','--host','192.0.2.50',*action]), \
                    patch('tv.SdbClient') as client, patch('tv.find_sdb') as find:
                with self.assertRaises(ValueError):
                    tv.main()
                client.assert_not_called()
                find.assert_not_called()

    def test_inventory_profile_cannot_authorize_exec(self):
        args=['tv.py','--host','192.0.2.50','--profile',str(ROOT/'profiles/qe65s95fauxru-1301.0.json'),
              '--acknowledge-temporary-root','exec','--command','true']
        with patch('sys.argv',args),patch('tv.SdbClient') as client:
            with self.assertRaises(ValueError):
                tv.main()
            client.assert_not_called()

    def test_root_proof_requires_explicit_ack_before_contact(self):
        args=['tv.py','--host','192.0.2.50','--profile',str(ROOT/'profiles/un75u8000ffxza-1300.9.json'),'root-check']
        with patch('sys.argv',args),patch('tv.SdbClient') as client:
            with self.assertRaises(ValueError):
                tv.main()
            client.assert_not_called()

    def test_operation_draft_is_local_and_grants_no_authority(self):
        args=['tv.py','--host','192.0.2.50','--profile',str(ROOT/'profiles/un75u8000ffxza-1300.9.json'),
              '--describe-operation','exec','--command','printf reviewed']
        with patch('sys.argv',args),patch('tv.SdbClient') as client,patch('builtins.print') as output:
            tv.main()
            client.assert_not_called()
            self.assertIn('DRAFT_UNAUTHORIZED',output.call_args.args[0])

    def test_conflicting_product_model_stops_before_root(self):
        args=['tv.py','--host','192.0.2.50','--profile',str(ROOT/'profiles/un75u8000ffxza-1300.9.json'),
              '--acknowledge-temporary-root','root-check']
        facts={'device':{'model':{'status':'UNKNOWN','value':None,'error':'MODEL_CONFLICT'},
                         'firmware':{'status':'PROVEN','value':'T-KSU2EFAKUC-0090-1300.9'}}}
        with patch('sys.argv',args),patch('tv.SdbClient'),patch('tv.find_sdb'), \
                patch('tv.observed_model',return_value='UN75U8000FFXZA'),patch('tv.save_private'), \
                patch('inventory.collect_inventory',return_value=facts),patch('tv.run_root') as root:
            with self.assertRaisesRegex(ValueError,'model'):
                tv.main()
            root.assert_not_called()

    def test_inspect_uses_only_the_supported_read_only_producer_without_root_ack(self):
        args=['tv.py','--host','192.0.2.50','--profile',str(ROOT/'profiles/un75u8000ffxza-1300.9.json'),'inspect']
        facts={'schema':'quiet-tizen.launcher-host.v1','artifact_manifests':[]}
        with patch('sys.argv',args),patch('tv.SdbClient'),patch('tv.find_sdb'),patch('tv.save_private') as save, \
                patch('inspect_artifacts.collect',return_value=facts) as collect,patch('tv.run_root') as root,patch('builtins.print'):
            tv.main()
            collect.assert_called_once()
            root.assert_not_called()
            self.assertEqual(json.loads(save.call_args.args[1]),facts)

    def test_snapshot_result_is_wired_to_private_target_bound_pull_manifests(self):
        profile=tv.PROFILE
        script=(ROOT/'recipes/snapshot.sh').read_bytes()
        operation={'action':'snapshot','script_sha256':hashlib.sha256(script).hexdigest(),
                   'arguments':[profile['model'],profile['firmware'],'phase_01']}
        from deployment import canonical_digest
        args=['tv.py','--host','192.0.2.50','--profile',str(ROOT/'profiles/un75u8000ffxza-1300.9.json'),
              '--acknowledge-temporary-root','--deployment-plan',str(ROOT/'.local/fixture-plan.json'),
              '--confirm-operation-sha256',canonical_digest(operation),'--confirm-plan-sha256','a'*64,'snapshot','phase_01']
        facts={'device':{k:{'status':'PROVEN','value':profile[k]} for k in ('model','firmware')}}
        parsed={'artifact_manifests':[{'kind':'backup','target':'192.0.2.50:26101'}]}
        with patch('sys.argv',args),patch('tv.SdbClient') as client,patch('tv.find_sdb'), \
                patch('tv.observed_model',return_value=profile['model']),patch('tv.save_private') as save, \
                patch('inventory.collect_inventory',return_value=facts), \
                patch('deployment.load_plan',return_value={'capability':'backup_capture'}), \
                patch('tv.run_root',return_value='verified snapshot report'), \
                patch('inspect_artifacts.from_snapshot_report',return_value=parsed) as parser,patch('builtins.print'):
            client.return_value.serial='192.0.2.50:26101'
            tv.main()
            report=parser.call_args.args[0]
            self.assertEqual(report['schema'],'quiet-tizen.snapshot-report.v1')
            self.assertEqual(report['target'],'192.0.2.50:26101')
            self.assertEqual(report['stdout'],'verified snapshot report')
            self.assertTrue(any('snapshot-pull-' in str(call.args[0]) for call in save.call_args_list))


if __name__=='__main__':
    unittest.main()
