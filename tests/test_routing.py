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


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="settings-plan-", dir=ROOT / ".local")
        self.directory = Path(self.temp.name)
        self.database = self.directory / "snapshot.db"
        with closing(sqlite3.connect(self.database)) as conn:
            conn.executescript("""
              CREATE TABLE key_filter(appid TEXT, key TEXT, target_selector TEXT, args TEXT, other TEXT);
              INSERT INTO key_filter VALUES
                ('com.samsung.tv.quicklaunch','XF86More','package:com.samsung.tv.quicklaunch','original-more','keep'),
                ('org.tizen.menu','XF86SysMenu','package:org.tizen.menu','original-menu','keep'),
                ('unrelated.app','Other','package:unrelated.app','unrelated','keep');
            """)
            conn.commit()
        self.profile_path, self.inventory_path, self.output = self.qualified_inputs()

    def tearDown(self):
        self.temp.cleanup()

    def qualified_inputs(self, *, rows=None, current_rows=None, routes=None, complete=True,
                         model="MODEL_A", firmware="T-TEST-0000-1.0",
                         inventory_model=None, inventory_firmware=None):
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        schema = "CREATE TABLE key_filter(appid TEXT, key TEXT, target_selector TEXT, args TEXT, other TEXT)"
        observed = rows or [
            {"app_id": "com.samsung.tv.quicklaunch", "key": "XF86More",
             "target_selector": "package:com.samsung.tv.quicklaunch", "args": "original-more"},
            {"app_id": "org.tizen.menu", "key": "XF86SysMenu",
             "target_selector": "package:org.tizen.menu", "args": "original-menu"},
        ]
        current = current_rows if current_rows is not None else observed
        route_list = routes or [
            {"match": {"appid": "com.samsung.tv.quicklaunch", "key": "XF86More"},
             "after": {"target_selector": "package:com.example.settings", "args": "settings:quick"}},
        ]
        caps = ("settings_bridge", "rollback_ready")
        profile = {
            "profile_version": 1,
            "model": model,
            "firmware": firmware,
            "launcher": {"app_id": "com.example.settings", "dll": "QuietHome.dll"},
            "settings_bridge": {"registry": "/opt/dbspace/.app-context.db", "table": "key_filter",
                                "routes": route_list},
            "capabilities": {name: True for name in caps},
            "evidence": {name: {"status": "PROVEN", "scope": "device", "model": model,
                                "firmware": firmware, "source": "synthetic fixture",
                                "observed_at": now} for name in caps},
        }
        profile_path = self.directory / "profile.json"
        profile_path.write_text(json.dumps(profile))
        columns = [
            {"cid": i, "name": name, "type": "TEXT", "not_null": 0,
             "default": None, "primary_key": 0}
            for i, name in enumerate(("appid", "key", "target_selector", "args", "other"))
        ]
        inventory = {
            "schema": "quiet-tizen.inventory.v1", "collected_at": now,
            "device": {
                "model": {"status": "PROVEN", "value": inventory_model or model,
                          "source": "synthetic", "observed_at": now},
                "firmware": {"status": "PROVEN", "value": inventory_firmware or firmware,
                             "source": "synthetic", "observed_at": now},
            },
            "databases": {"key_filter": {
                "status": "PROVEN", "source": "synthetic", "observed_at": now,
                "value": {"table": "key_filter", "schema_sql": schema, "columns": columns,
                          "rows": current, "row_count": len(current), "complete": complete,
                          "selected_keys": ["XF86More", "XF86SysMenu"]},
            }},
        }
        inventory_path = self.directory / "inventory.json"
        inventory_path.write_text(json.dumps(inventory))
        return profile_path, inventory_path, self.directory / "plan"

    def run_planner(self, *, profile=None, inventory=None, snapshot=None, output=None):
        command = [sys.executable, str(ROOT / "tools/plan_settings_bridge.py")]
        if profile is not None:
            command += ["--profile", str(profile)]
        if inventory is not None:
            command += ["--inventory", str(inventory)]
        if snapshot is not None:
            command += ["--snapshot", str(snapshot)]
        if output is not None:
            command += ["--output", str(output)]
        return subprocess.run(command, text=True, capture_output=True, check=False)

    def test_settings_planner_requires_explicit_inputs_and_exact_evidence(self):
        missing = self.run_planner(profile=self.profile_path, inventory=self.inventory_path,
                                   output=self.output)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("--snapshot", missing.stderr)
        self.assertFalse(self.output.exists())

        outside = ROOT / "README.md"
        result = self.run_planner(profile=self.profile_path, inventory=self.inventory_path,
                                  snapshot=outside, output=self.output)
        self.assertNotEqual(result.returncode, 0)
        self.assertRegex(result.stderr.lower(), "snapshot|.local")
        self.assertFalse(self.output.exists())

    def test_settings_planner_rejects_missing_duplicate_incomplete_and_drifted_rows(self):
        missing = [{"app_id": "org.tizen.menu", "key": "XF86SysMenu",
                    "target_selector": "package:org.tizen.menu", "args": "original-menu"}]
        profile, inventory, output = self.qualified_inputs(rows=missing)
        result = self.run_planner(profile=profile, inventory=inventory,
                                  snapshot=self.database, output=output)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exactly one", result.stderr.lower())

        profile, inventory, output = self.qualified_inputs(complete=False)
        result = self.run_planner(profile=profile, inventory=inventory,
                                  snapshot=self.database, output=output)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("complete", result.stderr.lower())

        duplicate = [
            {"app_id": "com.samsung.tv.quicklaunch", "key": "XF86More",
             "target_selector": "package:com.samsung.tv.quicklaunch", "args": "original-more"},
            {"app_id": "com.samsung.tv.quicklaunch", "key": "XF86More",
             "target_selector": "package:com.samsung.tv.quicklaunch", "args": "original-more"},
        ]
        profile, inventory, output = self.qualified_inputs(rows=duplicate)
        result = self.run_planner(profile=profile, inventory=inventory,
                                  snapshot=self.database, output=output)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate", result.stderr.lower())

        changed = [
            {"app_id": "com.samsung.tv.quicklaunch", "key": "XF86More",
             "target_selector": "package:com.samsung.tv.quicklaunch", "args": "changed"},
            {"app_id": "org.tizen.menu", "key": "XF86SysMenu",
             "target_selector": "package:org.tizen.menu", "args": "original-menu"},
        ]
        profile, inventory, output = self.qualified_inputs(current_rows=changed)
        result = self.run_planner(profile=profile, inventory=inventory,
                                  snapshot=self.database, output=output)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("snapshot", result.stderr.lower())

    def test_settings_planner_rejects_identity_or_unsafe_route(self):
        profile, inventory, output = self.qualified_inputs(inventory_firmware="T-OTHER-0000-1.0")
        result = self.run_planner(profile=profile, inventory=inventory,
                                  snapshot=self.database, output=output)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("firmware", result.stderr.lower())

        bad_routes = [{"match": {"appid": "com.samsung.tv.quicklaunch", "key": "Home"},
                       "after": {"target_selector": "package:com.example.settings", "args": "settings:home"}}]
        profile, inventory, output = self.qualified_inputs(routes=bad_routes)
        result = self.run_planner(profile=profile, inventory=inventory,
                                  snapshot=self.database, output=output)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())

    def test_generated_settings_inverse_preserves_other_routes_and_metadata(self):
        profile, inventory, output = self.qualified_inputs()
        snapshot = self.directory / "immutable-app-context.db"
        with closing(sqlite3.connect(self.database)) as source, closing(sqlite3.connect(snapshot)) as target:
            source.backup(target)
        result = self.run_planner(profile=profile, inventory=inventory,
                                  snapshot=snapshot, output=output)
        self.assertEqual(result.returncode, 0, result.stderr)
        before = json.loads((output / "before.json").read_text())
        self.assertEqual(before["model"], "MODEL_A")
        self.assertEqual(before["firmware"], "T-TEST-0000-1.0")
        self.assertEqual(before["snapshot_sha256"], hashlib.sha256(snapshot.read_bytes()).hexdigest())
        self.assertEqual(before["inventory_sha256"], hashlib.sha256(inventory.read_bytes()).hexdigest())
        apply_sql = (output / "apply.sql").read_text()
        restore_sql = (output / "restore.sql").read_text()
        self.assertLess(apply_sql.index("INSERT INTO quiet_guard"), apply_sql.index("UPDATE key_filter"))
        self.assertNotIn("rowid", apply_sql.lower())
        with closing(sqlite3.connect(self.database)) as live:
            live.executescript(apply_sql)
            self.assertEqual(live.execute("SELECT target_selector,args FROM key_filter WHERE key='XF86More'").fetchone(),
                             ("package:com.example.settings", "settings:quick"))
            self.assertEqual(live.execute("SELECT target_selector,args FROM key_filter WHERE key='XF86SysMenu'").fetchone(),
                             ("package:org.tizen.menu", "original-menu"))
            self.assertEqual(live.execute("SELECT target_selector,args FROM key_filter WHERE key='Other'").fetchone(),
                             ("package:unrelated.app", "unrelated"))
            live.execute("UPDATE key_filter SET other='updated'")
            live.commit()
            live.executescript(restore_sql)
            self.assertEqual(live.execute("SELECT target_selector,args,other FROM key_filter WHERE key='XF86SysMenu'").fetchone(),
                             ("package:org.tizen.menu", "original-menu", "updated"))
            self.assertEqual(live.execute("SELECT args FROM key_filter WHERE key='XF86More'").fetchone(),
                             ("original-more",))

    def test_apply_and_restore_refuse_any_semantic_route_drift(self):
        profile, inventory, output = self.qualified_inputs()
        snapshot = self.directory / "immutable-app-context.db"
        with closing(sqlite3.connect(self.database)) as source, closing(sqlite3.connect(snapshot)) as target:
            source.backup(target)
        generated = self.run_planner(profile=profile, inventory=inventory,
                                     snapshot=snapshot, output=output)
        self.assertEqual(generated.returncode, 0, generated.stderr)
        with closing(sqlite3.connect(self.database)) as live:
            live.execute("UPDATE key_filter SET args='vendor-change' WHERE key='XF86More'")
            live.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                live.executescript((output / "apply.sql").read_text())
            live.rollback()
            self.assertEqual(live.execute("SELECT args FROM key_filter WHERE key='XF86SysMenu'").fetchone(),
                             ("original-menu",))
            live.execute("UPDATE key_filter SET args='original-more' WHERE key='XF86More'")
            live.commit()

        with closing(sqlite3.connect(self.database)) as live:
            live.execute("UPDATE key_filter SET args='vendor-change' WHERE key='XF86SysMenu'")
            live.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                live.executescript((output / "apply.sql").read_text())
            live.rollback()
            live.execute("UPDATE key_filter SET args='original-menu' WHERE key='XF86SysMenu'")
            live.commit()

        with closing(sqlite3.connect(self.database)) as live:
            live.executescript((output / "apply.sql").read_text())
            self.assertEqual(live.execute("SELECT args FROM key_filter WHERE key='XF86SysMenu'").fetchone(),
                             ("original-menu",))
            live.execute("UPDATE key_filter SET args='vendor-change' WHERE key='XF86SysMenu'")
            live.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                live.executescript((output / "restore.sql").read_text())
            live.rollback()
            live.execute("UPDATE key_filter SET args='original-menu' WHERE key='XF86SysMenu'")
            live.commit()
            live.execute("UPDATE key_filter SET args='vendor-change' WHERE key='XF86More'")
            live.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                live.executescript((output / "restore.sql").read_text())
            live.rollback()
            live.execute("UPDATE key_filter SET args='settings:quick' WHERE key='XF86More'")
            live.commit()
            live.executescript((output / "restore.sql").read_text())
            self.assertEqual(live.execute("SELECT target_selector,args FROM key_filter WHERE key='XF86SysMenu'").fetchone(),
                             ("package:org.tizen.menu", "original-menu"))
            self.assertEqual(live.execute("SELECT target_selector,args FROM key_filter WHERE key='XF86More'").fetchone(),
                             ("package:com.samsung.tv.quicklaunch", "original-more"))


if __name__ == "__main__":
    unittest.main()
