# DU7200 installation gates and phase plan

**This is a gated plan, not evidence that an installation was performed.**
No television was contacted for this implementation, no package was installed,
and no hardware tests were run. The current acceptance record remains
OFFLINE_ONLY with all capability stages NOT_TESTED. Production is NO-GO until
the exact unit passes the [native AV and remote-control matrix](du7200-native-av-matrix.md).

Samsung's supported development workflow uses an explicitly selected TV and
recommends final testing on the target because hardware behavior can differ
from an emulator. That workflow does not establish that a private package
replacement, Home selector change, or root route is supported on this target.
[Samsung TV Device guidance](https://developer.samsung.com/smarttv/develop/getting-started/using-sdk/tv-device.html)

## Gate 0: preserve the actual TV baseline

Before any persistent change, the owner must record privately:

- The exact model code and full software version from the TV's About screen,
  the selected SDB device row, and /etc/prd_info.ini. Conflicting identity is
  a stop condition.
- Network topology and the current authorized phone/tablet remote client:
  TV wired or wireless, remote client Wi-Fi network, discovery path, remote
  server listener, WebSocket and JSON behavior, and pairing state. Preserve
  pairing credentials; do not export them into the repository.
- Every currently used HDMI port, source name, source device, CEC behavior,
  TV and external audio routes, Bluetooth devices, active apps, and native
  picture/sound/network settings.
- Existing streaming sign-ins and each service's playback/DRM/HDR state.
  Do not sign out, reset the Smart Hub, clear app data, or request credentials
  as part of a launcher test.
- A complete baseline from the matrix. A missing baseline is NOT_TESTED, not
  PASS. Save private screenshots or observations with their timestamp and
  exact model/firmware binding.

The phone remote is part of the acceptance surface. Samsung documents network
remote actions such as Home, Back, mute, volume, app launch, and input switching;
options vary by device. Test the already authorized client while the TV uses
wired Ethernet and the client uses Wi-Fi on its existing network. Do not pair
again to make a test pass. [Samsung SmartThings remote guidance](https://www.samsung.com/us/support/answer/ANS10007047/)

## Gate 1: collect read-only target evidence

Use only an operator-supplied address, supported connection/devices commands,
and the fixed read-only doctor:

    python3 tools/tv.py --host TV_IPV4 doctor

The collector uses SdbClient.run with an explicit SDB serial and fixed shell
commands. It reads ModelName and firmware fields from /etc/prd_info.ini,
selected OS/kernel/runtime facts, framework assembly presence, tools and
selected file capabilities, the two Home/startup selectors, and bounded
read-only SQLite schema/rows. It does not call capture, stage a script, push,
inject, probe, test root, or write target state. See the exact schema and
privacy limits in [compatibility status](du7200-compatibility.md).

An operator must independently verify the UI About screen. The command
returns private machine-readable evidence under ignored .local/inventory.
Do not publish a serial, IP, MAC, account, pairing secret, raw database or
session transcript. If SDB is closed or a command has absent/partial markers,
nonzero status, incomplete DB row set, invalid fields, output overflow, or
timeout, the affected field remains UNKNOWN. Do not use an old firmware value
to fill it in.

## Gate 2: qualify one exact profile and a reversible plan

The compatibility profile must match both the current exact model and full
firmware. It must be based on evidence from this model/firmware combination,
not the U8000F case study, a different DU suffix, an internet specification,
or a historical inventory.

Before writing, the exact current package must be examined on the TV and in
private evidence. Required facts include:

- Current package ID and registry row, executable entry point, package path,
  installed assembly and hash.
- uid:gid, file mode, SMACK label, native-image cache presence and hash,
  package metadata, and package-manager flags.
- Current Home/startup values and any affected key_filter rows.
- Verified original backup paths and hashes, free space, and an inverse for
  every planned file, selector, registry, key-filter, cache, or service change.

The inventory collector does not supply those package filesystem facts or a
snapshot digest. If the selected model/firmware inventory, complete registry,
exact path/metadata, matching backup, or tested inverse is absent, a launcher
plan must refuse; do not manufacture a generic package location or owner.
Keep the install and its inverse bound to the same fresh model, firmware,
current IDs, hashes, and private snapshot.

## Gate 3: stage and inspect locally

Build against framework assemblies collected from the exact target runtime.
Keep framework binaries, package output, private snapshot, logs, and plans
inside ignored .local. Record hashes and inspect the final package contents
before any target write. Source compilation on a desktop or passing offline
tests do not establish target compatibility.

The owner must review the generated phase plan and inverse as a pair. It must
make only the smallest targeted change, preserve the original app identity
and Samsung services, and refuse if a path, owner, label, hash, selector,
current registry ID, or firmware changed between plan and execution. The
plan must not silently switch to a different package, root route, model, or
firmware.

### Offline manual launcher plan

The only launcher-install plan generator is `tools/plan_launcher.py`. It
requires an explicit exact profile, fresh private inventory, immutable local
SQLite snapshot, and fresh private `quiet-tizen.launcher-host.v1` evidence.
There is no default profile, host, package directory, firmware, or package
owner. Example local plan generation:

```sh
python3 tools/plan_launcher.py \
  --profile profiles/EXACT_MODEL_FIRMWARE.json \
  --inventory .local/inventory/EXACT_RUN.json \
  --snapshot .local/EXACT_PHASE/.pkgmgr_parser.db \
  --host-evidence .local/EXACT_PHASE/launcher-host-evidence.json \
  --backup-name launcher-01 \
  --stage-name launcher-package.tar.gz \
  --output .local/plans/launcher-01
```

The selected profile must already bind `launcher_build`, `package_host`,
`launcher_install`, and `rollback_ready` to device evidence. Do not set those
capabilities true to get past the gate. Build output is resolved only from
`.local/package/<profile_key(profile)>`, using the repository's
`tools/build.py` profile key. The planner checks the exact app configuration,
current launcher/viewer/settings/app rows, and the same rows in the snapshot.
The launcher host and every configured app/viewer/native-settings target must
have exactly one installed, enabled package row.
The private host evidence must bind model and firmware, the launcher runtime
UID, exact `source_inputs` ID/label pairs, root path/type/owner/mode, entry
hash/owner/mode/SMACK label and a private original-entry copy, the absent
resource/cache paths, and a hash map for `.pkgmgr_parser.db`,
`.app-context.db`, `.webappservice.db`, and `preferences-before.txt`.
All inventory, snapshot, host-evidence, and original-entry files stay below
`.local/` and their hashes must match before a plan is emitted.

The separate fixed supported-SDB producer is `tools/tv.py --host TV_IPV4`
`--profile .local/EXACT_PROFILE.json inspect`. It records current identity,
bounded framework/entry sizes and full hashes, package path/type/owner/mode,
readable SMACK labels, resource/cache state and a unique observed live-entry
process UID when available. It never uses capture/injection or attempts root.
It writes private host evidence and exact target-bound framework/original-entry
pull manifests under `.local/inventory/`. Missing, ambiguous, unstable or
inaccessible fields stay UNKNOWN and cannot qualify a launcher plan.

An optional `--probe-evidence` attaches an already captured, target/model/fw/
time/hash-bound raw Probe result; it never executes Probe. The current native
source-list value is retained as opaque evidence. Input IDs, physical-port
mapping and owner card aliases must be established from the actual runtime
and hardware; no generic format, source ID or profile label becomes evidence.
A nonrunning/inaccessible entry process cannot prove its launcher UID.

The new plan directory contains `before.json`, `install.sh`, `restore.sh`,
their SHA-256 sidecars, and the named `.tar.gz` archive. The archive contains
only the configured entry DLL and the profile's `quiet-home/` resource files;
symbolic links, special files, and unsafe archive paths are rejected. Stage
the archive at the exact backup path recorded in `before.json`, using the
provided phase name, before a separately authorized deployment. The generated
`install.sh` rechecks the target identity, package root, current entry hash,
owner/mode/SMACK label, registry rows, snapshot hashes, and archive hash. It
installs only the launcher assembly and resources for manual review. It does
not start the app, assign Home/Startup, change registry flags, replace
settings keys, stop services, or alter network, native AV, HDMI, pairing, or
streaming data. `restore.sh` is the exact inverse from that same plan and
retains the numbered original backup.

The legacy `recipes/install-launcher.sh` and `recipes/restore-launcher.sh`
now stop with exit 64 before any write. Do not stage an old U8000F package or
use those recipes. `recipes/snapshot.sh` is the retained snapshot helper; its
arguments are `EXPECTED_MODEL EXPECTED_FIRMWARE UNIQUE_PHASE_NAME`. It checks
the exact identity before creating a new backup, uses SQLite `.backup` and
`quick_check`, writes and verifies SHA-256 readbacks, and never overwrites an
existing phase. A failed snapshot leaves every completed or partial backup
in place for inspection. Its firmware parser permits one optional terminal
semicolon, matching the read-only inventory parser; duplicate, conflicting,
malformed, or cross-firmware values fail before the backup directory is made.
The capture plan's mutation list binds both `/opt/usr/share/quiet-tizen-backups`
and its one numbered phase directory. If the project parent is absent, the
helper creates only that parent under an existing real vendor parent, with
mode 0700. Existing parent metadata is preserved; symlinks or non-directories
are refused. Missing vendor ancestors are never created or relabeled.
After complete hash readback, the helper emits one bounded
`ARTIFACT|absolute_path|size|sha256` line for each SQLite snapshot and the
preferences record, for a separately reviewed private pull manifest.

The supported capture entry point is the fixed `tools/tv.py snapshot NAME`
action, which supplies the selected profile's model and firmware to this
recipe. It cannot execute an arbitrary script or push a file. A future
capture requires an exact profile with `root_session` and `backup_capture`,
explicit temporary-root acknowledgement, a separate owner-approved
operation-bound plan, the current live inventory, and a passing native
AV/existing-remote baseline. Its exact mutation list contains the project-owned
`/opt/usr/share/quiet-tizen-backups` parent (created only if absent) and the
new numbered child `.../NAME`. This capture phase does not require a prior
backup or rehearsed installer rollback because it only creates new files
and never changes the original TV settings or package contents. Confirm both the exact operation digest and
the complete deployment-plan bytes:

```sh
python3 tools/tv.py --host TV_IPV4 \
  --profile profiles/EXACT_MODEL_FIRMWARE.json \
  --acknowledge-temporary-root \
  --deployment-plan .local/deployment/EXACT_SNAPSHOT_PLAN.json \
  --confirm-operation-sha256 OPERATION_SHA256 \
  --confirm-plan-sha256 PLAN_SHA256 \
  snapshot PHASE_NAME
```

After verified capture, `tv.py snapshot` constructs a private
`quiet-tizen.snapshot-report.v1` envelope from the actual verified session
output and observed target/model/firmware. The strict
`inspect_artifacts.from_snapshot_report` parser validates the phase and four
fixed files, then the CLI saves the report, parsed backup hash map and each
`snapshot-pull-*.json` manifest under `.local/inventory/`. No manifest is
created from a partial or conflicting report. Readback remains a separate
explicit `pull` operation with its own exact path, target, size and hash
checks. No TV capture or download was performed for this source-only work.

Plan generation is offline review material, not deployment approval or
device qualification. Any future `tools/tv.py exec --script` operation still
requires the separate exact-profile deployment plan, confirmations, owner
go-ahead, current target evidence, and passed acceptance gates described in
this document. No DU7200 profile or firmware is qualified here.

## Gate 4: staged verification order

Operation-bound private deployment records include a unique exact `mutations`
list. Every target must have a retained `original` artifact with matching
target, path, size and SHA-256 in BACKUP_VERIFIED. ROLLBACK_REHEARSED binds an
`inverse` script and a `rehearsal` result naming its exact hash and identity.
READ_ONLY_INVENTORY binds the actual fresh `quiet-tizen.inventory.v1` file,
including PROVEN identity and complete package evidence. The baseline receipt
binds each hardware reference to an actual retained private artifact. Status
strings or a hash of a status-only receipt are insufficient. These records
preserve review evidence; they do not authenticate owner consent or physical
observations. Confirm the separately approved exact operation digest through
`--confirm-operation-sha256` immediately before a persistent operation.
The reviewed plan's complete bytes also require `--confirm-plan-sha256`, so
the mutation list and all receipt references are covered by confirmation.

The native Settings key (`XF86SysMenu`) is preserved. Only a separately
qualified More-key route may point to the custom settings page. A successful
asynchronous app dispatch can still be followed by a launcher crash, so a
native recovery key must remain independent of the custom launcher.

The offline settings planner is `tools/plan_settings_bridge.py`; its required
inputs are the exact profile, private inventory, immutable
`.app-context.db` snapshot, and a new private output directory:

```sh
python3 tools/plan_settings_bridge.py \
  --profile profiles/EXACT_MODEL_FIRMWARE.json \
  --inventory .local/inventory/EXACT_RUN.json \
  --snapshot .local/EXACT_PHASE/.app-context.db \
  --output .local/plans/settings-01
```

It refuses unless that exact profile independently qualifies
`settings_bridge` and `rollback_ready`. The inventory and snapshot must cover
both `XF86More` and `XF86SysMenu`; only `XF86More` can be selected for update.
Generated apply and inverse transactions guard the complete selected route
set, preserve `XF86SysMenu` exactly as captured, and update only the qualified
More-key route. The recorded `XF86SysMenu` value must itself be confirmed as
the native Settings route on that exact TV before phase acceptance; the
planner preserves the snapshot value and does not independently identify the
destination. Use the generated `apply.sh` and `restore.sh` pair from one
reviewed plan. The legacy settings-bridge recipe pair also stops before any
write. These offline planners do not by themselves grant execution authority.

If a future authorized exact profile passes all earlier gates, test one phase
at a time and stop after any unexpected result:

1. Take and verify a fresh private baseline snapshot. Do not replace or delete
   an earlier snapshot.
2. Install only the isolated launcher assembly while leaving Home, startup,
   settings routing, cleanup, and vendor services untouched.
3. Verify the app can launch manually and return to Samsung Home. Exercise
   the complete native settings UI, all HDMI inputs, apps, audio, Bluetooth,
   network, and existing remote-control path from the matrix.
4. If a settings bridge is separately qualified, snapshot again and change
   only its exact key_filter rows. Verify both affected keys and native menu
   return before continuing.
5. Change Home/startup only after the launcher, settings route, apps, and
   remote controls all pass. Read back each changed selector and compare it
   with the exact planned value.
6. Run cold boot, Home/Back, app return, standby/wake, network reconnect,
   wired-TV/Wi-Fi-client remote reconnect without pairing, and crash fallback.
7. Run tools/check_acceptance.py against evidence/du7200-acceptance.json.
   It must report GO for all baseline, post_install, and restored rows, each
   bound to the same current model and firmware. An exit status of 1 or any
   NOT_TESTED, UNKNOWN, SKIP, or missing row is NO-GO.

Functional observability is not a latency or fidelity measurement. Do not
claim unchanged input-to-photon latency, HDR accuracy, upscaling quality,
frame pacing, audio sync, or codec support without an appropriate
instrumented measurement and retained evidence. Samsung's general guides say
picture menus vary by model/year, Game Mode is limited to selected models,
and audio format options depend on the connected equipment. [Samsung picture settings](https://www.samsung.com/us/support/answer/ANS10006970/),
[Samsung Game Mode](https://www.samsung.com/ca/support/tv-audio-video/use-game-mode-on-your-samsung-smart-tv/),
[Samsung audio formats](https://www.samsung.com/us/support/answer/ANS10005199/)

## Stop and recover

Stop immediately if the screen is blank, Home/Back fails, native settings do
not open, any current app or stream loses its signed-in state, a remote client
needs re-pairing, CEC/audio routing changes unexpectedly, a service crashes,
or the target stops matching the plan. Follow the exact phase in
[DU7200 recovery](du7200-recovery.md). Do not proceed to a later phase to
“see whether it settles,” and do not factory reset or clear user data as a
launcher recovery step.
