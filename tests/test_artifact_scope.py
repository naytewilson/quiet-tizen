from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from tv import pull_artifact,PROFILE


class ArtifactScopeTests(unittest.TestCase):
    def test_only_exact_current_artifact_path_and_destination_are_eligible(self):
        (ROOT/'.local').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/'.local') as d:
            target=Path(d)/'Tizen.NUI.dll'
            manifest=Path(d)/'artifact.json'
            remote='/usr/share/dotnet.tizen/framework/Tizen.NUI.dll'
            doc={'model':PROFILE['model'],'firmware':PROFILE['firmware'],'remote':remote,
                 'target':'192.0.2.50:26101',
                 'destination':str(target.relative_to(ROOT/'.local')),'kind':'framework',
                 'sha256':hashlib.sha256(b'fixture').hexdigest(),'size':7,
                 'observed_at':datetime.now(timezone.utc).isoformat()}
            manifest.write_text(json.dumps(doc))
            self.assertEqual(pull_artifact(manifest,PROFILE,remote,target,'192.0.2.50:26101')['size'],7)
            for field,value in (('remote','/etc/shadow'),('model','OTHER'),('size',33554433),
                                ('destination','elsewhere.dll'),('observed_at','2001-01-01T00:00:00Z'),
                                ('target','192.0.2.99:26101')):
                changed={**doc,field:value}
                manifest.write_text(json.dumps(changed))
                with self.subTest(field=field),self.assertRaises(ValueError):
                    pull_artifact(manifest,PROFILE,changed['remote'],target,'192.0.2.50:26101')
            manifest.write_text(json.dumps(doc))
            target.write_bytes(b'preserve')
            with self.assertRaises(ValueError):
                pull_artifact(manifest,PROFILE,remote,target,'192.0.2.50:26101')
