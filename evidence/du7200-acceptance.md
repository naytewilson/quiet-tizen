# UN65DU7200BXZA acceptance receipt

**OFFLINE_ONLY — production NO-GO.** This receipt describes source work, not
a device-qualified release. No TV was contacted, rooted, modified, rebooted
or tested by this work. No original TV backup exists in this checkout, no
DU7200 profile was invented, and no persistent-device permission was granted.

The source starts at upstream `cf518d7a9278dca7990e27756218258e44044a29` and
its publication target is only `naytewilson/quiet-tizen` on
`codex/quiet-tizen-un65du7200b-compat`. The exact final commit and remote
readback belong in the PR and private final receipt after publication.

The final pre-publication offline regression command ran **108 tests, all
passed, zero failures or skips** on the source tree. This includes real POSIX
and BusyBox shell fixtures, synthetic SQLite round trips/drift checks,
first-capture/symlink/partial-failure cases, exact profile A–B–A package tests,
read-only artifact metadata and CLI integration. The two open-source access
payloads subsequently compiled with zero warnings/errors using an isolated
Microsoft .NET SDK 6.0.428; the .NET catalog, launcher configuration and
navigation suite passed. Commands, download hash, artifact hashes and limits
are in `du7200-native-source-checks.md`. The full target NUI build is not
covered by those checks. GitHub CI did not run: the fork reported zero
registered workflows despite Actions permissions being enabled.

| Check | Expected | Observed / verdict |
|---|---|---|
| Script transport | Actual decoded bytes, hash, sentinel, status and bounded response | Offline POSIX/BusyBox regressions pass, including decoder/no-op/truncation and SIGKILL cleanup fixtures. Arbitrary script effects still need independent readback. |
| Exact profiles | No cross-model firmware match, override conflict or inventory-only write grant | Offline schema/cross-model/capability tests pass. All shipped device-write capabilities remain disabled. |
| Read-only discovery | No injection, payload, root or system write | Fixed supported-SDB doctor/inspection code is source-tested. No actual SDB target was used. |
| Package isolation | A then B then A contains no stale cross-profile files | Synthetic compiler/package tests pass. This is not native launcher compilation. |
| Consistent backup / inverse | New immutable `.backup`, quick_check, full hash readback, exact inverses and drift guards | Synthetic SQLite, shell and partial-failure fixtures pass. Actual-firmware BACKUP_VERIFIED and ROLLBACK_REHEARSED are absent. |
| Native settings recovery | Custom controls cannot consume native recovery key | Only More can be routed to custom settings; native SysMenu is preserved and guarded against drift. Runtime recovery is untested. |
| Native AV / Wi-Fi remote | All baseline, post-install and restored stages pass without re-pairing | All 23 rows are NOT_TESTED. Current checker returns NO-GO. Picture/audio/HDMI latency and fidelity are UNMEASURED. |
| Native .NET | Payload/catalog tests and target NUI build | Both open-source payload builds and the native catalog/configuration/navigation suite passed locally with .NET SDK 6.0.428. GitHub CI did not run. Matching DU7200 private NUI references and on-device compilation/runtime remain unavailable. |

The source CLI separates fixed first-backup capture from installer authority.
Capture is limited to the project-owned backup parent if absent and one new
numbered phase directory, with exact target/operation/plan confirmation.
Installation still requires retained hashed originals, an exact inverse and
fresh rehearsal, complete identity/inventory, native AV/remote baseline,
qualified private profile and the owner's separate explicit deployment go-ahead.
Neither JSON assertions nor a green source build authenticate physical evidence.

The original fixed-host installers, settings overwrite and stop-marker recipe
are retired before any write. The reviewed manual plan never launches an app,
assigns Home/startup, changes package flags, stops a service or modifies remote
pairing, network, AV, HDMI or streaming data. Cleanup requires separate exact
dependency evidence and group opt-ins; no DU7200 cleanup group is selected.

Independent review and repair history are in `du7200-independent-review.md`
and `du7200-review-recovery.md`. Hardware-dependent steps, including host/ABI/
SMACK qualification, source APIs, native settings fallback, manual launch,
Home/startup, cold boot, standby/wake, streaming DRM/sign-ins and complete
restore remain UNKNOWN. Review receipts are checkpoints; the final PR binds
the final source and executed test results.

Next eligible hardware step: obtain the current owner-selected LAN target and
supported SDB on a trusted LAN host, collect a fresh read-only doctor/inspection
and baseline, then use observed firmware to qualify a separate DU7200 profile.
Do not use the historical software version to pass a gate.
