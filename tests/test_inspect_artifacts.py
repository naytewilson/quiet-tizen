import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import inspect_artifacts as inspect_module
from inspect_artifacts import (FRAMEWORKS, HOST_SCHEMA, SNAPSHOT_FILES,
                               collect, from_snapshot_report)


MODEL = "TESTMODEL"
FIRMWARE = "T-TEST1234-5678-9012.3"
TARGET = "192.0.2.44:26101"
OBSERVED_AT = "2026-10-23T12:00:00Z"
SHA_A = "a" * 64
SHA_B = "b" * 64


def test_profile():
    return {
        "profile_version": 1,
        "model": MODEL,
        "firmware": FIRMWARE,
        "launcher": {
            "app_id": "com.test.quiet-home",
            "dll": "QuietHome.dll",
            "root": "/opt/usr/apps/com.test.quiet-home",
            "uid": 4321,
            "owner": "201:0",
            "smack_label": "User::Pkg::com.test.quiet-home::RO",
        },
        "launcher_config": {
            "inputs": [{"id": 7, "label": "Profile Default"}],
            "default_source": 7,
        },
        "capabilities": {},
        "evidence": {},
    }


def framework_payload(*, digest=SHA_A, size=128):
    rows = []
    for name in FRAMEWORKS:
        path = f"/usr/share/dotnet.tizen/netcoreapp/6.0.9/{name}"
        rows.append(f"FRAMEWORK|{name}|PROVEN|1|{path}|{size}|{digest}|")
    return "\n".join(rows)


def host_payload(*, digest=SHA_B, size=32):
    root = "/opt/usr/apps/com.test.quiet-home"
    return "\n".join((
        "ROOT|PRESENT|1|0|201:0|755|User::Pkg::com.test.quiet-home::RO",
        f"ENTRY|PRESENT|1|0|PROVEN|201:0|644|{size}|{digest}|User::Pkg::com.test.quiet-home::RO",
        "PATH|resource|ABSENT|NOT_SYMLINK",
        "PATH|native_image|ABSENT|NOT_SYMLINK",
    ))


def run_rtk_shell(script):
    rtk = shutil.which("rtk")
    if not rtk:
        raise RuntimeError("RTK is required by this repository's shell contract")
    result = subprocess.run([rtk, "proxy", "sh", "-c", script], text=True,
                            capture_output=True, check=False, timeout=20)
    if result.returncode != 0:
        raise AssertionError(f"fixture shell failed ({result.returncode}): {result.stderr}")
    return result.stdout


def inventory_payloads(*, model=MODEL, firmware=FIRMWARE):
    return {
        "prd_info": f"ModelName={model}\nVersion={firmware};",
        "platform": "KERNEL|Linux test\nOS_ID|Tizen\nOS_VERSION_ID|9.0",
        "runtime": "RUNTIME_PATH|/usr/share/dotnet.tizen/netcoreapp/6.0.9",
        "framework": "\n".join(
            f"ASSEMBLY|{name}|present|/usr/share/dotnet.tizen/netcoreapp/6.0.9/{name}"
            for name in FRAMEWORKS
        ),
        "capabilities": "TOOL|getcap|/usr/bin/getcap",
        "package_db": (
            "TABLE_SCHEMA|CREATE TABLE package_app_info(app_id TEXT,app_disable TEXT,app_onboot TEXT,app_autorestart TEXT)\n"
            "COLUMN|0|app_id|TEXT|1||1\nCOLUMN|1|app_disable|TEXT|0||0\n"
            "COLUMN|2|app_onboot|TEXT|0||0\nCOLUMN|3|app_autorestart|TEXT|0||0\n"
            "APP_ROW|com.test.quiet-home|false|false|false\nAPP_TOTAL|1"
        ),
        "app_context_db": (
            "TABLE_SCHEMA|CREATE TABLE key_filter(appid TEXT,key TEXT,target_selector TEXT,args TEXT)\n"
            "COLUMN|0|appid|TEXT|0||0\nCOLUMN|1|key|TEXT|0||0\n"
            "COLUMN|2|target_selector|TEXT|0||0\nCOLUMN|3|args|TEXT|0||0\nKEY_TOTAL|0"
        ),
        "home_selector": "db/appfw/home_appid: org.tizen.menu\ndb/appfw/startup_appid: org.tizen.menu",
    }


class FakeSdbClient:
    """Fixture client for the existing inventory._run_read transport contract."""

    def __init__(self, payloads=None, *, device_model=MODEL, timeout=(), nonzero=(),
                 partial=(), oversized=(), no_op=()):
        self.serial = TARGET
        self.device_model = device_model
        self.payloads = dict(payloads or {})
        self.timeout = set(timeout)
        self.nonzero = set(nonzero)
        self.partial = set(partial)
        self.oversized = set(oversized)
        self.no_op = set(no_op)
        self.commands = []
        self.connected = False
        self.device_checked = False

    def connect(self):
        self.connected = True

    def require_device(self):
        self.device_checked = True

    def run(self, arguments, *, check, timeout=None):
        self.commands.append((arguments, check, timeout))
        if arguments == ("devices",):
            if check is not False or timeout is None or timeout > 5:
                raise AssertionError("devices listing must be bounded and unchecked")
            return SimpleNamespace(
                returncode=0,
                stdout=f"List of devices attached\n{self.serial} device {self.device_model}\n",
                stderr="",
            )
        if arguments[:3] != ("-s", self.serial, "shell") or check is not False or timeout is None or timeout > 5:
            raise AssertionError("device command must use the explicit target and a short timeout")
        command = arguments[-1]
        match = re.search(r"__QTINV_([a-z_]+)_[0-9a-f]{16}_BEGIN__", command)
        if not match:
            raise AssertionError("read command is missing its inventory sentinel")
        name = match.group(1)
        if any(token in command for token in ("appinstall", "capture(", "inject(", "push ", "pull ", "archive-root")):
            raise AssertionError("collector attempted a non-read-only command")
        begin = re.search(r"__QTINV_[a-z_]+_[0-9a-f]{16}_BEGIN__", command).group(0)
        end = begin.replace("_BEGIN__", "_END__")
        if name in self.timeout:
            raise subprocess.TimeoutExpired("sdb shell", timeout)
        if name in self.oversized:
            return SimpleNamespace(returncode=0, stdout="x" * 300_000, stderr="")
        payload = "" if name in self.no_op else self.payloads.get(name, "")
        stdout = f"{begin}\n{payload}\n"
        if name not in self.partial:
            stdout += f"{end}\n"
        return SimpleNamespace(returncode=1 if name in self.nonzero else 0,
                               stdout=stdout, stderr="")


class InspectArtifactsTests(unittest.TestCase):
    def client(self, **kwargs):
        payloads = inventory_payloads()
        payloads.update({
            "framework_artifacts": framework_payload(),
            "host_paths": host_payload(),
        })
        payloads.update(kwargs.pop("payloads", {}))
        return FakeSdbClient(payloads, **kwargs)

    def test_collect_binds_proven_artifacts_to_explicit_target_and_keeps_unknowns(self):
        client = self.client()

        host = collect(client, test_profile(), now=OBSERVED_AT)

        self.assertEqual(host["schema"], HOST_SCHEMA)
        self.assertEqual(host["identity_status"], "PROVEN")
        self.assertEqual(host["target"]["serial"], TARGET)
        self.assertEqual(host["model"], MODEL)
        self.assertEqual(host["firmware"], FIRMWARE)
        self.assertIsNone(host["launcher_uid"], "profile UID must not be copied as target evidence")
        self.assertIsNone(host["source_inputs"], "profile input IDs must not be copied as target evidence")
        self.assertIsNone(host["entry"]["original_backup"], "collector must not invent a local backup filename")
        self.assertEqual(host["field_evidence"]["launcher_uid"]["status"], "UNKNOWN")
        self.assertEqual(host["field_evidence"]["source_inputs"]["status"], "UNKNOWN")
        self.assertEqual(host["field_evidence"]["package_root.owner"]["status"], "PROVEN")
        self.assertEqual(host["package_root"]["owner"], "201:0")
        self.assertEqual(host["entry"]["sha256"], SHA_B)
        self.assertEqual(host["entry"]["size"], 32)
        self.assertEqual(host["resource_path"]["exists"], False)
        self.assertEqual(host["native_image"]["exists"], False)

        framework_manifests = [m for m in host["artifact_manifests"] if m["kind"] == "framework"]
        original_entry = [m for m in host["artifact_manifests"] if m["kind"] == "original-entry"]
        self.assertEqual(len(framework_manifests), 4)
        self.assertEqual(len(original_entry), 1)
        for manifest in host["artifact_manifests"]:
            self.assertEqual(manifest["target"], TARGET)
            self.assertEqual(manifest["model"], MODEL)
            self.assertEqual(manifest["firmware"], FIRMWARE)
            self.assertEqual(manifest["size"], 32 if manifest["kind"] == "original-entry" else 128)
            self.assertRegex(manifest["sha256"], r"^[a-f0-9]{64}$")
        self.assertTrue(client.connected)
        self.assertTrue(client.device_checked)
        self.assertTrue(all(arguments[:3] == ("-s", TARGET, "shell")
                            for arguments, _, _ in client.commands if arguments[:1] == ("-s",)))

    def test_profile_defaults_do_not_become_source_input_evidence(self):
        host = collect(self.client(), test_profile(), now=OBSERVED_AT)
        self.assertEqual(test_profile()["launcher_config"]["inputs"], [{"id": 7, "label": "Profile Default"}])
        self.assertIsNone(host["source_inputs"])
        self.assertEqual(host["field_evidence"]["source_inputs"]["status"], "UNKNOWN")

    def test_launcher_uid_requires_one_unambiguous_exact_entry_process(self):
        rtk = shutil.which("rtk")
        self.assertIsNotNone(rtk)
        syntax = subprocess.run([rtk, "proxy", "sh", "-n", "-c",
                                 inspect_module._uid_process_command(
                                     "/opt/usr/apps/com.test.quiet-home/bin/QuietHome.dll")],
                                text=True, capture_output=True, check=False, timeout=20)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)

        proven = {"launcher_uid": None, "launcher_uid_process": None, "field_evidence": {}}
        inspect_module._parse_uid_rows(
            "UIDPROC|4242|4321|4321|4321|4321\nUIDSCAN|64|0|0\n", proven, OBSERVED_AT)
        self.assertEqual(proven["launcher_uid"], 4321)
        self.assertEqual(proven["launcher_uid_process"]["pid"], 4242)
        self.assertEqual(proven["field_evidence"]["launcher_uid"]["status"], "PROVEN")

        for raw in (
            "UIDSCAN|64|0|0\n",  # not running
            "UIDPROC|4242|4321|4321|4321|4321\nUIDPROC|4243|4321|4321|4321|4321\nUIDSCAN|64|0|0\n",
            "UIDPROC|4242|4321|4321|4321|4321\nUIDSCAN|64|1|0\n",  # permission denied elsewhere
            "UIDPROC|4242|4321|5000|4321|4321\nUIDSCAN|64|0|0\n",  # UID fields disagree
        ):
            unknown = {"launcher_uid": None, "launcher_uid_process": None, "field_evidence": {}}
            inspect_module._parse_uid_rows(raw, unknown, OBSERVED_AT)
            self.assertIsNone(unknown["launcher_uid"])
            self.assertEqual(unknown["field_evidence"]["launcher_uid"]["status"], "UNKNOWN")

    def test_bound_raw_probe_sources_remain_opaque_without_ids_or_aliases(self):
        stdout = "home_result=0 home=org.test.home\nsources=HDMI1,HDMI2\ncurrent_source_result=0 current_source=1\n"
        probe = {
            "schema": "quiet-tizen.probe-output.v1",
            "target": TARGET,
            "model": MODEL,
            "firmware": FIRMWARE,
            "observed_at": OBSERVED_AT,
            "stdout": stdout,
            "sha256": hashlib.sha256(stdout.encode()).hexdigest(),
        }

        host = collect(self.client(), test_profile(), now=OBSERVED_AT, probe_evidence=probe)

        self.assertIsNone(host["source_inputs"])
        self.assertEqual(host["field_evidence"]["source_inputs"]["status"], "UNKNOWN")
        self.assertEqual(host["probe_observation"]["status"], "PROVEN")
        self.assertEqual(host["probe_observation"]["sources_raw"], "HDMI1,HDMI2")
        self.assertEqual(host["probe_observation"]["stdout_sha256"], hashlib.sha256(stdout.encode()).hexdigest())

    def test_profile_identity_conflict_keeps_host_unknown_and_emits_no_manifests(self):
        wrong_profile = test_profile()
        wrong_profile["firmware"] = "T-OTHER1234-5678-9012.3"

        host = collect(self.client(), wrong_profile, now=OBSERVED_AT)

        self.assertEqual(host["identity_status"], "CONFLICT")
        self.assertIn("PROFILE_IDENTITY_CONFLICT", host["errors"])
        self.assertEqual(host["artifact_manifests"], [])
        self.assertIsNone(host["entry"]["sha256"])
        self.assertEqual(host["field_evidence"]["entry.sha256"]["status"], "UNKNOWN")

    def test_inventory_model_conflict_blocks_all_manifest_generation(self):
        payloads = inventory_payloads(model="OTHERMODEL")
        client = self.client(payloads=payloads)

        host = collect(client, test_profile(), now=OBSERVED_AT)

        self.assertEqual(host["identity_status"], "UNKNOWN")
        self.assertEqual(host["artifact_manifests"], [])
        self.assertIsNone(host["model"])

    def test_zero_exit_no_op_does_not_prove_artifacts(self):
        client = self.client(no_op=("framework_artifacts", "host_paths"))

        host = collect(client, test_profile(), now=OBSERVED_AT)

        self.assertEqual(host["artifact_manifests"], [])
        self.assertTrue(all(item["status"] == "UNKNOWN" for item in host["framework_artifacts"].values()))
        self.assertEqual(host["field_evidence"]["entry.sha256"]["status"], "UNKNOWN")

    def test_timeout_and_oversized_output_stay_unknown_and_bounded(self):
        timed = collect(self.client(timeout=("framework_artifacts", "host_paths", "launcher_uid")),
                        test_profile(), now=OBSERVED_AT)
        self.assertEqual(timed["artifact_manifests"], [])
        self.assertTrue(all(item["status"] == "UNKNOWN" for item in timed["framework_artifacts"].values()))
        self.assertIn("FRAMEWORK_READ_COMMAND_ERROR", timed["errors"])

        oversized = collect(self.client(oversized=("framework_artifacts", "host_paths", "launcher_uid")),
                            test_profile(), now=OBSERVED_AT)
        self.assertEqual(oversized["artifact_manifests"], [])
        self.assertTrue(all(item["status"] == "UNKNOWN" for item in oversized["framework_artifacts"].values()))
        self.assertIn("FRAMEWORK_READ_OUTPUT_LIMIT", oversized["errors"])

    def test_duplicate_or_malformed_framework_rows_do_not_create_manifests(self):
        payload = framework_payload() + "\n" + framework_payload().splitlines()[0]
        host = collect(self.client(payloads={"framework_artifacts": payload}, no_op=("host_paths",)),
                       test_profile(), now=OBSERVED_AT)
        framework_manifests = [m for m in host["artifact_manifests"] if m["kind"] == "framework"]
        self.assertEqual(len(framework_manifests), 3)
        self.assertNotIn("Tizen.NUI.dll", {m["remote"].rsplit("/", 1)[-1] for m in framework_manifests})

        bad_path_rows = []
        for name in FRAMEWORKS:
            bad_path_rows.append(f"FRAMEWORK|{name}|PROVEN|1|/etc/{name}|128|{SHA_A}|")
        host = collect(self.client(payloads={"framework_artifacts": "\n".join(bad_path_rows)},
                                   no_op=("host_paths",)),
                       test_profile(), now=OBSERVED_AT)
        self.assertEqual(host["artifact_manifests"], [])

    def test_generated_read_commands_execute_against_owned_local_fixtures(self):
        with tempfile.TemporaryDirectory(prefix=".inspect-framework-", dir=ROOT) as folder:
            framework_root = Path(folder) / "usr" / "share" / "dotnet.tizen"
            runtime_dir = framework_root / "netcoreapp" / "6.0.9"
            runtime_dir.mkdir(parents=True)
            bytes_by_name = {}
            for index, name in enumerate(FRAMEWORKS):
                data = f"fixture-{index}-{name}".encode()
                (runtime_dir / name).write_bytes(data)
                bytes_by_name[name] = data
            with patch.object(inspect_module, "FRAMEWORK_ROOT", str(framework_root)):
                output = run_rtk_shell(inspect_module._framework_command())
                artifacts, manifests = inspect_module._parse_framework_rows(
                    output, test_profile(), TARGET, OBSERVED_AT)
            self.assertEqual(set(artifacts), set(FRAMEWORKS))
            self.assertEqual(len(manifests), 4)
            for name in FRAMEWORKS:
                self.assertEqual(artifacts[name]["status"], "PROVEN")
                self.assertEqual(artifacts[name]["size"], len(bytes_by_name[name]))
                self.assertEqual(artifacts[name]["sha256"], hashlib.sha256(bytes_by_name[name]).hexdigest())

            root_alias = Path(folder) / "framework-root-alias"
            root_alias.symlink_to(Path(folder), target_is_directory=True)
            aliased_root = root_alias / "usr" / "share" / "dotnet.tizen"
            with patch.object(inspect_module, "FRAMEWORK_ROOT", str(aliased_root)):
                aliased_output = run_rtk_shell(inspect_module._framework_command())
                aliased_artifacts, aliased_manifests = inspect_module._parse_framework_rows(
                    aliased_output, test_profile(), TARGET, OBSERVED_AT)
            self.assertEqual(aliased_manifests, [])
            self.assertTrue(all(item["status"] == "UNKNOWN" for item in aliased_artifacts.values()))

        with tempfile.TemporaryDirectory(prefix=".inspect-host-", dir=ROOT) as folder:
            package_root = Path(folder) / "apps" / "com.test.quiet-home"
            bin_dir = package_root / "bin"
            resource_parent = package_root / "res"
            bin_dir.mkdir(parents=True)
            resource_parent.mkdir()
            entry = bin_dir / "QuietHome.dll"
            data = b"local shell fixture assembly"
            entry.write_bytes(data)
            entry.chmod(0o644)
            host = {
                "package_root": {"path": str(package_root), "is_directory": None, "is_symlink": None,
                                 "owner": None, "mode": None, "smack_label": None},
                "entry": {"path": str(entry), "is_regular": None, "is_symlink": None, "owner": None,
                          "mode": None, "size": None, "sha256": None, "smack_label": None,
                          "original_backup": None, "hash_size_status": "UNKNOWN"},
                "resource_path": {"path": str(resource_parent / "quiet-home"), "exists": None, "is_symlink": None},
                "native_image": {"path": str(bin_dir / ".native_image"), "exists": None, "is_symlink": None},
                "field_evidence": {},
            }
            output = run_rtk_shell(inspect_module._host_paths_command(
                str(package_root), str(entry), str(resource_parent / "quiet-home"),
                str(bin_dir / ".native_image")))
            inspect_module._parse_host_rows(output, host, OBSERVED_AT)
            self.assertEqual(host["package_root"]["is_directory"], True)
            self.assertEqual(host["package_root"]["owner"], f"{os.getuid()}:{os.getgid()}")
            self.assertEqual(host["entry"]["size"], len(data))
            self.assertEqual(host["entry"]["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(host["entry"]["mode"], "644")
            self.assertEqual(host["resource_path"]["exists"], False)
            self.assertEqual(host["native_image"]["exists"], False)
            self.assertEqual(host["field_evidence"]["entry.smack_label"]["status"], "UNKNOWN")

            alias = Path(folder) / "path-alias"
            alias.symlink_to(Path(folder), target_is_directory=True)
            aliased_root = alias / "apps" / "com.test.quiet-home"
            aliased_entry = aliased_root / "bin" / "QuietHome.dll"
            aliased_host = {
                "package_root": {"path": str(aliased_root), "is_directory": None, "is_symlink": None,
                                 "owner": None, "mode": None, "smack_label": None},
                "entry": {"path": str(aliased_entry), "is_regular": None, "is_symlink": None,
                          "owner": None, "mode": None, "size": None, "sha256": None,
                          "smack_label": None, "original_backup": None, "hash_size_status": "UNKNOWN"},
                "resource_path": {"path": str(aliased_root / "res" / "quiet-home"), "exists": None,
                                  "is_symlink": None},
                "native_image": {"path": str(aliased_root / "bin" / ".native_image"), "exists": None,
                                 "is_symlink": None},
                "field_evidence": {},
            }
            aliased_output = run_rtk_shell(inspect_module._host_paths_command(
                str(aliased_root), str(aliased_entry), str(aliased_root / "res" / "quiet-home"),
                str(aliased_root / "bin" / ".native_image")))
            inspect_module._parse_host_rows(aliased_output, aliased_host, OBSERVED_AT)
            self.assertIsNone(aliased_host["package_root"]["owner"])
            self.assertEqual(aliased_host["field_evidence"]["package_root.owner"]["status"], "UNKNOWN")
            self.assertIsNone(aliased_host["entry"]["sha256"])
            self.assertIsNone(aliased_host["entry"]["is_regular"])
            self.assertEqual(aliased_host["field_evidence"]["entry.sha256"]["status"], "UNKNOWN")

    def test_snapshot_report_emits_only_exact_target_bound_backup_manifests(self):
        phase = "phase_01"
        backup = f"/opt/usr/share/quiet-tizen-backups/{phase}"
        lines = []
        for index, filename in enumerate(SNAPSHOT_FILES):
            digest = (str(index + 1) * 64)
            lines.append(f"ARTIFACT|{backup}/{filename}|{100 + index}|{digest}")
        lines.extend((
            f"Verified, immutable snapshot retained at: {backup}",
            "This helper records state only. Use a separate reviewed phase plan for every later write.",
        ))
        envelope = {
            "schema": "quiet-tizen.snapshot-report.v1",
            "model": MODEL,
            "firmware": FIRMWARE,
            "target": TARGET,
            "phase": phase,
            "observed_at": OBSERVED_AT,
            "stdout": "\n".join(lines) + "\n",
        }

        result = from_snapshot_report(envelope, test_profile(), now=OBSERVED_AT)

        self.assertEqual(result["schema"], "quiet-tizen.snapshot-artifacts.v1")
        self.assertEqual(result["status"], "PROVEN")
        self.assertEqual(result["target"], TARGET)
        self.assertEqual(result["backup"]["path"], backup)
        self.assertEqual(set(result["backup"]["files"]), set(SNAPSHOT_FILES))
        self.assertEqual(len(result["artifact_manifests"]), 4)
        for manifest in result["artifact_manifests"]:
            self.assertEqual(manifest["kind"], "backup")
            self.assertEqual(manifest["target"], TARGET)
            self.assertEqual(manifest["remote"].rsplit("/", 1)[-1], manifest["destination"].rsplit("/", 1)[-1])

    def test_snapshot_report_rejects_identity_path_phase_size_and_freshness_drift(self):
        phase = "phase_01"
        backup = f"/opt/usr/share/quiet-tizen-backups/{phase}"
        artifact_lines = [f"ARTIFACT|{backup}/{name}|128|{SHA_A}" for name in SNAPSHOT_FILES]
        base = {
            "schema": "quiet-tizen.snapshot-report.v1",
            "model": MODEL,
            "firmware": FIRMWARE,
            "target": TARGET,
            "phase": phase,
            "observed_at": OBSERVED_AT,
            "stdout": "\n".join([*artifact_lines, f"Verified, immutable snapshot retained at: {backup}"]),
        }
        bad_identity = dict(base, firmware="T-OTHER1234-5678-9012.3")
        with self.assertRaisesRegex(ValueError, "identity"):
            from_snapshot_report(bad_identity, test_profile(), now=OBSERVED_AT)
        bad_path = dict(base, stdout=base["stdout"].replace("/quiet-tizen-backups/phase_01/", "/etc/"))
        with self.assertRaisesRegex(ValueError, "path"):
            from_snapshot_report(bad_path, test_profile(), now=OBSERVED_AT)
        bad_phase = dict(base, phase="other_phase")
        with self.assertRaisesRegex(ValueError, "retained backup path"):
            from_snapshot_report(bad_phase, test_profile(), now=OBSERVED_AT)
        bad_size = dict(base, stdout=base["stdout"].replace("|128|", f"|{33 * 1024 * 1024}|", 1))
        with self.assertRaisesRegex(ValueError, "size"):
            from_snapshot_report(bad_size, test_profile(), now=OBSERVED_AT)
        stale = dict(base, observed_at="2026-10-20T12:00:00Z")
        with self.assertRaisesRegex(ValueError, "stale"):
            from_snapshot_report(stale, test_profile(), now=OBSERVED_AT)


if __name__ == "__main__":
    unittest.main()
