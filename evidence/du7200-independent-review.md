# Independent DU7200 source review

**Review result: SOURCE_REVIEW_COMPLETE / DEVICE_QUALIFICATION_NO-GO.** This is a static, read-only review of the exact source snapshot below. It is not a device approval, a production acceptance, or proof that any hardware action occurred.

## Bound source snapshot

| Fact | Evidence / result |
|---|---|
| Repository | Isolated owner-fork checkout (private host path omitted) |
| Branch / base HEAD | `codex/quiet-tizen-un65du7200b-compat` / `cf518d7a9278dca7990e27756218258e44044a29` |
| Working state | Shared review tree contained concurrent uncommitted source, docs and test changes. I did not stage, edit, reset or revert them. |
| Owned change | This report only: `evidence/du7200-independent-review.md`. |
| Initial source/test/recipe digest | `75b181723534497f17d08c627d4367b562d1bca9ddf1a7f74a864494eb8d94d0` |
| Follow-up checkpoint | HEAD `69d8d021b33790795c6f791145f6ef80f3e65b0a`; root-owned follow-up edits were in the shared working tree and are bound by the digest below. |
| Follow-up source/native-check digest | `ba95a2e52d4d1c4aac6b42ae72d9c2b6dc7d5d218ffc5bb1e050f6bd5298a455` |
| Acceptance record | `evidence/du7200-acceptance.json` SHA-256 `3c0a3d36536c7f02ae6a69329087ab77ef305d4d44b8290833b212029292330f` |
| Independent whitespace check | `rtk git diff --check` exited 0 with no output. |

The source/test/recipe digest is the SHA-256 of the ordered `sha256sum` output for the following reviewed files. Recompute it with `rtk proxy sha256sum <listed files> | rtk proxy sha256sum`. Publication-time review and acceptance reports are excluded from this code snapshot:

```text
tools/tv.py tools/profiles.py tools/deployment.py tools/check_acceptance.py tools/inventory.py tools/inspect_artifacts.py tools/build.py tools/plan_launcher.py tools/plan_settings_bridge.py tools/plan_cleanup.py launcher/src/Home.cs launcher/src/QuickLaunch.cs launcher/src/AppLibrary.cs launcher/src/LauncherConfig.cs launcher/src/QuietHomeContainer.targets vendor/payload-source/SamsungTvRootAgent.cs launcher/probe/Probe.cs tests/test_acceptance.py tests/test_cli_gates.py tests/test_deployment.py tests/test_inventory.py tests/test_profiles.py tests/test_recovery.py tests/test_routing.py tests/test_transport.py tests/test_build_profiles.py tests/test_inspect_artifacts.py tests/CatalogTests.cs recipes/install-launcher.sh recipes/install-settings-bridge.sh recipes/restore-launcher.sh recipes/restore-settings-bridge.sh recipes/snapshot.sh recipes/stop-marker.sh
```

## Check / expected / observed / verdict

| Check | Expected | Observed | Verdict |
|---|---|---|---|
| Script transport | Exact decoded bytes, checked hash, per-run execution sentinels, nonzero status preservation, bounded channel and timeout cleanup | `tools/tv.py` decodes in memory, checks SHA-256, quotes arguments and bounds the wrapper. The root agent kills timed-out command groups; pull scratch is placed under the exact root-session directory which the host cleanup removes and verifies. Local SIGKILL/decoder/no-op tests cover these paths. | **PROVEN in source; runtime on a TV UNKNOWN.** The sentinel proves execution/status, not a generic script's intended side effect. Operations needing an effect still need the plan-specific independent readback. |
| Read-only target inspection | Explicit SDB target; no capture/injection/root/push/pull or system writes | `doctor` uses fixed `SdbClient.run` queries with explicit target, bounded output and markers. `inspect` separately reads fixed framework/package metadata and emits constrained manifests; it does not call the root probe. | **PROVEN in source; no SDB target was contacted.** |
| Identity gates | Cross-model or firmware conflicts stop before root/write; declarations never override a proven conflict | Non-doctor actions require selected SDB model and PROVEN matching product model. Firmware resolution rejects disagreement. `exec`, `push` and `snapshot` require live firmware readback. | **PROVEN in source; actual model/firmware UNKNOWN.** |
| Profiles and permissions | Inventory/profile data is not owner consent or write capability | Inventory records `write_qualification.eligible=false`. Shipped DU7200 profile is absent; the U8000F profile has no device-write capabilities, and the S95F candidate has none. Persistent `exec`/`push` require a separate operation digest, complete plan digest and owner record; the deployment module states it is not an authentication service. | **PROVEN in source. No DU7200 write profile or deployment authority is established.** |
| File reads and uploads | A privileged pull is limited to inventoried framework, exact phase backup or original entry, with bound destination, target, size and hash; upload/commands are bound to review | `pull_artifact` restricts type/path, exact target/profile, fresh time, private destination, size and expected SHA-256; readback is verified before save. `push` is bounded and hash-checked. Deployment plan bytes and operation bytes require separate SHA-256 confirmations. | **PROVEN in source. No target file was pulled or uploaded.** |
| Backup and inverse | Backups are immutable and hashed; every declared mutation has a retained original; rollback binds the exact inverse and rehearsal | `snapshot` is a dedicated operation limited to the shipped `recipes/snapshot.sh`, exact model/firmware/name and exact mutation allowlist. It verifies SQLite copies and hashes, refuses symlinks/existing phase, and reports fixed artifact paths/sizes/hashes. Deployment receipts read back retained artifact bytes and bind backups, inverse, rehearsal, inventory and baseline evidence. | **PROVEN in source. A real snapshot/rollback is UNKNOWN.** Snapshot capture requires the existing real vendor parent `/opt/usr/share`; only the project-owned backup directory and phase directory may be created. Missing vendor ancestors fail closed. |
| Native Settings and remote recovery | Native recovery key remains independent; re-pair or missing Wi-Fi reconnect evidence cannot pass | Profile/planner allow only More-key customization and guard `XF86SysMenu` unchanged. The release checker requires fresh exact-identity evidence and structured remote pairing preservation (`repaired=false`, credentials unchanged, wired-TV/Wi-Fi-client topology, reconnect after reboot and standby). | **PROVEN in source; runtime path UNKNOWN.** The native Settings crash/fallback matrix row remains NOT_TESTED. |
| Launcher targets and native settings controls | No U8000 input/app guesses on a new model; unknown controls use native fallback | Build input/app cards come only from validated profile configuration. The install planner checks configured apps, viewer and native Settings targets against fresh installed/enabled package rows and binds source input IDs/labels to host evidence. Brightness is disabled if its range is unavailable/invalid; game/sleep settings use the configured value map; native Settings remains in UI. | **PROVEN in source. Actual source IDs, launcher UID, private API behavior and app launchability remain UNKNOWN until exact-target evidence/runtime tests.** |
| Build, ABI, privacy and licenses | Isolated profile builds, preserved root payload ABI, no bundled private Samsung SDK, no new remote service or permanent poll | Build outputs are profile-keyed and A-B-A tests check stale/cross-profile isolation. NUI references remain local and are not copied. UI has no network client; diagnostic files are local and overwritten. Legacy unqualified install/settings/stop-marker recipes now refuse before mutation. License/provenance files remain present. | **PROVEN in source/tests as reported; native compilation and target ABI UNKNOWN.** |

## Resolved findings during the review

The following concrete issues were found, sent to the owning implementer, and then rechecked in the final source snapshot:

- Privileged pulls now require a private artifact manifest bound to exact path, profile, target, destination, size and hash.
- Persistent operations now require both the exact operation digest and the digest of the full deployment plan, including its mutation list and receipt references.
- A conflicting or unavailable product-model field blocks root operations even if the SDB model string matches.
- Snapshot capture now has its own narrowly scoped plan path instead of requiring a backup/rollback that cannot exist before first capture. Its shell checks exact identity before creating the project-owned parent/phase directories and preserves partial backups for inspection.
- Acceptance checks now reject missing/stale/malformed evidence and a Wi-Fi remote reconnect that requires re-pairing. The SysMenu route remains native.
- Launcher planning now requires installed/enabled app targets and binds configured input IDs/labels to exact host evidence. The artifact inspector checks canonical paths and emits no manifest for symlinked/aliased paths.
- The original decoded-script temp file was removed; pull compression scratch stays inside the root-session directory cleaned and verified by the root helper after timeout.

These fixes are source-level and synthetic-test evidence. They do not qualify any current DU7200 hardware.

## Follow-up: native payload and catalog compilation scope

The follow-up review at HEAD `69d8d021b33790795c6f791145f6ef80f3e65b0a` checked the root-runtime gate, archive-root assessment, Probe sidecar packaging and the catalog/configuration project. The source/test/native-check digest above was computed over this ordered set:

```text
tools/tv.py tools/profiles.py tools/deployment.py tools/check_acceptance.py tools/inventory.py tools/inspect_artifacts.py tools/build.py tools/plan_launcher.py tools/plan_settings_bridge.py tools/plan_cleanup.py launcher/src/Home.cs launcher/src/QuickLaunch.cs launcher/src/AppLibrary.cs launcher/src/LauncherConfig.cs launcher/src/QuietHomeContainer.targets launcher/src/Home.csproj launcher/src/QuickLaunch.csproj launcher/probe/Probe.cs vendor/samsung_tv_root/archive_root.py vendor/payload-source/SamsungTvArchiveRoot.csproj vendor/payload-source/SamsungTvRootAgent.csproj vendor/payload-source/SamsungTvRootAgent.cs tests/test_acceptance.py tests/test_cli_gates.py tests/test_deployment.py tests/test_inventory.py tests/test_profiles.py tests/test_recovery.py tests/test_routing.py tests/test_transport.py tests/test_build_profiles.py tests/test_inspect_artifacts.py tests/CatalogTests.cs tests/CatalogTests.csproj docs/04-launcher.md docs/provenance.md recipes/install-launcher.sh recipes/install-settings-bridge.sh recipes/restore-launcher.sh recipes/restore-settings-bridge.sh recipes/snapshot.sh recipes/stop-marker.sh docs/du7200-execution-plan.md docs/du7200-compatibility.md docs/du7200-install.md docs/du7200-recovery.md docs/du7200-native-av-matrix.md evidence/du7200-native-source-checks.md evidence/du7200-acceptance.json
```

The runtime mismatch finding is resolved in source: all root actions require a profile declaring .NET `6.0.x`, `tv.py` passes `required_runtime="6.0"`, and `archive_root.probe` compares the actually assessed runtime before it starts the callback listener or stages payloads. Unsupported `3.1`, `7.x`, unknown-profile and live 3.1 cases have regression coverage.

Probe packaging now carries `Probe.dll`, `Probe.runtimeconfig.json` and `Probe.deps.json`; the A-B-A build test checks those sidecars, and the stale shared Probe path in `docs/04-launcher.md` is replaced with the exact profile-keyed output path and its required staging constraints. The Python fake-dotnet fixture still checks output/path contracts rather than compiling C#.

`tests/CatalogTests.csproj` directly compiles only `AppLibrary.cs`, `LauncherConfig.cs` and `CatalogTests.cs`; it has no Tizen private references and does not compile `Home.cs` or `QuickLaunch.cs`. Root-recorded native source checks report both open-source root payloads, the catalog/configuration executable, standalone Probe and generated `QuietHomeContainer.targets` smoke project compiled successfully. The Probe loader smoke reached managed `Main` and then failed to resolve Samsung native libraries on Linux, as expected. This proves managed compilation and sidecar handling only; it does not prove Samsung P/Invoke symbols or device runtime behavior. Full NUI Home/QuickLaunch compilation remains unavailable without the exact target's private framework references.

## Remaining UNKNOWN / BLOCKED boundaries

- `evidence/du7200-acceptance.json` is `OFFLINE_ONLY`, has `firmware: null`, and every required native AV, HDMI, audio, lifecycle and Wi-Fi remote row is `NOT_TESTED`. The checker remains NO-GO. No TV, current address, model/firmware readback, backup, native build, application install, reboot, standby/wake or AV/remote test was performed.
- Source inputs remain UNKNOWN unless supplied from a fresh, exact-target Probe capture. Current source text does not decode the opaque `get_source_list()` string into IDs/labels, and no U8000 mapping is applied. The launcher plan must refuse until the owner-observed mapping is established.
- Launcher UID remains UNKNOWN unless exactly one running process for the inventoried entry path is observed with a complete, unambiguous `/proc` UID scan. Unavailable SMACK/file metadata also remains UNKNOWN. These gates are intentional; no profile values fill them.
- The artifact inspector and manifest factories are not an authority/authentication service. SHA-256 proves file identity and the checker validates record structure; neither authenticates a human statement or proves physical hardware truth. The owner must independently inspect the retained private evidence and authorize any future deployment.
- A valid execution sentinel proves the reviewed script bytes ran and returned status. It does not prove a generic command changed the intended object; backup/install artifacts require independent hash and state readback.
- At initial review, no .NET SDK was on the host PATH and `.local/tv-framework` was absent. The owner later used a temporary pinned SDK 6.0.428 under `.local` for payload, catalog, Probe and generated-container checks, then removed it after shutting down build servers. Matching TV-private NUI references remain absent, so full Home/QuickLaunch target compilation and device runtime remain UNKNOWN.

## Verification and cleanup

| Command | Result |
|---|---|
| `rtk git rev-parse --show-toplevel` / `rtk git branch --show-current` / `rtk git rev-parse HEAD` | Expected isolated checkout root; `codex/quiet-tizen-un65du7200b-compat`; `cf518d7a9278dca7990e27756218258e44044a29`. |
| `rtk git diff --check` | Exit 0, no output. |
| Initial-review `rtk proxy command -v dotnet` / `rtk ls -la .local/tv-framework` | No default PATH SDK and no TV-private framework-reference directory. Later owner-run temporary SDK checks are recorded in `evidence/du7200-native-source-checks.md`. |
| Tests / build / TV operations | No test, build, SDB, root, upload or TV operation was run by this reviewer. Root reported 110 offline tests passing and recorded native builds in `evidence/du7200-native-source-checks.md`; those results were not independently executed by me. |
| Process cleanup | No background process or long-running command was started by this reviewer; all shell/Scout calls completed synchronously. No temporary file or test fixture was created by this review. The owner reports the temporary SDK was removed and build servers were shut down. |

Initial source/test/recipe digest: `75b181723534497f17d08c627d4367b562d1bca9ddf1a7f74a864494eb8d94d0`. Follow-up source/native-check digest: `ba95a2e52d4d1c4aac6b42ae72d9c2b6dc7d5d218ffc5bb1e050f6bd5298a455`. At the end of this review, root-owned follow-up edits were still uncommitted; the acceptance record remains NO-GO.
