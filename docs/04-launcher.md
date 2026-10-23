# Build and install Quiet Home

The hardware operations below describe the original U8000F case study. The
DU7200 branch uses [exact-profile deployment and recovery gates](du7200-install.md)
and makes no current DU7200 runtime/host claim. Original container, UID, labels,
HDMI integers and APIs below must never be copied onto an unqualified TV.

Quiet Home is a native NUI/.NET application, with Home/startup and remote-key
routing adjusted to make it act as the launcher. It does not replace the
kernel or the whole Tizen shell. Samsung's settings, app framework and media
stack remain available underneath.

The design follows Free My TV: light background, wide feature card, two
portrait cards, colorful tiles, rounded controls and a translucent dock.
All UI text here is English. See [attribution](../NOTICE.md).

## Obtain matching private framework references

Build against the TV's own compatible `Tizen.NUI.dll`,
`Tizen.Applications.Common.dll`, `Tizen.System.dll` and `Tizen.dll`.
The shipped project files use `Private=false`, so those references are not
copied into the app package.

Find the paths on the target:

```sh
tv exec --command 'find /usr/share/dotnet.tizen -name Tizen.NUI.dll -o -name Tizen.Applications.Common.dll -o -name Tizen.System.dll -o -name Tizen.dll'
```

Inspect the results and pull the four matching runtime assemblies individually
with `tv pull REMOTE_PATH .local/tv-framework/NAME.dll`. Do not use a random
desktop/mobile SDK version just because it has a file with the same name.
Do not commit the resulting assemblies.

```sh
python3 tools/build.py launcher --profile profiles/un75u8000ffxza-1300.9.json --framework .local/tv-framework
```

For this exact historical profile this builds `BixbyPromotion.dll`,
`QuickLaunch.dll` and `Probe.dll`, then assembles a profile-isolated package
below `.local/package/`. The build prints the exact directory. Public source cards are neutral text placeholders;
[optional publisher artwork](06-artwork.md) can be fetched before rebuilding.

## Adapt inputs and apps

The original UI uses HDMI 2 as the wide PlayStation 5 card, HDMI 3 as the
Switch card, and HDMI 1 as the third card. Source IDs 14, 15 and 13 were observed
on the tested TV. They are example wiring, not auto-discovery.

Upload and run Probe to inspect sources and the current main-operation handler:

```sh
tv push .local/build/Probe/Probe.dll Probe.dll
tv exec --command '/usr/bin/dotnet /home/owner/share/tmp/sdk_tools/quiet-tizen/Probe.dll'
```

Configure the verified `launcher_config.inputs`, `default_source`,
`viewer_app`, `native_settings_app`, `settings` and app-ID allow-list in the
exact profile. The build packages `launcher.json`; Home validates it before
using a source, app or optional private setting. The library is read from
`apps.json`; verify all selected app IDs against the TV. See
[app configuration](06-artwork.md). Rebuild after source changes.

## Install without changing Home yet

The tested native-app container is **`com.samsung.tv.bixby-promotion`**.
Its `BixbyPromotion.dll` is replaced with our assembly, while the package's
existing identity, metadata and framework integration are retained. This
replaces Bixby Promotion's function. It is not a store-signed standalone TPK.

That host is historical evidence, not the default for a different model.
The old install/restore scripts now stop before writes. Use
`tools/plan_launcher.py` with the exact qualified profile, fresh inventory,
consistent SQLite snapshot, verified original entry assembly and private
host evidence. [Current plan inputs and outputs](du7200-install.md) and
[recovery ordering](du7200-recovery.md) describe the full contract.

The generated manual-only install/inverse binds exact model, firmware,
paths, metadata, observed source mappings, installed targets and hashes.
It refuses existing custom resources or native-image cache whose safe
invalidation has not been established. It does not stop apps, launch the
replacement, alter registry flags or assign Home/startup. Do not delete the
first backup to retry a partial install.

Verify visibly:

- All pages render; the D-pad, Select and Back behave correctly.
- HDMI cards switch the intended input, with picture and audio.
- Volume/mute and applicable quick settings work.
- Native Advanced and Network settings still open.
- Chosen app shortcuts launch the correct installed apps.
- The Controllers shortcut reaches the Bluetooth device list.

The `build_ms` value in `/tmp/quiet-home-state.txt` measures scene construction,
not input-to-photon latency or complete rendering time.

## Make it open on Home and startup

Save the **three original values** privately first: the main-operation Home
handler printed by Probe, `db/appfw/home_appid`, and
`db/appfw/startup_appid`. They all happened to be `com.samsung.tv.csfs` on the
case-study device; restoration must use the actual saved values.

Home/startup assignment is a separate hardware-qualified phase. The generated
manual install never assigns these selectors. Prepare a new exact selector
snapshot, guarded inverse and operation-bound owner deployment decision.
Read all three selectors back, finish temporary-root cleanup, and test normal
cold boot, Home, standby/wake and return from apps. Automatic startup on the
original TV does not establish this behavior for a DU7200 or later firmware.

## Replace the slow settings/sidebar path

The quick settings page handles volume, mute, brightness, Game Mode and
sleep timer with native APIs. Availability is checked for the current input.
Advanced settings, network setup and Bluetooth pairing still use Samsung's
native menu on demand.

The small QuickLaunch bridge exits on vendor `launch_type=preload` requests.
For interactive requests it opens Quiet Home's settings page and exits.
It does not keep a second heavy settings process running.

The original QuickLaunch overwrite and dual-key recipe is retired in this
branch. `tools/plan_settings_bridge.py` generates field-specific SQL and an
inverse only for an independently qualified More route. It preserves native
`XF86SysMenu` rows and guards them against drift so Settings does not depend
on the custom app surviving. It never overwrites QuickLaunch. The native
source fallback handles synchronous dispatch errors; asynchronous crash
recovery still needs the physical-device tests and original native key.

Optional cleanup is independent. Building a launcher or generating a settings
plan does not authorize the historical `light-shell` group. Keep native
settings, display, audio, inputs, discovery, network remote and pairing paths
untouched. Remove only plan-named staged files after exact hash readback.

## Performance choices in the launcher

App art is local. Real `ImageView` objects are created ahead of use and moved
between the hidden cache parent and visible cards. `ReleasePolicy=Destroyed`
keeps decoded textures alive while those views live. The cache is bounded
by catalog size and the grid/dock variants, not an ever-growing screenshot
cache. Image loading is asynchronous, so a first launch still needs loading
time; subsequent page visits avoid recreating image views.

The clock updates every 30 seconds and stops while the launcher is paused.
There is no always-running controller-poll loop. The 180 ms repeat filter
applies inside Quiet Home only; changing it cannot fix input lag inside Netflix.
Local diagnostic files retain only current launcher state and are overwritten;
the launcher has no telemetry/network client.
