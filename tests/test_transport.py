import base64
import hashlib
import os
from pathlib import Path
import subprocess
import shutil
import signal
import time
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import tv


class ScriptTransportTests(unittest.TestCase):
    def execute(self, script, args=(), path=None, shell=('sh',)):
        with tempfile.TemporaryDirectory() as scratch:
            env = {**os.environ, 'TMPDIR': scratch}
            if path:
                env['PATH'] = path + os.pathsep + env['PATH']
            command = tv.script_command(script, args, token='0123456789abcdef')
            result = subprocess.run([*shell, '-c', command], capture_output=True, env=env, timeout=5)
            self.assertEqual(list(Path(scratch).iterdir()), [], 'session script leaked')
            return result

    def test_real_decoded_execution_quotes_unicode_spaces_and_empty_args(self):
        result = self.execute(b'printf "%s|" "$@"\n', ['two words', "it's", '雪', '', '$(false)'])
        self.assertEqual(result.returncode, 0, result.stderr)
        output = tv.verify_script_output(result.stdout.decode(), '0123456789abcdef', 0)
        self.assertEqual(output, "two words|it's|雪||$(false)|")

    def test_expected_file_hash_is_an_independent_effect(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / 'result with spaces'
            result = self.execute(b'printf "%s" "$2" > "$1"\n', [str(target), 'owner content 雪'])
            self.assertEqual(result.returncode, 0)
            self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(),
                             hashlib.sha256('owner content 雪'.encode()).hexdigest())

    @unittest.skipUnless(shutil.which('busybox'), 'BusyBox is not installed on this test host')
    def test_busybox_shell_and_decoder_execute_real_script(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/'base64').symlink_to(shutil.which('busybox'))
            result=self.execute(b'printf "%s" "$1"\n',['BusyBox 雪'],path=d,shell=(shutil.which('busybox'),'sh'))
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(tv.verify_script_output(result.stdout.decode(),'0123456789abcdef',0),'BusyBox 雪')

    def test_nonzero_and_early_exit_preserved(self):
        result = self.execute(b'printf before; exit 27\n')
        self.assertEqual(result.returncode, 27)
        with self.assertRaises(ValueError):
            tv.verify_script_output(result.stdout.decode(), '0123456789abcdef', 27)

    def test_decoder_failure_cannot_be_hidden_by_successful_shell(self):
        with tempfile.TemporaryDirectory() as d:
            decoder = Path(d) / 'base64'
            decoder.write_text('#!/bin/sh\nexit 31\n')
            decoder.chmod(0o700)
            result = self.execute(b'exit 0\n', path=d)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(b'QTSCRIPT:0123456789abcdef:BEGIN', result.stdout)

    def test_zero_exit_decoder_noop_is_rejected_by_script_hash(self):
        with tempfile.TemporaryDirectory() as d:
            decoder = Path(d) / 'base64'
            decoder.write_text('#!/bin/sh\nexit 0\n')
            decoder.chmod(0o700)
            result = self.execute(b'printf effect\n', path=d)
            self.assertNotEqual(result.returncode, 0)

    def test_missing_wrong_duplicate_or_truncated_sentinel_is_rejected(self):
        for output in ('', 'no-op\n', 'QTSCRIPT:wrong:END:0\n',
                       'QTSCRIPT:0123456789abcdef:BEGIN\n',
                       'QTSCRIPT:0123456789abcdef:BEGIN\nx\nQTSCRIPT:0123456789abcdef:END:0\ntrailing'):
            with self.subTest(output=output), self.assertRaises(ValueError):
                tv.verify_script_output(output, '0123456789abcdef', 0)

    def test_nul_oversized_script_and_invalid_token_refused(self):
        for script in (b'bad\0data', b'x' * 45001):
            with self.assertRaises(ValueError):
                tv.script_command(script, [], token='0123456789abcdef')
        with self.assertRaises(ValueError):
            tv.script_command(b'true', [], token='injection;')

    def test_transfer_envelope_and_path_limits_fail_closed(self):
        with self.assertRaises(ValueError):
            tv.decode_pull('x' * 1013401)
        for path in ('relative', '/bad\rpath', '/bad\npath', '/bad\0path'):
            with self.subTest(path=path),self.assertRaises(ValueError):
                tv.pull_command(path)

    def test_original_private_snapshot_cannot_be_overwritten(self):
        (ROOT/'.local').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/'.local') as d:
            path=Path(d)/'original.json'
            tv.save_private(path,b'original')
            with self.assertRaises(FileExistsError):
                tv.save_private(path,b'replaced')
            self.assertEqual(path.read_bytes(),b'original')

    def test_exit_zero_noop_root_response_is_refused(self):
        result={'authenticated':True,'uid':0,'euid':0,'system_files_unchanged':True,'staging_cleaned':True,
                'commands':[{'exit_code':0,'timed_out':False,'stdout':'','stderr':''}]}
        args=SimpleNamespace(host='192.0.2.50',callback_host=None)
        with patch('tv.probe',new=AsyncMock(return_value=result)),patch('tv.save_private'), \
                self.assertRaisesRegex(ValueError,'sentinel'):
            tv.run_root(args,'SYNTHETIC',b'printf effect')

    def test_sigkill_timeout_cannot_strand_a_decoded_script_file(self):
        with tempfile.TemporaryDirectory() as d:
            command=tv.script_command(b'sleep 20\n',[],token='0123456789abcdef')
            process=subprocess.Popen(['sh','-c',command],stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                     start_new_session=True,env={**os.environ,'TMPDIR':d})
            try:
                self.assertEqual(process.stdout.readline(),b'QTSCRIPT:0123456789abcdef:BEGIN\n')
                os.killpg(process.pid,signal.SIGKILL)
                process.communicate(timeout=5)
                self.assertEqual(list(Path(d).iterdir()),[])
            finally:
                if process.poll() is None:
                    os.killpg(process.pid,signal.SIGKILL)
                process.communicate(timeout=5)

    def test_pull_timeout_scratch_is_removed_by_verified_session_cleanup(self):
        from samsung_tv_root import archive_root
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);package='archive-root-0123456789abcdef'
            session=root/package;session.mkdir()
            source=root/'source';source.write_bytes(b'private fixture')
            tools=root/'tools';tools.mkdir()
            gzip_tool=tools/'gzip'
            gzip_tool.write_text('#!/bin/sh\nprintf ready > "$GZIP_MARKER"\nexec sleep 20\n')
            gzip_tool.chmod(0o700)
            marker=root/'gzip-started'
            env={**os.environ,'PATH':str(tools)+os.pathsep+os.environ['PATH'],
                 'QUIET_TIZEN_SESSION_DIR':str(session)+'/', 'GZIP_MARKER':str(marker)}
            process=subprocess.Popen(['sh','-c',tv.pull_command(str(source))],env=env,
                                     stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
            try:
                for _ in range(100):
                    if marker.exists():break
                    time.sleep(.01)
                self.assertTrue(marker.exists(),'bounded compressor did not start')
                self.assertEqual(len(list(session.iterdir())),1,'scratch must be inside this session')
                os.killpg(process.pid,signal.SIGKILL);process.communicate(timeout=5)
                sibling=root/'other-session';sibling.mkdir();(sibling/'keep').write_bytes(b'keep')
                def capture(client,command):
                    # Map only the documented SDK staging prefix into this owned fixture.
                    mapped=command.replace(str(archive_root.STAGING_ROOT),str(root))
                    result=subprocess.run(['sh','-c',mapped],capture_output=True,text=True,check=True)
                    return result.stdout.strip()
                with patch.object(archive_root,'_capture',side_effect=capture):
                    self.assertTrue(archive_root._cleanup(object(),package))
                self.assertFalse(session.exists())
                self.assertEqual((sibling/'keep').read_bytes(),b'keep')
            finally:
                if process.poll() is None:os.killpg(process.pid,signal.SIGKILL)
                process.communicate(timeout=5)


if __name__ == '__main__':
    unittest.main()
