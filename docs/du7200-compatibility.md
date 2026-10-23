# UN65DU7200B compatibility status

**Decision: NO-GO for production use on a television.** This worktree has no
current device and firmware readback and has no hardware verification. The
inventory implementation and local tests are offline source evidence only.
Every target-dependent capability below remains UNKNOWN until it is measured
on the exact television and firmware.

Samsung lists the product code UN65DU7200BXZA in its support catalog. That
catalog entry does not identify the television connected to this worktree or
prove its installed firmware. Record the About This TV screen and compare it
with the read-only inventory before selecting any profile. Samsung also says
TV and emulator behavior can differ at the hardware level and recommends
testing on the target device. [Samsung model support](https://www.samsung.com/us/support/service/warranty/UN65DU7200BXZA/),
[Samsung TV Device testing guidance](https://developer.samsung.com/smarttv/develop/getting-started/using-sdk/tv-device.html)

## Evidence matrix

| Area | Current status | Evidence needed before qualification |
|---|---|---|
| Exact model identity | UNKNOWN | Match physical About screen, selected SDB devices row, and ModelName in /etc/prd_info.ini. Any disagreement is a stop condition. |
| Installed firmware | UNKNOWN | Read the TV's About screen and the exact Version field in /etc/prd_info.ini. The parser accepts one strict firmware token with at most one terminal semicolon; duplicate or conflicting fields remain UNKNOWN. |
| Platform, kernel, architecture, Tizen release | UNKNOWN | Read uname and the small ID/VERSION_ID subset of /etc/os-release on the selected TV. |
| .NET runtime path and core runtime files | UNKNOWN | Resolve the TV's netcoreapp path and inspect the presence of libcoreclr.so and System.Private.CoreLib.dll. |
| Tizen framework assembly paths | UNKNOWN | Inventory the target's Tizen.NUI.dll, Tizen.Applications.Common.dll, Tizen.System.dll, and Tizen.dll. Build only against the matching TV runtime references. |
| Package registry and Home/startup selectors | UNKNOWN | Read the package_app_info schema and bounded app ID/flag rows, filtered key_filter rows, and the two app framework selectors. |
| Temporary-root route and privilege behavior | UNKNOWN | This inventory checks selected tools and file capabilities only. It does not execute a root path, use an exploit, or establish that a root route works. |
| Launcher package identity, entry assembly, package path and metadata | UNKNOWN | Obtain exact package metadata, file paths and hashes, uid/gid, mode, SMACK label, native-image cache state, and verified inverse from the exact target. The inventory does not collect these facts. |
| Picture, audio, gaming, HDMI, native settings, app and remote-control behavior | NOT_TESTED | Complete every baseline and post-install row in the [native AV and remote-control matrix](du7200-native-av-matrix.md) on actual hardware. |

The string T-KSU2EDAKUC-0090-2130.0 is present in historical DU7200
workspace notes. It is not a current firmware observation and must not be used
as a profile selector. Likewise, the U8000F profile in this repository is a
different case study; its runtime, package IDs, selectors, key mappings,
source IDs, paths, ownership, labels, or settings do not transfer to a DU7200.

Samsung notes that available picture screens differ by model and year and that
Game Mode exists only on select models. General Samsung guidance describes
ARC/eARC, CEC, and audio formats, but does not establish those options for this
specific unit. Treat every such feature as UNKNOWN until the target's menus,
ports, connected equipment, and signals are checked. [Samsung picture settings](https://www.samsung.com/us/support/answer/ANS10006970/),
[Samsung Game Mode](https://www.samsung.com/ca/support/tv-audio-video/use-game-mode-on-your-samsung-smart-tv/),
[Samsung eARC guidance](https://www.samsung.com/us/support/answer/ANS10006962/),
[Samsung Anynet+ CEC guidance](https://www.samsung.com/ca/support/tv-audio-video/anynet-hdmi-cec-on-your-samsung-tv/),
[Samsung audio format guidance](https://www.samsung.com/us/support/answer/ANS10005199/)

## Read-only inventory

Use an operator-supplied TV IPv4 address and the supported SDB client. The
doctor path must call tools/inventory.py through SdbClient.run using explicit
target shell commands; it must not use SdbClient.capture, shell injection,
payload staging, push, pull, root checks, probes, or configuration writes.

    python3 tools/tv.py --host TV_IPV4 doctor

If SDB is not on PATH, pass the separately installed SDB executable with the
CLI's explicit SDB option. Do not auto-discover a target. The full machine
result belongs under ignored .local/inventory with restrictive permissions.
Do not publish the target address, serial, MAC address, device identifier,
account details, raw database files, or credentials.

The machine result uses schema quiet-tizen.inventory.v1. collected_at is a UTC
timestamp; each field carries its own status, source, and observed_at time.
Statuses are PROVEN or UNKNOWN. UNKNOWN includes a sanitized error code. The
operations are sequential rather than an atomic device snapshot. The selected
database evidence is bounded: package_app_info returns up to 256 current rows;
key_filter is restricted to XF86More and XF86SysMenu and up to 128 rows. An
over-limit result is explicitly incomplete and UNKNOWN. No database digest or
filesystem package identity is created by doctor alone. The separate
`tv.py --host TV_IPV4 --profile .local/EXACT_PROFILE.json inspect` producer
collects bounded framework and selected-host hash/size/metadata through fixed
supported SDB reads and creates private target-bound pull manifests. It
does not attempt root or automatically qualify a host. UID, source mappings,
SMACK or any unreadable metadata remain UNKNOWN when not actually observed.

The firmware source is the exact target file /etc/prd_info.ini. The fixed
collector prints only ModelName, Version, and FirmwareVersion lines, not the
rest of that configuration file. It validates the selected SDB target and
requires the selected-device model row and prd_info model to agree whenever
both are present. A closed connection, zero-exit no-op, missing/duplicated
sentinels, nonzero command, oversized output, malformed fields, ambiguous
identity, or ambiguous firmware never becomes PROVEN evidence.

Inventory is not compatibility qualification and never enables writes.
Unknown model values may still be recorded as evidence but cannot select a
write profile. A profile must bind the exact model and exact current firmware;
all required package paths, current app IDs, metadata, settings, backups, and
inverse behavior must also be independently verified. Any missing fact blocks
the next persistent step.

## Current release gate

The release checklist is evidence/du7200-acceptance.json and its fail-closed
checker is tools/check_acceptance.py. Its current source record is OFFLINE_ONLY;
the firmware is null, and all native capability stages are NOT_TESTED. The
checker is expected to return exit status 1 with NO-GO until the model and
firmware are current and every required baseline, post-install, and restored
result has PROVEN actual-hardware evidence bound to that same pair. Fixture
data and source tests cannot qualify hardware. Observable behavior alone also
does not measure latency, HDR accuracy, or audio fidelity.
