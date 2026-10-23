#!/usr/bin/env python3
"""Build local payloads or an exact-profile Quiet Home package.

Samsung framework references remain local and are never copied into the package.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from profiles import load_profile, require_capability

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_FIELDS = {"app_id", "dll", "root", "uid", "owner", "smack_label"}
APP_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
ENTRY_DLL = re.compile(r"\A[A-Za-z][A-Za-z0-9_.-]{0,95}\.dll\Z")
OWNER = re.compile(r"\A[0-9]{1,5}:[0-9]{1,5}\Z")
SMACK_LABEL = re.compile(r"\A[A-Za-z0-9_:.-]{1,200}\Z")


def profile_key(profile):
    """Return a deterministic, filesystem-safe key for the exact TV identity."""
    model = profile["model"].lower()
    firmware = profile["firmware"].lower()
    if not re.fullmatch(r"[a-z0-9_.-]{1,64}", model) or not re.fullmatch(r"[a-z0-9_.-]{1,128}", firmware):
        raise ValueError("Profile identity cannot be used as a build directory name.")
    identity = f"{profile['model']}\0{profile['firmware']}".encode("utf-8")
    suffix = hashlib.sha256(identity).hexdigest()[:12]
    return f"{model}-{firmware}-{suffix}"


def launcher_metadata(profile):
    launcher = profile.get("launcher")
    config = profile.get("launcher_config")
    if not isinstance(launcher, dict) or set(launcher) != LAUNCHER_FIELDS:
        raise ValueError("Launcher build needs app_id, dll, root, uid, owner and smack_label.")
    app_id = launcher["app_id"]
    dll = launcher["dll"]
    root = launcher["root"]
    if not isinstance(app_id, str) or not APP_ID.fullmatch(app_id):
        raise ValueError("Invalid launcher application ID.")
    if not isinstance(dll, str) or not ENTRY_DLL.fullmatch(dll) or Path(dll).name != dll:
        raise ValueError("Launcher entry DLL must be a validated filename.")
    if root != f"/opt/usr/apps/{app_id}":
        raise ValueError("Launcher root must exactly match its native package ID.")
    if type(launcher["uid"]) is not int or not 1 <= launcher["uid"] <= 65535:
        raise ValueError("Invalid launcher UID.")
    if not isinstance(launcher["owner"], str) or not OWNER.fullmatch(launcher["owner"]):
        raise ValueError("Invalid launcher file owner.")
    if not isinstance(launcher["smack_label"], str) or not SMACK_LABEL.fullmatch(launcher["smack_label"]):
        raise ValueError("Launcher SMACK label must be supplied as a validated observed value.")
    if not isinstance(config, dict) or set(config) - {
            "inputs", "default_source", "settings", "apps", "viewer_app", "native_settings_app",
            "setting_options"}:
        raise ValueError("Launcher build needs an explicit launcher_config.")
    viewer_app = config.get("viewer_app")
    native_settings_app = config.get("native_settings_app")
    if not isinstance(viewer_app, str) or not APP_ID.fullmatch(viewer_app):
        raise ValueError("Launcher build needs an observed viewer_app ID.")
    if not isinstance(native_settings_app, str) or not APP_ID.fullmatch(native_settings_app):
        raise ValueError("Launcher build needs an observed native_settings_app ID.")
    inputs = config.get("inputs")
    if not isinstance(inputs, list) or not 1 <= len(inputs) <= 3:
        raise ValueError("Launcher build needs one to three validated input cards.")
    source_ids = []
    for item in inputs:
        if (not isinstance(item, dict) or set(item) != {"id", "label"}
                or type(item["id"]) is not int or not 0 <= item["id"] <= 65535
                or not isinstance(item["label"], str) or not item["label"].strip()
                or len(item["label"]) > 80 or any(ord(char) < 32 or ord(char) == 127 for char in item["label"])):
            raise ValueError("Launcher input IDs and labels must be explicit and valid.")
        source_ids.append(item["id"])
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("Launcher input IDs must be unique.")
    if type(config.get("default_source")) is not int or config["default_source"] not in source_ids:
        raise ValueError("Launcher default source must be explicitly configured.")
    settings = config.get("settings", [])
    apps = config.get("apps", [])
    setting_options = config.get("setting_options", {})
    if not isinstance(settings, list) or not isinstance(apps, list) or not isinstance(setting_options, dict):
        raise ValueError("Launcher settings and app IDs must be arrays.")
    if len(settings) != len(set(settings)) or any(setting not in {
            "brightness", "game-mode", "sleep-timer", "volume", "mute",
            "advanced", "network", "bluetooth"} for setting in settings):
        raise ValueError("Launcher settings must use the validated setting allow-list.")
    if len(apps) > 64:
        raise ValueError("Launcher app allow-list exceeds the 64-app cache bound.")
    if len(apps) != len(set(apps)) or any(not isinstance(app_id, str) or not APP_ID.fullmatch(app_id)
                                           for app_id in apps):
        raise ValueError("Launcher app IDs must be valid and unique.")
    enum_settings = {"game-mode", "sleep-timer"}
    if set(setting_options) - (enum_settings & set(settings)):
        raise ValueError("Setting options are only allowed for configured game-mode and sleep-timer settings.")
    validated_options = {}
    for name in enum_settings & set(settings):
        values = setting_options.get(name)
        if not isinstance(values, list) or not 2 <= len(values) <= 32:
            raise ValueError("Each configured enum setting requires its observed ordered value map.")
        if any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", value)
               for value in values):
            raise ValueError("Invalid setting option.")
        if len(values) != len(set(values)):
            raise ValueError("Duplicate setting options.")
        validated_options[name] = values
    native_config = {
        "inputs": inputs,
        "default_source": config["default_source"],
        "viewer_app": viewer_app,
        "native_settings_app": native_settings_app,
        "settings": settings,
        "apps": apps,
        "setting_options": validated_options,
    }
    return launcher, native_config


def check_catalog(profile, assets_root):
    config = profile["launcher_config"]
    local_catalog = assets_root / "apps.local.json"
    catalog_path = local_catalog if local_catalog.is_file() else assets_root / "apps.json"
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read launcher catalog {catalog_path.name}: {error}") from error
    if not isinstance(catalog, list) or len(catalog) > 64:
        raise ValueError("Launcher app catalog must be an array bounded to 64 entries.")
    ids = [entry.get("Id") for entry in catalog if isinstance(entry, dict)]
    if (len(ids) != len(catalog) or len(ids) != len(set(ids))
            or any(not isinstance(app_id, str) or not APP_ID.fullmatch(app_id) for app_id in ids)):
        raise ValueError("Launcher app catalog has malformed or duplicate entries.")
    missing = sorted(set(config.get("apps", [])) - set(ids))
    if missing:
        raise ValueError("Configured launcher apps are absent from the selected catalog: " + ", ".join(missing))


def confined_generated_path(root, base, key, leaf=None):
    local_root = root / ".local"
    components = [base, key]
    if leaf is not None:
        components.append(leaf)
    if local_root.is_symlink():
        raise ValueError(f"Refusing generated path under symlinked .local: {local_root}")
    path = local_root
    for component in components:
        path = path / component
        if path.is_symlink():
            raise ValueError(f"Refusing generated-path symlink: {path}")
    resolved_local = local_root.resolve(strict=False)
    resolved = path.resolve(strict=False)
    if not resolved.is_relative_to(resolved_local):
        raise ValueError(f"Refusing generated path outside .local/{base}: {path}")
    return path


def remove_generated(path):
    if path.is_symlink():
        raise ValueError(f"Refusing to remove generated-path symlink: {path}")
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def run_build(target, dotnet, framework, profile=None):
    launcher = native_config = None
    key = None
    if target in ("launcher", "all"):
        if profile is None:
            raise ValueError("--profile is required for launcher and all builds.")
        profile_data = require_capability(load_profile(profile), "launcher_build")
        launcher, native_config = launcher_metadata(profile_data)
        key = profile_key(profile_data)
        check_catalog(profile_data, ROOT / "launcher/assets")
        for name in ("Tizen.NUI", "Tizen.Applications.Common", "Tizen.System", "Tizen"):
            if not (framework / f"{name}.dll").is_file():
                raise ValueError(f"Missing {name}.dll. Read docs/04-launcher.md; these are not bundled.")

    jobs = []
    if target in ("payloads", "all"):
        # Root probe payloads have a stable profile-independent path; launcher
        # assemblies and their package are isolated under the exact TV profile.
        payload_build = confined_generated_path(ROOT, "build", "payloads")
        payload_obj = confined_generated_path(ROOT, "obj", "payloads")
        remove_generated(payload_build)
        remove_generated(payload_obj)
        for name in ("SamsungTvArchiveRoot", "SamsungTvRootAgent"):
            jobs.append((ROOT / f"vendor/payload-source/{name}.csproj", "payloads", name,
                         payload_build, payload_obj / name, []))
    if target in ("launcher", "all"):
        build_root = confined_generated_path(ROOT, "build", key)
        obj_root = confined_generated_path(ROOT, "obj", key)
        package_root = confined_generated_path(ROOT, "package", key)
        staging = confined_generated_path(ROOT, "package", f".{key}.staging")
        remove_generated(build_root)
        remove_generated(obj_root)
        remove_generated(package_root)
        remove_generated(staging)
        build_root.mkdir(parents=True, exist_ok=True)
        obj_root.mkdir(parents=True, exist_ok=True)
        app_id = launcher["app_id"]
        container_root = launcher["root"]
        resource_root = f"{container_root}/res/quiet-home/"
        viewer_app = native_config["viewer_app"]
        native_settings_app = native_config["native_settings_app"]
        common = [
            f"-p:TV_FRAMEWORK_DIR={framework.resolve()}",
            f"-p:QuietHomeAppId={app_id}",
            f"-p:QuietHomeRoot={container_root}",
            f"-p:QuietHomeResourceRoot={resource_root}",
            f"-p:QuietHomeViewerApp={viewer_app}",
            f"-p:QuietHomeNativeSettingsApp={native_settings_app}",
        ]
        home_assembly = Path(launcher["dll"]).stem
        jobs.append((ROOT / "launcher/src/Home.csproj", "Home", "Home",
                     build_root / "Home", obj_root / "Home",
                     [*common, f"-p:AssemblyName={home_assembly}"]))
        jobs.append((ROOT / "launcher/src/QuickLaunch.csproj", "QuickLaunch", "QuickLaunch",
                     build_root / "QuickLaunch", obj_root / "QuickLaunch", common))
        jobs.append((ROOT / "launcher/probe/Probe.csproj", "Probe", "Probe",
                     build_root / "Probe", obj_root / "Probe", []))

    for project, _, _, output_dir, intermediate_dir, extra in jobs:
        output_dir.mkdir(parents=True, exist_ok=True)
        intermediate_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run([
            dotnet, "build", str(project), "-c", "Release", "--nologo",
            "-o", str(output_dir),
            f"-p:BaseIntermediateOutputPath={intermediate_dir}/",
            "-p:Deterministic=true",
            f"-p:PathMap={ROOT}=/_/quiet-tizen", *extra,
        ], check=True, env={**os.environ, "DOTNET_CLI_TELEMETRY_OPTOUT": "1"})

    if target in ("launcher", "all"):
        staging.mkdir(parents=True)
        try:
            entry_dll = launcher["dll"]
            shutil.copyfile(build_root / "Home" / entry_dll, staging / entry_dll)
            shutil.copyfile(build_root / "QuickLaunch" / "QuickLaunch.dll", staging / "QuickLaunch.dll")
            shutil.copyfile(build_root / "Probe" / "Probe.dll", staging / "Probe.dll")
            assets = staging / "quiet-home"
            shutil.copytree(ROOT / "launcher/assets", assets)
            local_catalog = ROOT / "launcher/assets/apps.local.json"
            if local_catalog.is_file():
                shutil.copyfile(local_catalog, assets / "apps.json")
            (assets / "apps.local.json").unlink(missing_ok=True)
            (assets / "launcher.json").write_text(
                json.dumps(native_config, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            remove_generated(package_root)
            os.replace(staging, package_root)
        finally:
            remove_generated(staging)
        print(f"Deployable files: .local/package/{key}/. Review its profile-bound inputs and catalog before use.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=["payloads", "launcher", "all"])
    parser.add_argument("--dotnet", default=os.environ.get("DOTNET", "dotnet"))
    parser.add_argument("--framework", type=Path, default=ROOT / ".local/tv-framework")
    parser.add_argument("--profile", type=Path,
                        help="Exact TV profile; required for launcher and all builds")
    args = parser.parse_args()
    try:
        run_build(args.target, args.dotnet, args.framework, args.profile)
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
