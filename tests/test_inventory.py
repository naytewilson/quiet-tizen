import re
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "vendor"))
from inventory import collect_inventory, parse_firmware


class FakeSdbClient:
    def __init__(self, payloads=None, *, closed=False, partial=(), nonzero=(), oversized=(),
                 device_model="UN65DU7200BXZA"):
        self.serial = "192.0.2.10:26101"
        self.device_model = device_model
        self.payloads = payloads or {}
        self.closed = closed
        self.partial = set(partial)
        self.nonzero = set(nonzero)
        self.oversized = set(oversized)
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
            if self.closed:
                return SimpleNamespace(returncode=0, stdout="closed\n", stderr="")
            return SimpleNamespace(
                returncode=0,
                stdout=f"List of devices attached\n{self.serial} device {self.device_model}\n",
                stderr="",
            )
        self.assert_explicit_shell(arguments, check, timeout)
        command = arguments[-1]
        match = re.search(r"__QTINV_([a-z_]+)_[0-9a-f]{16}_BEGIN__", command)
        if not match:
            raise AssertionError("inventory command is missing its per-run marker")
        name = match.group(1)
        begin = re.search(r"__QTINV_[a-z_]+_[0-9a-f]{16}_BEGIN__", command).group(0)
        end = begin.replace("_BEGIN__", "_END__")
        if self.closed:
            return SimpleNamespace(returncode=0, stdout="closed\n", stderr="")
        if name in self.oversized:
            return SimpleNamespace(returncode=0, stdout="x" * 300_000, stderr="")
        output = f"{begin}\n{self.payloads.get(name, '')}\n"
        if name not in self.partial:
            output += f"{end}\n"
        return SimpleNamespace(
            returncode=1 if name in self.nonzero else 0,
            stdout=output,
            stderr="",
        )

    @staticmethod
    def assert_explicit_shell(arguments, check, timeout):
        if arguments[:3] != ("-s", "192.0.2.10:26101", "shell"):
            raise AssertionError("device shell command must use an explicit SDB target")
        if check is not False or timeout is None or timeout > 5:
            raise AssertionError("device commands must be unchecked and time bounded")
        command = arguments[3]
        if any(value in command for value in ("appinstall", "push ", "pull ", "probe", "archive-root")):
            raise AssertionError("inventory command must not inject, transfer, or probe")


def successful_payloads():
    return {
        "prd_info": "ModelName=UN65DU7200BXZA\nVersion=T-KSU2EDAKUC-0090-2130.0;",
        "platform": (
            "KERNEL|Linux 5.15.0 armv7l\nOS_ID|Tizen\nOS_VERSION_ID|9.0"
        ),
        "runtime": (
            "RUNTIME_PATH|/usr/share/dotnet.tizen/netcoreapp/6.0.9\n"
            "/usr/share/dotnet.tizen/netcoreapp/6.0.9/libcoreclr.so\n"
            "/usr/share/dotnet.tizen/netcoreapp/6.0.9/System.Private.CoreLib.dll"
        ),
        "framework": (
            "ASSEMBLY|Tizen.NUI.dll|present\nASSEMBLY|Tizen.Applications.Common.dll|present\n"
            "ASSEMBLY|Tizen.System.dll|absent\nASSEMBLY|Tizen.dll|present"
        ),
        "capabilities": (
            "TOOL|sqlite3|/usr/bin/sqlite3\nTOOL|vconftool|/usr/bin/vconftool\n"
            "TOOL|dotnet|/usr/bin/dotnet\nTOOL|getcap|/usr/sbin/getcap\n"
            "TOOL|base64|/usr/bin/base64\nTOOL|sha256sum|/usr/bin/sha256sum\n"
            "TOOL|mktemp|/usr/bin/mktemp\nTOOL|gzip|/usr/bin/gzip\n"
            "TOOL|find|/usr/bin/find\nTOOL|readlink|/usr/bin/readlink\n"
            "TOOL|awk|/usr/bin/awk\nTOOL|sed|/usr/bin/sed\n"
            "FILECAP|/usr/bin/dotnet|cap_sys_admin=ep"
        ),
        "package_db": (
            "TABLE_SCHEMA|CREATE TABLE package_app_info(app_id TEXT,app_disable TEXT,app_onboot TEXT,app_autorestart TEXT)\n"
            "COLUMN|0|app_id|TEXT|1||1\nCOLUMN|1|app_disable|TEXT|0||0\n"
            "COLUMN|2|app_onboot|TEXT|0||0\nCOLUMN|3|app_autorestart|TEXT|0||0\n"
            "APP_ROW|com.samsung.tv.csfs|false|false|false\nAPP_TOTAL|1"
        ),
        "app_context_db": (
            "TABLE_SCHEMA|CREATE TABLE key_filter(appid TEXT,key TEXT,target_selector TEXT,args TEXT)\n"
            "COLUMN|0|appid|TEXT|0||0\nCOLUMN|1|key|TEXT|0||0\n"
            "COLUMN|2|target_selector|TEXT|0||0\nCOLUMN|3|args|TEXT|0||0\n"
            "KEY_ROW|org.tizen.menu|XF86SysMenu|package:com.samsung.tv.menu|menuitem:settings"
        ),
        "home_selector": "db/appfw/home_appid: com.samsung.tv.csfs",
        "startup_selector": "db/appfw/startup_appid: com.samsung.tv.csfs",
    }


class InventoryTests(unittest.TestCase):
    def test_collects_bounded_read_only_facts_with_field_evidence(self):
        client = FakeSdbClient(successful_payloads())

        inventory = collect_inventory(client)

        self.assertTrue(client.connected)
        self.assertTrue(client.device_checked)
        self.assertEqual(inventory["schema"], "quiet-tizen.inventory.v1")
        self.assertEqual(inventory["device"]["model"]["status"], "PROVEN")
        self.assertEqual(inventory["device"]["model"]["value"], "UN65DU7200BXZA")
        self.assertEqual(inventory["device"]["firmware"]["status"], "PROVEN")
        self.assertEqual(
            inventory["device"]["firmware"]["value"],
            "T-KSU2EDAKUC-0090-2130.0",
        )
        self.assertEqual(
            inventory["platform"]["kernel"]["value"], "Linux 5.15.0 armv7l"
        )
        self.assertEqual(
            inventory["selectors"]["home_appid"]["value"], "com.samsung.tv.csfs"
        )
        self.assertEqual(
            inventory["databases"]["package_app_info"]["value"]["rows"][0]["app_id"],
            "com.samsung.tv.csfs",
        )
        self.assertEqual(
            inventory["platform"]["runtime_files"]["value"]["libcoreclr.so"][0],
            "/usr/share/dotnet.tizen/netcoreapp/6.0.9/libcoreclr.so",
        )
        self.assertEqual(
            inventory["platform"]["runtime_files"]["value"]["System.Private.CoreLib.dll"][0],
            "/usr/share/dotnet.tizen/netcoreapp/6.0.9/System.Private.CoreLib.dll",
        )
        self.assertFalse(inventory["write_qualification"]["eligible"])
        self.assertTrue(
            all(command[0] == ("devices",) or command[0][0] == "-s"
                for command in client.commands)
        )
        self.assertTrue(
            all(command[1] is False and command[2] <= 5 for command in client.commands)
        )
        self.assertTrue(
            all("PROVEN" in repr(field) for field in inventory["device"].values())
        )

    def test_closed_exit_zero_without_markers_never_becomes_evidence(self):
        inventory = collect_inventory(FakeSdbClient(closed=True))

        self.assertEqual(inventory["device"]["model"]["status"], "UNKNOWN")
        self.assertEqual(inventory["device"]["firmware"]["status"], "UNKNOWN")
        self.assertEqual(inventory["platform"]["kernel"]["status"], "UNKNOWN")
        self.assertTrue(inventory["errors"])
        self.assertTrue(
            all(entry["status"] != "PROVEN" for entry in inventory["device"].values())
        )

    def test_partial_output_nonzero_and_oversized_output_fail_closed(self):
        partial = collect_inventory(FakeSdbClient(successful_payloads(), partial={"package_db"}))
        self.assertEqual(partial["databases"]["package_app_info"]["status"], "UNKNOWN")

        nonzero = collect_inventory(FakeSdbClient(successful_payloads(), nonzero={"platform"}))
        self.assertEqual(nonzero["platform"]["kernel"]["status"], "UNKNOWN")

        oversized = collect_inventory(FakeSdbClient(successful_payloads(), oversized={"app_context_db"}))
        self.assertEqual(oversized["databases"]["key_filter"]["status"], "UNKNOWN")

    def test_firmware_parser_requires_one_unambiguous_firmware_value(self):
        self.assertEqual(
            parse_firmware(
                "[Version]\nModelName=UN65DU7200BXZA\nVersion=T-KSU2EDAKUC-0090-2130.0\n"
            ),
            "T-KSU2EDAKUC-0090-2130.0",
        )
        self.assertIsNone(parse_firmware("ModelName=UN65DU7200BXZA\nVersion=unknown\n"))
        self.assertIsNone(
            parse_firmware(
                "Version=T-KSU2EDAKUC-0090-2130.0\nFirmwareVersion=T-KSU2EDAKUC-0090-2131.0\n"
            )
        )
        self.assertEqual(
            parse_firmware("Version=T-KSU2EDAKUC-0090-2130.0;\n"),
            "T-KSU2EDAKUC-0090-2130.0",
        )
        self.assertIsNone(parse_firmware("Version=T-KSU2EDAKUC-0090-2130.0;;\n"))

    def test_model_conflict_and_bad_explicit_serial_fail_closed(self):
        conflict = collect_inventory(
            FakeSdbClient(successful_payloads(), device_model="UN55OTHERMODEL")
        )
        self.assertEqual(conflict["device"]["model"]["status"], "UNKNOWN")
        self.assertEqual(conflict["device"]["model"]["error"], "IDENTITY_CONFLICT")

        invalid = FakeSdbClient(successful_payloads())
        invalid.serial = "192.0.2.10:26101; rm -rf /"
        inventory = collect_inventory(invalid)
        self.assertEqual(inventory["target"]["status"], "UNKNOWN")
        self.assertFalse(invalid.connected)

    def test_remote_wrapper_requires_success_marker_and_has_fixed_output_ceiling(self):
        from inventory import MAX_OUTPUT_BYTES, _wrapped
        import subprocess

        command, begin, end = _wrapped("platform", "printf 'ok\\n'", "0123456789abcdef")
        completed = subprocess.run(["/bin/sh", "-c", command], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, f"{begin}\nok\n{end}\n")
        self.assertIn(f"-v limit={MAX_OUTPUT_BYTES}", command)

        failing, failed_begin, failed_end = _wrapped(
            "platform", "false\nprintf 'must-not-count\\n'", "fedcba9876543210"
        )
        failed = subprocess.run(["/bin/sh", "-c", failing], capture_output=True, text=True)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn(failed_begin, failed.stdout)
        self.assertNotIn("must-not-count", failed.stdout)
        self.assertNotIn(failed_end, failed.stdout)

        oversized, oversized_begin, oversized_end = _wrapped(
            "platform", "printf '%0140000d\\n' 0", "0011223344556677"
        )
        too_large = subprocess.run(
            ["/bin/sh", "-c", oversized], capture_output=True, text=True
        )
        self.assertNotEqual(too_large.returncode, 0)
        self.assertIn(oversized_begin, too_large.stdout)
        self.assertNotIn(oversized_end, too_large.stdout)

    def test_database_row_cap_is_explicitly_incomplete_and_unknown(self):
        payload = successful_payloads()
        package_lines = payload["package_db"].splitlines()
        payload["package_db"] = "\n".join(
            package_lines[:5]
            + [f"APP_ROW|com.example.app{index:03d}|false|false|false"
               for index in range(256)]
            + ["APP_TOTAL|257"]
        )
        inventory = collect_inventory(FakeSdbClient(payload))
        package = inventory["databases"]["package_app_info"]
        self.assertEqual(package["status"], "UNKNOWN")
        self.assertEqual(package["error"], "ROW_LIMIT")
        self.assertEqual(package["value"]["row_count"], 257)
        self.assertFalse(package["value"]["complete"])
        self.assertEqual(len(package["value"]["rows"]), 256)


if __name__ == "__main__":
    unittest.main()
