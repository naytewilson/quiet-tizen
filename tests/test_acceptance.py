import copy
from pathlib import Path
import sys
import unittest
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from check_acceptance import REQUIRED,evaluate


class AcceptanceTests(unittest.TestCase):
    def synthetic(self):
        evidence={'status':'PROVEN','method':'actual-hardware','source':'SYNTHETIC TEST ONLY',
                  'observed_at':datetime.now(timezone.utc).isoformat(),'model':'UN65DU7200BXZA','firmware':'T-TEST-0000-1000.0',
                  'artifact_sha256':'a'*64,
                  'remote_pairing':{'repaired':False,'credentials_unchanged':True,
                    'reconnected_after_reboot':True,'reconnected_after_standby':True,
                    'topology':'ethernet-tv_wifi-client'}}
        return {'model':'UN65DU7200BXZA','firmware':'T-TEST-0000-1000.0','known_regressions':[],
                'source_status':'TESTED_ON_DU7200','source_commit':'f'*40,
                'capabilities':[{'id':name,'test_method':'synthetic fixture','equipment':'fixture',
                    'recovery':'fixture','baseline':{'status':'PASS','evidence':evidence},
                    'post_install':{'status':'PASS','evidence':evidence},
                    'restored':{'status':'PASS','evidence':evidence}} for name in REQUIRED]}

    def test_missing_baseline_and_unmeasured_features_never_pass(self):
        self.assertEqual(evaluate({})['recommendation'],'NO-GO')
        matrix=self.synthetic()
        matrix['capabilities'][0]['baseline']={'status':'NOT_TESTABLE'}
        self.assertEqual(evaluate(matrix)['recommendation'],'NO-GO')

    def test_network_remote_reconnect_without_repair_is_mandatory(self):
        matrix=self.synthetic()
        self.assertEqual(evaluate(matrix)['recommendation'],'GO')
        matrix['capabilities']=[r for r in matrix['capabilities'] if r['id']!='wifi-remote-reconnect-no-repair']
        self.assertEqual(evaluate(matrix)['recommendation'],'NO-GO')

    def test_losing_previously_supported_function_cannot_be_not_supported_pass(self):
        matrix=self.synthetic()
        matrix['capabilities'][0]['post_install']['status']='NOT_SUPPORTED'
        self.assertEqual(evaluate(matrix)['recommendation'],'NO-GO')

    def test_remote_repair_or_missing_pairing_preservation_blocks_release(self):
        for change in ({'repaired':True},{'credentials_unchanged':False},
                       {'reconnected_after_reboot':False},{'reconnected_after_standby':False},
                       {'topology':'same-computer-only'}):
            matrix=self.synthetic()
            row=next(r for r in matrix['capabilities'] if r['id']=='wifi-remote-reconnect-no-repair')
            row['post_install']['evidence']['remote_pairing'].update(change)
            with self.subTest(change=change):
                self.assertEqual(evaluate(matrix)['recommendation'],'NO-GO')

    def test_malformed_or_stale_evidence_time_and_missing_hash_fail_closed(self):
        for change in ({'observed_at':'not-a-time'},{'observed_at':'2001-01-01T00:00:00Z'},
                       {'observed_at':'2999-01-01T00:00:00Z'},{'artifact_sha256':'missing'}):
            matrix=self.synthetic()
            matrix['capabilities'][0]['baseline']['evidence'].update(change)
            with self.subTest(change=change):
                self.assertEqual(evaluate(matrix)['recommendation'],'NO-GO')

    def test_mocks_wrong_firmware_known_regression_and_duplicate_fail(self):
        for kind in ('mock','firmware','regression','duplicate'):
            matrix=self.synthetic()
            if kind=='mock':
                matrix['capabilities'][0]['baseline']['evidence']={**matrix['capabilities'][0]['baseline']['evidence'],'method':'mock'}
            elif kind=='firmware':
                matrix['firmware']='DIFFERENT'
            elif kind=='regression':
                matrix['known_regressions']=['remote app had to re-pair']
            else:
                matrix['capabilities'].append(copy.deepcopy(matrix['capabilities'][0]))
            with self.subTest(kind=kind):
                self.assertEqual(evaluate(matrix)['recommendation'],'NO-GO')


if __name__=='__main__':
    unittest.main()
