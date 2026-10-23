import hashlib
import json
import os
import re
import sqlite3
import shutil
import shlex
import subprocess
import sys
import tempfile
import tarfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build import profile_key
from plan_cleanup import identity_check as cleanup_identity_check
from plan_launcher import identity_check as launcher_identity_check
import plan_settings_bridge
from plan_settings_bridge import _identity_check as settings_identity_check


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class LauncherPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="launcher-plan-", dir=ROOT / ".local")
        self.directory = Path(self.temp.name)
        self.model = "TVM" + re.sub(r"[^a-z0-9]", "", self.directory.name.split("-")[-1]).upper()
        self.firmware = "T-ABC-1234-1300.1"
        self.app_id = "com.example.quiet-home"
        self.dll = "QuietHome.dll"
        self.owner = "201:0"
        self.mode = "644"
        self.smack = "User::Pkg::com.example.quiet-home::RO"
        self.root = f"/opt/usr/apps/{self.app_id}"
        self.backup_name = "plan_test_phase"
        self.stage_name = "quiet-home-plan.tar.gz"
        self.backup_snapshot = self.directory / "package-app-info.db"
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self.now = now
        evidence_source = "synthetic fixture; never device evidence"
        capabilities = {"launcher_build": True, "package_host": True,
                        "launcher_install": True, "rollback_ready": True}
        cap_evidence = {}
        for name in capabilities:
            cap_evidence[name] = {
                "status": "PROVEN", "scope": "offline" if name == "launcher_build" else "device",
                "model": self.model, "firmware": self.firmware,
                "source": evidence_source, "observed_at": now,
            }
        self.profile = {
            "profile_version": 1, "model": self.model, "firmware": self.firmware,
            "registry": "/opt/dbspace/.pkgmgr_parser.db",
            "launcher": {"app_id": self.app_id, "dll": self.dll, "root": self.root,
                         "uid": 5001, "owner": self.owner, "smack_label": self.smack},
            "launcher_config": {
                "inputs": [{"id": 13, "label": "HDMI 1"}], "default_source": 13,
                "viewer_app": "org.tizen.tv-viewer", "native_settings_app": "org.tizen.menu",
                "settings": ["advanced"], "apps": [], "setting_options": {},
            },
            "groups": {}, "capabilities": capabilities, "evidence": cap_evidence,
        }
        self.profile_path = self.directory / "profile.json"
        self.profile_path.write_text(json.dumps(self.profile))
        self.package_parent = ROOT / ".local" / "package"
        self.package_parent.mkdir(parents=True, exist_ok=True)
        self.package = self.package_parent / profile_key(self.profile)
        if self.package.exists():
            raise RuntimeError("Synthetic profile build directory unexpectedly already exists.")
        self.package.mkdir()
        resources = self.package / "quiet-home"
        resources.mkdir(parents=True)
        self.package_dll = self.package / self.dll
        self.package_dll.write_bytes(b"synthetic new launcher assembly\n")
        self.package_dll.chmod(0o644)
        self.resource = resources / "launcher.json"
        self.resource.write_text(json.dumps(self.profile["launcher_config"]))
        self.resource.chmod(0o644)
        (resources / "apps.json").write_text("[]\n")
        (resources / "apps.json").chmod(0o644)
        with closing(sqlite3.connect(self.backup_snapshot)) as conn:
            conn.executescript("""
                CREATE TABLE package_app_info (
                  app_id TEXT, app_disable TEXT, app_onboot TEXT, app_autorestart TEXT
                );
                INSERT INTO package_app_info VALUES
                  ('com.example.quiet-home','false','true','false'),
                  ('org.tizen.tv-viewer','false','false','false'),
                  ('org.tizen.menu','false','false','false');
            """)
            conn.commit()
        self.inventory_path = self.directory / "inventory.json"
        self.inventory = {
            "schema": "quiet-tizen.inventory.v1", "collected_at": now,
            "device": {
                "model": {"status": "PROVEN", "value": self.model, "observed_at": now},
                "firmware": {"status": "PROVEN", "value": self.firmware, "observed_at": now},
            },
            "databases": {"package_app_info": {
                "status": "PROVEN", "value": {
                    "complete": True, "row_count": 3,
                    "rows": [
                        {"app_id": self.app_id, "app_disable": "false",
                         "app_onboot": "true", "app_autorestart": "false"},
                        {"app_id": "org.tizen.tv-viewer", "app_disable": "false",
                         "app_onboot": "false", "app_autorestart": "false"},
                        {"app_id": "org.tizen.menu", "app_disable": "false",
                         "app_onboot": "false", "app_autorestart": "false"},
                    ],
                },
            }},
        }
        self.inventory_path.write_text(json.dumps(self.inventory))
        self.evidence_path = self.directory / "launcher-host-evidence.json"
        self.original_bytes = b"synthetic original system assembly\n"
        self.original_path = self.directory / "original-entry.dll"
        self.original_path.write_bytes(self.original_bytes)
        backup_files = {".pkgmgr_parser.db": digest(self.backup_snapshot)}
        for filename in (".app-context.db", ".webappservice.db", "preferences-before.txt"):
            path = self.directory / filename
            path.write_text("synthetic snapshot input " + filename + "\n")
            backup_files[filename] = digest(path)
        self.host = {
            "schema": "quiet-tizen.launcher-host.v1", "observed_at": now,
            "model": self.model, "firmware": self.firmware,
            "launcher_uid": 5001,
            "source_inputs": [{"id": 13, "label": "HDMI 1"}],
            "package_root": {"path": self.root, "is_directory": True, "is_symlink": False,
                             "owner": self.owner, "mode": "755"},
            "entry": {"path": f"{self.root}/bin/{self.dll}",
                      "sha256": hashlib.sha256(self.original_bytes).hexdigest(),
                      "original_backup": self.original_path.name,
                      "owner": self.owner, "mode": self.mode, "smack_label": self.smack},
            "resource_path": {"path": f"{self.root}/res/quiet-home", "exists": False},
            "native_image": {"path": f"{self.root}/bin/.native_image", "exists": False},
            "backup": {"path": f"/opt/usr/share/quiet-tizen-backups/{self.backup_name}",
                       "files": backup_files},
        }
        self.evidence_path.write_text(json.dumps(self.host))
        self.output = self.directory / "plan"

    def tearDown(self):
        if self.package.exists() and not self.package.is_symlink():
            shutil.rmtree(self.package)
        self.temp.cleanup()

    def run_planner(self, *, profile=None, inventory=None, snapshot=None, host=None,
                    backup_name=None, stage_name=None, output=None):
        command = [sys.executable, str(ROOT / "tools/plan_launcher.py")]
        values = (("--profile", profile), ("--inventory", inventory),
                  ("--snapshot", snapshot), ("--host-evidence", host),
                  ("--backup-name", backup_name), ("--stage-name", stage_name),
                  ("--output", output))
        for flag, value in values:
            if value is not None:
                command += [flag, str(value)]
        return subprocess.run(command, text=True, capture_output=True, check=False)

    def generate(self, **kwargs):
        return self.run_planner(
            profile=kwargs.get("profile", self.profile_path),
            inventory=kwargs.get("inventory", self.inventory_path),
            snapshot=kwargs.get("snapshot", self.backup_snapshot),
            host=kwargs.get("host", self.evidence_path),
            backup_name=kwargs.get("backup_name", self.backup_name),
            stage_name=kwargs.get("stage_name", self.stage_name),
            output=kwargs.get("output", self.output),
        )

    def save_host(self, **changes):
        host = json.loads(self.evidence_path.read_text())
        for section, values in changes.items():
            host[section].update(values)
        self.evidence_path.write_text(json.dumps(host))

    def test_launcher_plan_requires_all_exact_private_inputs_and_capabilities(self):
        missing = self.run_planner(profile=self.profile_path, output=self.output)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("--inventory", missing.stderr)
        self.assertFalse(self.output.exists())

        profile = json.loads(self.profile_path.read_text())
        profile["capabilities"]["launcher_install"] = False
        no_install = self.directory / "no-install-profile.json"
        no_install.write_text(json.dumps(profile))
        result = self.generate(profile=no_install)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("launcher_install", result.stderr)
        self.assertFalse(self.output.exists())

    def test_launcher_plan_rejects_identity_host_state_and_snapshot_drift(self):
        self.inventory["device"]["firmware"]["value"] = "T-OTHER-1234-1300.1"
        self.inventory_path.write_text(json.dumps(self.inventory))
        wrong_fw = self.generate()
        self.assertNotEqual(wrong_fw.returncode, 0)
        self.assertIn("firmware", wrong_fw.stderr.lower())
        self.inventory["device"]["firmware"]["value"] = self.firmware
        self.inventory_path.write_text(json.dumps(self.inventory))

        self.save_host(native_image={"path": f"{self.root}/bin/.native_image", "exists": True})
        cache_present = self.generate()
        self.assertNotEqual(cache_present.returncode, 0)
        self.assertIn("native", cache_present.stderr.lower())
        self.save_host(native_image={"path": f"{self.root}/bin/.native_image", "exists": False})

        self.save_host(resource_path={"path": f"{self.root}/res/quiet-home", "exists": True})
        resource_present = self.generate()
        self.assertNotEqual(resource_present.returncode, 0)
        self.assertIn("resource", resource_present.stderr.lower())
        self.save_host(resource_path={"path": f"{self.root}/res/quiet-home", "exists": False})

        self.save_host(entry={"sha256": "0" * 64})
        bad_hash = self.generate()
        self.assertNotEqual(bad_hash.returncode, 0)
        # The original intent was alternative matching; literal pipes accept no real hash error.
        self.assertRegex(bad_hash.stderr.lower(), r"snapshot|host|assembly|hash")
        self.assertFalse(self.output.exists())

    def test_launcher_plan_binds_paths_fw_hashes_and_exact_inverse(self):
        result = self.generate()
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((self.output / "before.json").read_text())
        self.assertEqual(manifest["model"], self.model)
        self.assertEqual(manifest["firmware"], self.firmware)
        self.assertEqual(manifest["entry"]["path"], f"{self.root}/bin/{self.dll}")
        self.assertEqual(manifest["entry"]["before_sha256"], hashlib.sha256(self.original_bytes).hexdigest())
        self.assertEqual(manifest["entry"]["after_sha256"], digest(self.package_dll))
        self.assertEqual(manifest["snapshot_sha256"], digest(self.backup_snapshot))
        self.assertEqual(manifest["inventory_sha256"], digest(self.inventory_path))
        self.assertEqual(manifest["host_evidence_sha256"], digest(self.evidence_path))
        self.assertEqual(manifest["package_app_row"], self.inventory["databases"]["package_app_info"]["value"]["rows"][0])
        self.assertTrue((self.output / self.stage_name).is_file())
        install = (self.output / "install.sh").read_text()
        restore = (self.output / "restore.sh").read_text()
        self.assertIn("/etc/prd_info.ini", install)
        self.assertIn(self.firmware, install)
        self.assertIn(self.root, install)
        self.assertIn("sha256sum", install)
        self.assertIn(self.smack, install)
        self.assertNotIn("QuickLaunch.dll", install)
        self.assertNotIn("app_launcher", install)
        self.assertNotIn("pkill", install)
        self.assertNotIn("home_appid", install)
        self.assertNotIn("startup_appid", install)
        self.assertNotIn("vconftool set", install)
        self.assertIn("launcher-entry-before.dll", restore)
        self.assertIn(hashlib.sha256(self.original_bytes).hexdigest(), restore)
        self.assertIn(digest(self.package_dll), restore)
        self.assertIn("org.tizen.tv-viewer", install)
        self.assertIn("org.tizen.menu", install)

        syntax = subprocess.run(["sh", "-n", str(self.output / "install.sh")], check=False)
        self.assertEqual(syntax.returncode, 0)
        syntax = subprocess.run(["sh", "-n", str(self.output / "restore.sh")], check=False)
        self.assertEqual(syntax.returncode, 0)

        with tarfile.open(self.output / self.stage_name, "r:gz") as package:
            members = package.getmembers()
        self.assertEqual({member.name for member in members},
                         {self.dll, "quiet-home/launcher.json", "quiet-home/apps.json"})
        self.assertTrue(all(member.isfile() and not member.issym() and not member.islnk()
                            for member in members))

    def test_launcher_plan_rejects_uninstalled_targets_uid_and_unbound_source_inputs(self):
        self.inventory["databases"]["package_app_info"]["value"]["rows"][1]["app_disable"] = "true"
        self.inventory_path.write_text(json.dumps(self.inventory))
        disabled = self.generate()
        self.assertNotEqual(disabled.returncode, 0)
        self.assertIn("not installed and enabled", disabled.stderr)
        self.inventory["databases"]["package_app_info"]["value"]["rows"][1]["app_disable"] = "false"
        self.inventory_path.write_text(json.dumps(self.inventory))

        self.inventory["databases"]["package_app_info"]["value"]["rows"][0]["app_disable"] = "true"
        self.inventory_path.write_text(json.dumps(self.inventory))
        disabled_host = self.generate()
        self.assertNotEqual(disabled_host.returncode, 0)
        self.assertIn("Launcher host package is not installed and enabled", disabled_host.stderr)
        self.inventory["databases"]["package_app_info"]["value"]["rows"][0]["app_disable"] = "false"
        self.inventory_path.write_text(json.dumps(self.inventory))

        self.host["launcher_uid"] = 5002
        self.evidence_path.write_text(json.dumps(self.host))
        wrong_uid = self.generate()
        self.assertNotEqual(wrong_uid.returncode, 0)
        self.assertIn("UID", wrong_uid.stderr)
        self.host["launcher_uid"] = 5001
        self.host["source_inputs"] = [{"id": 99, "label": "HDMI 1"}]
        self.evidence_path.write_text(json.dumps(self.host))
        wrong_source = self.generate()
        self.assertNotEqual(wrong_source.returncode, 0)
        self.assertIn("source-input", wrong_source.stderr)
        self.assertFalse(self.output.exists())

    def test_launcher_plan_rejects_symlinked_package_payload_and_preserves_old_output(self):
        outside = self.directory / "outside.txt"
        outside.write_text("outside fixture\n")
        link = self.package / "quiet-home" / "link-outside"
        link.symlink_to(outside)
        unsafe = self.generate()
        self.assertNotEqual(unsafe.returncode, 0)
        self.assertIn("symlink", unsafe.stderr.lower())
        self.assertFalse(self.output.exists())
        link.unlink()

        self.output.mkdir()
        marker = self.output / "keep.txt"
        marker.write_text("preserve\n")
        already_exists = self.generate()
        self.assertNotEqual(already_exists.returncode, 0)
        self.assertEqual(marker.read_text(), "preserve\n")


class GeneratedIdentityTests(unittest.TestCase):
    def test_all_generated_identity_checks_match_inventory_optional_semicolon_and_duplicate_rules(self):
        model = "TVMTEST"
        firmware = "T-ABC-1234-1300.1"
        gates = (launcher_identity_check, cleanup_identity_check, settings_identity_check)
        with tempfile.TemporaryDirectory(prefix="identity-check-", dir=ROOT / ".local") as temp:
            prd_info = Path(temp) / "prd_info.ini"
            for gate in gates:
                body = "set -eu\n" + gate(model, firmware).replace(
                    "/etc/prd_info.ini", shlex.quote(str(prd_info)))
                for contents, expected in (
                    (f"ModelName={model}\nVersion={firmware};\n", 0),
                    (f"ModelName={model}\nFirmwareVersion={firmware}\n", 0),
                    (f"ModelName={model}\nVersion={firmware}\nFirmwareVersion={firmware}\n", 1),
                    (f"ModelName={model}\nVersion=T-OTHER-1234-1300.1;\n", 1),
                    (f"ModelName={model}\nVersion={firmware};;\n", 1),
                    (f"ModelName={model}\nModelName={model}\nVersion={firmware};\n", 1),
                ):
                    prd_info.write_text(contents)
                    result = subprocess.run(["sh", "-c", body], check=False)
                    self.assertEqual(result.returncode, expected, contents)


class SnapshotRecipeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="snapshot-recipe-", dir=ROOT / ".local")
        self.root = Path(self.temp.name)
        self.model = "TVM_SNAPSHOT"
        self.firmware = "T-ABC-1234-1300.1"
        self.backup_root = self.root / "opt/usr/share/quiet-tizen-backups"
        self.dbspace = self.root / "opt/dbspace"
        self.etc = self.root / "etc"
        self.bin = self.root / "bin"
        self.backup_root.mkdir(parents=True)
        self.dbspace.mkdir(parents=True)
        self.etc.mkdir()
        self.bin.mkdir()
        self.write_identity(self.firmware)
        for name in (".pkgmgr_parser.db", ".app-context.db", ".webappservice.db"):
            (self.dbspace / name).write_text("synthetic source database " + name + "\n")
        self.write_executable(self.bin / "id", "#!/bin/sh\nprintf '0\\n'\n")
        sqlite = """#!/bin/sh
if [ "$1" = -readonly ]; then shift; fi
database=$1
request=${2:-}
case "$request" in
    'PRAGMA quick_check;')
        if [ -n "${FAIL_CHECK_PATH:-}" ] && [ "$database" = "$FAIL_CHECK_PATH" ]; then
            exit 17
        fi
        printf 'ok\\n'
        ;;
    .backup\\ *)
        destination=$(printf '%s\\n' "$request" | sed "s/^\\.backup '//; s/'$//")
        cp "$database" "$destination"
        ;;
    *) exit 18 ;;
esac
"""
        self.write_executable(self.bin / "sqlite3", sqlite)
        self.write_executable(self.bin / "vconftool", "#!/bin/sh\nprintf 'synthetic-value\\n'\n")
        source = (ROOT / "recipes/snapshot.sh").read_text()
        source = source.replace("/etc/prd_info.ini", str(self.etc / "prd_info.ini"))
        source = source.replace("/opt/usr/share/quiet-tizen-backups", str(self.backup_root))
        source = source.replace("/opt/dbspace", str(self.dbspace))
        self.script = self.root / "snapshot.sh"
        self.script.write_text(source)

    def tearDown(self):
        self.temp.cleanup()

    def write_identity(self, firmware):
        (self.etc / "prd_info.ini").write_text(
            f"ModelName = {self.model}\nFirmwareVersion = {firmware};\n")

    @staticmethod
    def write_executable(path, content):
        path.write_text(content)
        path.chmod(0o755)

    def run_recipe(self, phase, firmware=None, **environment):
        env = dict(os.environ)
        env["PATH"] = str(self.bin) + ":" + env.get("PATH", "")
        env.update(environment)
        return subprocess.run(
            ["sh", str(self.script), self.model, firmware or self.firmware, phase],
            text=True, capture_output=True, env=env, check=False,
        )

    def test_cross_firmware_refuses_before_creating_snapshot(self):
        result = self.run_recipe("cross_firmware", firmware="T-OTHER-1234-1300.1")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.backup_root / "cross_firmware").exists())

    def test_first_snapshot_creates_only_its_new_project_parent_and_phase(self):
        self.backup_root.rmdir()
        result=self.run_recipe('first_capture')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue((self.backup_root/'first_capture/SHA256SUMS').is_file())
        self.assertEqual(self.backup_root.stat().st_mode & 0o777,0o700)

    def test_symlinked_backup_parent_is_refused_before_snapshot_write(self):
        self.backup_root.rmdir()
        elsewhere=self.root/'unrelated';elsewhere.mkdir()
        self.backup_root.symlink_to(elsewhere,target_is_directory=True)
        result=self.run_recipe('refuse_link')
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(list(elsewhere.iterdir()),[])

    def test_partial_snapshot_failure_retains_every_created_backup(self):
        phase = "partial_failure"
        partial_copy = self.backup_root / phase / ".app-context.db"
        result = self.run_recipe(phase, FAIL_CHECK_PATH=str(partial_copy))
        self.assertNotEqual(result.returncode, 0)
        directory = self.backup_root / phase
        self.assertTrue((directory / ".pkgmgr_parser.db").is_file())
        self.assertTrue((directory / ".pkgmgr_parser.db.sha256").is_file())
        self.assertTrue((directory / ".app-context.db").is_file())
        self.assertFalse((directory / ".app-context.db.sha256").exists())

    def test_complete_snapshot_has_verified_hashes_and_never_overwrites_phase(self):
        phase = "complete_phase"
        result = self.run_recipe(phase)
        self.assertEqual(result.returncode, 0, result.stderr)
        directory = self.backup_root / phase
        self.assertTrue((directory / "SHA256SUMS").is_file())
        checked = subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=directory, check=False,
                                 text=True, capture_output=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        for filename in (".pkgmgr_parser.db", ".app-context.db", ".webappservice.db", "preferences-before.txt"):
            artifact = directory / filename
            expected = f"ARTIFACT|{artifact}|{artifact.stat().st_size}|{digest(artifact)}"
            self.assertIn(expected, result.stdout)
        marker = directory / "marker.txt"
        marker.write_text("preserve\n")
        duplicate = self.run_recipe(phase)
        self.assertNotEqual(duplicate.returncode, 0)
        self.assertEqual(marker.read_text(), "preserve\n")


class SettingsBridgeBoundaryTests(unittest.TestCase):
    def test_inventory_covers_sysmenu_but_sql_updates_only_more_and_guards_sysmenu(self):
        self.assertEqual(plan_settings_bridge.ALLOWED_KEYS, frozenset(("XF86More",)))
        self.assertEqual(plan_settings_bridge.INVENTORY_KEYS,
                         frozenset(("XF86More", "XF86SysMenu")))
        with tempfile.TemporaryDirectory(prefix="settings-routes-", dir=ROOT / ".local") as temp:
            snapshot = Path(temp) / "app-context.db"
            with closing(sqlite3.connect(snapshot)) as connection:
                connection.execute(
                    "CREATE TABLE key_filter (appid TEXT, key TEXT, target_selector TEXT, args TEXT)")
                connection.executemany(
                    "INSERT INTO key_filter VALUES (?,?,?,?)",
                    (
                        ("org.tizen.menu", "XF86More", "package:com.example.quiet-home", "settings"),
                        ("org.tizen.menu", "XF86SysMenu", "package:org.tizen.menu", "native-settings"),
                    ),
                )
                connection.commit()
                schema_sql = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='key_filter'").fetchone()[0]
                raw_columns = connection.execute("PRAGMA table_info(key_filter)").fetchall()
            columns = [
                {"cid": cid, "name": name, "type": col_type, "not_null": not_null,
                 "default": default, "primary_key": primary_key}
                for cid, name, col_type, not_null, default, primary_key in raw_columns
            ]
            inventory = {"databases": {"key_filter": {"status": "PROVEN", "value": {
                "table": "key_filter", "complete": True, "row_count": 2,
                "selected_keys": ["XF86More", "XF86SysMenu"], "columns": columns,
                "schema_sql": schema_sql,
                "rows": [
                    {"app_id": "org.tizen.menu", "key": "XF86More",
                     "target_selector": "package:com.example.quiet-home", "args": "settings"},
                    {"app_id": "org.tizen.menu", "key": "XF86SysMenu",
                     "target_selector": "package:org.tizen.menu", "args": "native-settings"},
                ],
            }}}}
            route = {"match": {"appid": "org.tizen.menu", "key": "XF86More"},
                     "after": {"target_selector": "package:com.example.quiet-home", "args": "quick-settings"}}
            observed, normalized, observed_schema = plan_settings_bridge._validate_inventory_rows(
                inventory, [route])
            self.assertEqual(set(observed), {("org.tizen.menu", "XF86More"),
                                             ("org.tizen.menu", "XF86SysMenu")})
            before = plan_settings_bridge._snapshot_rows(snapshot, normalized, observed_schema)
            self.assertEqual(before, observed)
            sql = plan_settings_bridge.make_sql([route], before)
            updates = [line for line in sql.splitlines() if line.startswith("UPDATE key_filter")]
            self.assertEqual(len(updates), 1)
            self.assertIn("key='XF86More'", updates[0])
            self.assertNotIn("UPDATE key_filter SET", "\n".join(
                line for line in sql.splitlines() if "XF86SysMenu" in line))
            self.assertTrue(any("XF86SysMenu" in line and "INSERT INTO quiet_guard" in line
                                for line in sql.splitlines()))
            self.assertIn("package:org.tizen.menu", sql)

    def test_customizing_sysmenu_route_is_rejected(self):
        profile = {
            "launcher": {"app_id": "com.example.quiet-home"},
            "settings_bridge": {
                "registry": plan_settings_bridge.REGISTRY,
                "table": plan_settings_bridge.TABLE,
                "routes": [{
                    "match": {"appid": "org.tizen.menu", "key": "XF86SysMenu"},
                    "after": {"target_selector": "package:com.example.quiet-home", "args": "quick-settings"},
                }],
            },
        }
        with self.assertRaisesRegex(ValueError, "Only explicitly observed Settings/More keys"):
            plan_settings_bridge._validate_routes(profile)


if __name__ == "__main__":
    unittest.main()
