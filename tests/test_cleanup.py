import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from plan_cleanup import read_rows, make_sql


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "synthetic.db"
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript("""
            CREATE TABLE package_app_info (
              app_id TEXT, app_disable TEXT, app_onboot TEXT, app_autorestart TEXT,
              unrelated TEXT
            );
            INSERT INTO package_app_info VALUES ('example.optional','false','true','false','keep');
            INSERT INTO package_app_info VALUES ('example.core','false','true','true','keep');
        """)
        self.rows = read_rows(self.path, ["example.optional"])

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()
        if hasattr(self, "bundle"):
            self.bundle.cleanup()

    def test_round_trip_preserves_unrelated_changes(self):
        self.conn.executescript(make_sql(self.rows))
        self.assertEqual(self.conn.execute("SELECT app_disable,app_onboot,app_autorestart "
                         "FROM package_app_info WHERE app_id='example.optional'").fetchone(),
                         ("true", "false", "false"))
        self.conn.execute("UPDATE package_app_info SET unrelated='new app metadata'")
        self.conn.execute("UPDATE package_app_info SET app_onboot='false' WHERE app_id='example.core'")
        self.conn.commit()
        self.conn.executescript(make_sql(self.rows, restore=True))
        self.assertEqual(self.conn.execute("SELECT app_disable,app_onboot,app_autorestart,unrelated "
                         "FROM package_app_info WHERE app_id='example.optional'").fetchone(),
                         ("false", "true", "false", "new app metadata"))
        self.assertEqual(self.conn.execute("SELECT app_onboot FROM package_app_info "
                         "WHERE app_id='example.core'").fetchone(), ("false",))

    def test_apply_refuses_target_field_drift(self):
        self.conn.execute("UPDATE package_app_info SET app_autorestart='true' WHERE app_id='example.optional'")
        self.conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.executescript(make_sql(self.rows))
        self.conn.rollback()
        self.assertEqual(self.conn.execute("SELECT app_disable FROM package_app_info "
                         "WHERE app_id='example.optional'").fetchone(), ("false",))

    def test_restore_refuses_target_field_drift(self):
        self.conn.executescript(make_sql(self.rows))
        self.conn.execute("UPDATE package_app_info SET app_onboot='true' WHERE app_id='example.optional'")
        self.conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.executescript(make_sql(self.rows, restore=True))
        self.conn.rollback()

    def test_all_guards_run_before_any_update(self):
        rows = read_rows(self.path, ["example.optional", "example.core"])
        self.conn.execute("DELETE FROM package_app_info WHERE app_id='example.core'")
        self.conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.executescript(make_sql(rows))
        self.conn.rollback()
        self.assertEqual(self.conn.execute("SELECT app_disable FROM package_app_info").fetchone(), ("false",))

    def test_missing_and_duplicate_rows_fail(self):
        with self.assertRaises(ValueError):
            read_rows(self.path, ["missing.app"])
        self.conn.execute("INSERT INTO package_app_info SELECT * FROM package_app_info WHERE app_id='example.optional'")
        self.conn.commit()
        with self.assertRaises(ValueError):
            read_rows(self.path, ["example.optional"])

    def test_unknown_boolean_and_sql_injection_fail(self):
        self.conn.execute("UPDATE package_app_info SET app_disable='yes'")
        self.conn.commit()
        with self.assertRaises(ValueError):
            read_rows(self.path, ["example.optional"])
        with self.assertRaises(ValueError):
            read_rows(self.path, ["example'; DROP TABLE package_app_info;--"])

    def test_null_original_value_round_trip(self):
        self.conn.execute("UPDATE package_app_info SET app_onboot=NULL WHERE app_id='example.optional'")
        self.conn.commit()
        rows = read_rows(self.path, ["example.optional"])
        self.conn.executescript(make_sql(rows))
        self.conn.executescript(make_sql(rows, restore=True))
        self.assertIsNone(self.conn.execute("SELECT app_onboot FROM package_app_info "
                          "WHERE app_id='example.optional'").fetchone()[0])

    def qualified_inputs(self, *, cleanup=True, rollback=True, group_evidence=True,
                         group_ids=None, inventory_model="MODEL_A",
                         inventory_firmware="T-TEST-0000-1.0", row=None,
                         inventory_row=None):
        """Create a complete synthetic proof bundle; it is never device evidence."""
        if not hasattr(self, "bundle"):
            self.bundle = tempfile.TemporaryDirectory(prefix="cleanup-plan-", dir=ROOT / ".local")
        bundle = Path(self.bundle.name)
        evidence_path = bundle / "dependency-evidence.txt"
        evidence_path.write_text("Synthetic test fixture only; no TV was inspected.\n")
        source_hash = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
        evidence_source = str(evidence_path.relative_to(ROOT))
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        model, firmware = "MODEL_A", "T-TEST-0000-1.0"
        capability_evidence = {
            name: {
                "status": "PROVEN",
                "scope": "device",
                "model": model,
                "firmware": firmware,
                "source": evidence_source,
                "source_sha256": source_hash,
                "observed_at": now,
            }
            for name in ("cleanup", "rollback_ready")
        }
        capabilities = {"cleanup": cleanup, "rollback_ready": rollback}
        group = {"description": "Synthetic optional target", "app_ids": group_ids or ["example.optional"]}
        if group_evidence:
            group["dependency_evidence"] = {
                "status": "PROVEN",
                "model": model,
                "firmware": firmware,
                "source": evidence_source,
                "source_sha256": source_hash,
                "observed_at": now,
                "preserves": ["native-av", "network-remote", "native-settings", "updates", "streaming-data"],
            }
        profile = {
            "profile_version": 1,
            "model": model,
            "firmware": firmware,
            "registry": "/opt/dbspace/.pkgmgr_parser.db",
            "groups": {"optional": group},
            "capabilities": capabilities,
            "evidence": capability_evidence,
        }
        profile_path = bundle / "profile.json"
        profile_path.write_text(json.dumps(profile))
        values = row or ("false", "true", "false")
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("UPDATE package_app_info SET app_disable=?, app_onboot=?, app_autorestart=? "
                         "WHERE app_id='example.optional'", values)
        self.conn.commit()
        current_values = inventory_row or values
        inventory = {
            "schema": "quiet-tizen.inventory.v1",
            "collected_at": now,
            "device": {
                "model": {"status": "PROVEN", "value": inventory_model, "observed_at": now},
                "firmware": {"status": "PROVEN", "value": inventory_firmware, "observed_at": now},
            },
            "databases": {
                "package_app_info": {
                    "status": "PROVEN",
                    "value": {"complete": True, "rows": [{
                        "app_id": "example.optional",
                        "app_disable": current_values[0],
                        "app_onboot": current_values[1],
                        "app_autorestart": current_values[2],
                    }]},
                }
            },
        }
        inventory_path = bundle / "current-inventory.json"
        inventory_path.write_text(json.dumps(inventory))
        self.plan_snapshot = bundle / "package-app-info.db"
        with closing(sqlite3.connect(self.path)) as source, closing(sqlite3.connect(self.plan_snapshot)) as target:
            source.backup(target)
        output = bundle / "plan"
        return bundle, profile_path, inventory_path, output

    def run_planner(self, profile=None, inventory=None, output=None, *, snapshot=None,
                    groups=("optional",),
                    opt_ins=("optional",)):
        command = [sys.executable, str(ROOT / "tools/plan_cleanup.py"),
                   "--snapshot", str(snapshot or getattr(self, "plan_snapshot", self.path))]
        if profile is not None:
            command += ["--profile", str(profile)]
        if inventory is not None:
            command += ["--inventory", str(inventory)]
        command += ["--groups", *groups]
        for group in opt_ins:
            command += ["--owner-opt-in", group]
        if output is not None:
            command += ["--output", str(output)]
        return subprocess.run(command, text=True, capture_output=True, check=False)

    def test_cleanup_planner_has_no_implicit_profile_or_owner_consent(self):
        _, _, inventory, output = self.qualified_inputs()
        missing_profile = self.run_planner(inventory=inventory, output=output)
        self.assertNotEqual(missing_profile.returncode, 0)
        self.assertIn("--profile", missing_profile.stderr)
        self.assertFalse(output.exists())

        _, profile, inventory, output = self.qualified_inputs()
        missing_opt_in = self.run_planner(profile, inventory, output, opt_ins=())
        self.assertNotEqual(missing_opt_in.returncode, 0)
        self.assertIn("opt-in", missing_opt_in.stderr.lower())
        self.assertFalse(output.exists())

    def test_cleanup_requires_exact_capability_and_group_dependency_evidence(self):
        _, profile, inventory, output = self.qualified_inputs(cleanup=False)
        unqualified = self.run_planner(profile, inventory, output)
        self.assertNotEqual(unqualified.returncode, 0)
        self.assertIn("cleanup", unqualified.stderr.lower())
        self.assertFalse(output.exists())

        _, profile, inventory, output = self.qualified_inputs(group_evidence=False)
        without_group_evidence = self.run_planner(profile, inventory, output)
        self.assertNotEqual(without_group_evidence.returncode, 0)
        self.assertIn("dependency", without_group_evidence.stderr.lower())
        self.assertFalse(output.exists())

    def test_cleanup_requires_current_exact_identity_and_snapshot_row_match(self):
        _, profile, inventory, output = self.qualified_inputs(inventory_firmware="T-OTHER-0000-1.0")
        wrong_firmware = self.run_planner(profile, inventory, output)
        self.assertNotEqual(wrong_firmware.returncode, 0)
        self.assertIn("firmware", wrong_firmware.stderr.lower())
        self.assertFalse(output.exists())

        _, profile, inventory, output = self.qualified_inputs(inventory_row=("false", "false", "false"))
        row_mismatch = self.run_planner(profile, inventory, output)
        self.assertNotEqual(row_mismatch.returncode, 0)
        self.assertIn("snapshot", row_mismatch.stderr.lower())
        self.assertFalse(output.exists())

    def test_cleanup_qualified_plan_binds_snapshot_inventory_and_group(self):
        _, profile, inventory, output = self.qualified_inputs()
        result = self.run_planner(profile, inventory, output)
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((output / "before.json").read_text())
        self.assertEqual(manifest["model"], "MODEL_A")
        self.assertEqual(manifest["firmware"], "T-TEST-0000-1.0")
        self.assertEqual(manifest["groups"], ["optional"])
        self.assertEqual(manifest["snapshot_sha256"], hashlib.sha256(self.plan_snapshot.read_bytes()).hexdigest())
        self.assertEqual(manifest["inventory_sha256"], hashlib.sha256(inventory.read_bytes()).hexdigest())
        self.assertIn("example.optional", (output / "apply.sql").read_text())
        self.assertIn("example.optional", (output / "restore.sql").read_text())
        self.assertIn("/etc/prd_info.ini", (output / "apply.sh").read_text())
        self.assertEqual((output / "apply.sh").stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
