import json
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"

if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
import build as build_module


class BuildProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.local = self.root / ".local"
        self.framework = self.root / "tv-framework"
        self.framework.mkdir()
        for name in ("Tizen.NUI", "Tizen.Applications.Common", "Tizen.System", "Tizen"):
            (self.framework / f"{name}.dll").write_bytes(b"synthetic reference; not a redistributed assembly")
        for project in (
            "launcher/src/Home.csproj",
            "launcher/src/QuickLaunch.csproj",
            "launcher/probe/Probe.csproj",
            "vendor/payload-source/SamsungTvArchiveRoot.csproj",
            "vendor/payload-source/SamsungTvRootAgent.csproj",
        ):
            path = self.root / project
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic project")
        assets = self.root / "launcher/assets"
        assets.mkdir(parents=True)
        (assets / "apps.json").write_text(json.dumps([
            {"Name": "Netflix", "Id": "org.tizen.netflix-app", "Asset": "netflix.svg",
             "Description": "Movies", "Width": 100, "Height": 100}
        ]))
        (assets / "netflix.svg").write_text("synthetic app art")
        self.profile_dir = self.root / "profiles"
        self.profile_dir.mkdir()
        self.profile_a = self.profile_dir / "profile-a.json"
        self.profile_b = self.profile_dir / "profile-b.json"
        self.write_profile(self.profile_a, "T-KSU2EFAKUC-0090-1300.9", "com.example.quiet-home-a",
                           "QuietHomeA.dll", 14)
        self.write_profile(self.profile_b, "T-KSU2EFAKUC-0091-1300.9", "com.example.quiet-home-b",
                           "QuietHomeB.dll", 25)
        self.dotnet_invocations = []
        self.active_profile = None
        self.original_root = build_module.ROOT
        build_module.ROOT = self.root

    def tearDown(self):
        build_module.ROOT = self.original_root
        self.temp.cleanup()

    def write_profile(self, path, firmware, app_id, dll, source_id):
        path.write_text(json.dumps({
            "profile_version": 1,
            "model": "UN75U8000FFXZA",
            "firmware": firmware,
            "launcher": {
                "app_id": app_id,
                "dll": dll,
                "root": f"/opt/usr/apps/{app_id}",
                "uid": 5001,
                "owner": "201:0",
                "smack_label": f"User::Pkg::{app_id}::RO",
            },
            "capabilities": {"launcher_build": True},
            "evidence": {"launcher_build": {
                "status": "PROVEN",
                "source": "synthetic offline build fixture",
                "model": "UN75U8000FFXZA",
                "firmware": firmware,
                "observed_at": "2026-10-23T00:00:00Z",
                "scope": "offline",
            }},
            "launcher_config": {
                "inputs": [{"id": source_id, "label": f"Input {source_id}"}],
                "default_source": source_id,
                "viewer_app": "org.tizen.tv-viewer",
                "native_settings_app": "org.tizen.menu",
                "settings": ["volume", "mute", "game-mode", "sleep-timer", "advanced"],
                "setting_options": {
                    "game-mode": ["0", "1"],
                    "sleep-timer": ["0", "1", "2", "3", "4"],
                },
                "apps": ["org.tizen.netflix-app"],
            },
        }))

    def fake_dotnet(self, command, check, env):
        output = Path(command[command.index("-o") + 1])
        project = Path(command[2])
        output.mkdir(parents=True, exist_ok=True)
        assembly_name = next((arg.split("=", 1)[1] for arg in command
                              if arg.startswith("-p:AssemblyName=")), None)
        if project.name == "Home.csproj":
            filename = f"{assembly_name}.dll"
        elif project.name == "QuickLaunch.csproj":
            filename = "QuickLaunch.dll"
        elif project.name == "Probe.csproj":
            filename = "Probe.dll"
        else:
            filename = f"{project.stem}.dll"
        marker = f"{self.active_profile.stem}:{project.name}".encode()
        (output / filename).write_bytes(marker)
        self.dotnet_invocations.append(command)

    def build_profile(self, profile):
        self.active_profile = profile
        with mock.patch.object(sys, "argv", [
            "build.py", "launcher", "--profile", str(profile), "--framework", str(self.framework)
        ]), mock.patch.object(build_module.subprocess, "run", side_effect=self.fake_dotnet):
            build_module.main()

    def package_for(self, model, firmware):
        return self.local / "package" / build_module.profile_key({"model": model, "firmware": firmware})

    def package_snapshot(self, package):
        return {str(path.relative_to(package)): path.read_bytes()
                for path in sorted(package.rglob("*")) if path.is_file()}

    def test_profile_is_required_for_launcher_builds(self):
        for target in ("launcher", "all"):
            with self.subTest(target=target), mock.patch.object(sys, "argv", [
                "build.py", target, "--framework", str(self.framework)
            ]), mock.patch.object(build_module.subprocess, "run") as run, \
                contextlib.redirect_stderr(io.StringIO()) as error:
                with self.assertRaises(SystemExit):
                    build_module.main()
                self.assertIn("--profile", error.getvalue())
                run.assert_not_called()

    def test_build_a_then_b_then_a_keeps_outputs_and_packages_isolated(self):
        self.build_profile(self.profile_a)
        package_a = self.package_for("UN75U8000FFXZA", "T-KSU2EFAKUC-0090-1300.9")
        first_a = self.package_snapshot(package_a)
        self.assertIn("QuietHomeA.dll", first_a)
        self.assertNotIn("QuietHomeB.dll", first_a)

        self.build_profile(self.profile_b)
        package_b = self.package_for("UN75U8000FFXZA", "T-KSU2EFAKUC-0091-1300.9")
        snapshot_b = self.package_snapshot(package_b)
        self.assertIn("QuietHomeB.dll", snapshot_b)
        self.assertNotIn("QuietHomeA.dll", snapshot_b)
        self.assertEqual(self.package_snapshot(package_a), first_a)

        stale = package_a / "obsolete-profile-assembly.dll"
        stale.write_bytes(b"stale")
        (package_a / "quiet-home/stale-resource.svg").write_bytes(b"stale")
        self.build_profile(self.profile_a)
        final_a = self.package_snapshot(package_a)
        self.assertIn("QuietHomeA.dll", final_a)
        self.assertNotIn("QuietHomeB.dll", final_a)
        self.assertNotIn("obsolete-profile-assembly.dll", final_a)
        self.assertNotIn("quiet-home/stale-resource.svg", final_a)
        self.assertEqual(final_a["QuietHomeA.dll"], b"profile-a:Home.csproj")
        self.assertEqual(final_a["QuickLaunch.dll"], b"profile-a:QuickLaunch.csproj")
        self.assertEqual(final_a["Probe.dll"], b"profile-a:Probe.csproj")
        native_config = json.loads(final_a["quiet-home/launcher.json"])
        self.assertEqual(native_config["inputs"], [{"id": 14, "label": "Input 14"}])
        self.assertEqual(native_config["viewer_app"], "org.tizen.tv-viewer")
        self.assertEqual(native_config["native_settings_app"], "org.tizen.menu")
        self.assertEqual(native_config["setting_options"], {
            "game-mode": ["0", "1"],
            "sleep-timer": ["0", "1", "2", "3", "4"],
        })
        self.assertNotIn("Tizen.NUI.dll", final_a)
        self.assertNotIn("Tizen.Applications.Common.dll", final_a)

        outputs = [Path(command[command.index("-o") + 1]) for command in self.dotnet_invocations]
        intermediates = [Path(arg.split("=", 1)[1]) for command in self.dotnet_invocations
                         for arg in command if arg.startswith("-p:BaseIntermediateOutputPath=")]
        self.assertTrue(all("un75u8000ffxza" in str(path) for path in outputs + intermediates))
        self.assertNotEqual(outputs[0], outputs[3])  # profile A and B have distinct build trees
        self.assertNotEqual(intermediates[0], intermediates[3])
        self.assertIn("1300.9", str(outputs[0]))
        self.assertIn("1300.9", str(intermediates[0]))
        self.assertIn("1300.9", str(outputs[3]))
        self.assertIn("1300.9", str(intermediates[3]))
        self.assertTrue(all("-p:Deterministic=true" in command for command in self.dotnet_invocations))
        for command in self.dotnet_invocations:
            if Path(command[2]).name in ("Home.csproj", "QuickLaunch.csproj"):
                self.assertTrue(any(arg == "-p:QuietHomeViewerApp=org.tizen.tv-viewer" for arg in command))
                self.assertTrue(any(arg == "-p:QuietHomeNativeSettingsApp=org.tizen.menu" for arg in command))
                if Path(command[2]).name == "Home.csproj":
                    assembly = next(arg for arg in command if arg.startswith("-p:AssemblyName="))
                    suffix = "a" if assembly == "-p:AssemblyName=QuietHomeA" else "b"
                    app_id = f"com.example.quiet-home-{suffix}"
                    self.assertTrue(any(arg == f"-p:QuietHomeAppId={app_id}" for arg in command))
                    self.assertTrue(any(arg == f"-p:QuietHomeRoot=/opt/usr/apps/{app_id}" for arg in command))
                    self.assertTrue(any(arg == f"-p:QuietHomeResourceRoot=/opt/usr/apps/{app_id}/res/quiet-home/" for arg in command))

    def test_invalid_launcher_paths_fail_before_compiler(self):
        for field, value in (("app_id", "../escape"), ("dll", "../other.dll"),
                             ("root", "/opt/usr/apps/other-container")):
            with self.subTest(field=field):
                profile = json.loads(self.profile_a.read_text())
                profile["launcher"][field] = value
                invalid = self.profile_dir / f"invalid-{field}.json"
                invalid.write_text(json.dumps(profile))
                with mock.patch.object(sys, "argv", [
                    "build.py", "launcher", "--profile", str(invalid), "--framework", str(self.framework)
                ]), mock.patch.object(build_module.subprocess, "run") as run:
                    with self.assertRaises((SystemExit, ValueError)):
                        build_module.main()
                    run.assert_not_called()

    def test_missing_observed_targets_fail_before_compiler(self):
        for field in ("viewer_app", "native_settings_app"):
            with self.subTest(field=field):
                profile = json.loads(self.profile_a.read_text())
                del profile["launcher_config"][field]
                invalid = self.profile_dir / f"missing-{field}.json"
                invalid.write_text(json.dumps(profile))
                with mock.patch.object(sys, "argv", [
                    "build.py", "launcher", "--profile", str(invalid), "--framework", str(self.framework)
                ]), mock.patch.object(build_module.subprocess, "run") as run:
                    with self.assertRaises((SystemExit, ValueError)):
                        build_module.main()
                    run.assert_not_called()

    def test_setting_option_maps_are_required_and_bound_to_configured_settings(self):
        cases = (
            lambda config: config.pop("setting_options"),
            lambda config: config["setting_options"].update({"unknown": ["0", "1"]}),
            lambda config: config["setting_options"].update({"game-mode": ["0"]}),
            lambda config: config["setting_options"].update({"game-mode": ["0", "0"]}),
            lambda config: config["setting_options"].update({"game-mode": ["0", "unsafe value"]}),
        )
        for index, mutate in enumerate(cases):
            with self.subTest(index=index):
                profile = json.loads(self.profile_a.read_text())
                if index == 1:
                    # Include the unknown name in settings too, to isolate invalid map keys.
                    profile["launcher_config"]["settings"].append("unknown")
                mutate(profile["launcher_config"])
                invalid = self.profile_dir / f"invalid-setting-options-{index}.json"
                invalid.write_text(json.dumps(profile))
                with mock.patch.object(sys, "argv", [
                    "build.py", "launcher", "--profile", str(invalid), "--framework", str(self.framework)
                ]), mock.patch.object(build_module.subprocess, "run") as run:
                    with self.assertRaises((SystemExit, ValueError)):
                        build_module.main()
                    run.assert_not_called()

    def test_catalog_and_configured_app_allowlists_are_bounded(self):
        profile = json.loads(self.profile_a.read_text())
        profile["launcher_config"]["apps"] = [f"org.example.app{i}" for i in range(65)]
        profile["launcher_config"]["settings"] = ["advanced"]
        profile["launcher_config"].pop("setting_options")
        invalid = self.profile_dir / "too-many-apps.json"
        invalid.write_text(json.dumps(profile))
        with mock.patch.object(sys, "argv", [
            "build.py", "launcher", "--profile", str(invalid), "--framework", str(self.framework)
        ]), mock.patch.object(build_module.subprocess, "run") as run:
            with self.assertRaises((SystemExit, ValueError)):
                build_module.main()
            run.assert_not_called()

        catalog = [{"Name": f"App {i}", "Id": f"org.example.app{i}", "Asset": f"app{i}.svg",
                    "Description": "Example", "Width": 100, "Height": 100} for i in range(65)]
        (self.root / "launcher/assets/apps.json").write_text(json.dumps(catalog))
        bounded_profile = json.loads(self.profile_a.read_text())
        bounded_profile["launcher_config"]["settings"] = ["advanced"]
        bounded_profile["launcher_config"].pop("setting_options")
        bounded_profile["launcher_config"]["apps"] = []
        invalid_catalog_profile = self.profile_dir / "catalog-too-large.json"
        invalid_catalog_profile.write_text(json.dumps(bounded_profile))
        with mock.patch.object(sys, "argv", [
            "build.py", "launcher", "--profile", str(invalid_catalog_profile), "--framework", str(self.framework)
        ]), mock.patch.object(build_module.subprocess, "run") as run:
            with self.assertRaises((SystemExit, ValueError)):
                build_module.main()
            run.assert_not_called()

    def test_launcher_smacks_label_uses_profile_value_without_guessing(self):
        profile = json.loads(self.profile_a.read_text())
        profile["launcher"]["smack_label"] = "User::Observed::Label::RO"
        observed = self.profile_dir / "observed-smack-label.json"
        observed.write_text(json.dumps(profile))
        launcher, config = build_module.launcher_metadata(build_module.load_profile(observed))
        self.assertEqual(launcher["smack_label"], "User::Observed::Label::RO")
        self.assertEqual(config["native_settings_app"], "org.tizen.menu")

    def test_all_keeps_root_payload_abi_and_isolates_launcher_paths(self):
        self.active_profile = self.profile_a
        with mock.patch.object(sys, "argv", [
            "build.py", "all", "--profile", str(self.profile_a), "--framework", str(self.framework)
        ]), mock.patch.object(build_module.subprocess, "run", side_effect=self.fake_dotnet):
            build_module.main()
        payload_outputs = [Path(command[command.index("-o") + 1]) for command in self.dotnet_invocations
                           if "vendor/payload-source" in command[2]]
        launcher_outputs = [Path(command[command.index("-o") + 1]) for command in self.dotnet_invocations
                            if "launcher/" in command[2]]
        self.assertEqual(payload_outputs, [self.local / "build/payloads"] * 2)
        self.assertTrue(all(self.profile_a.name.lower() not in str(path) for path in payload_outputs))
        self.assertTrue(all("1300.9" in str(path) for path in launcher_outputs))
        self.assertEqual((self.local / "build/payloads/SamsungTvArchiveRoot.dll").read_bytes(),
                         b"profile-a:SamsungTvArchiveRoot.csproj")
        self.assertEqual((self.local / "build/payloads/SamsungTvRootAgent.dll").read_bytes(),
                         b"profile-a:SamsungTvRootAgent.csproj")

    def test_case_distinct_firmware_get_distinct_exact_profile_paths(self):
        first = {"model": "UN75U8000FFXZA", "firmware": "T-KSU2EFAKUC-0090-Test"}
        second = {"model": "UN75U8000FFXZA", "firmware": "t-ksu2efakuc-0090-test"}
        self.assertNotEqual(build_module.profile_key(first), build_module.profile_key(second))


if __name__ == "__main__":
    unittest.main()
