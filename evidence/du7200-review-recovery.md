# DU7200 recovery worker receipt — offline source only

> Historical worker checkpoint. The later integration repaired first-parent
> snapshot capture and added the supported read-only artifact producer. Use
> the final independent review and acceptance receipt for current source status.

## Source truth

- Checkout: `isolated owner-fork checkout`
- Branch: `codex/quiet-tizen-un65du7200b-compat`
- Base HEAD observed at task start: `cf518d7a9278dca7990e27756218258e44044a29`
- ANVIL task preflight: persisted `TASK_PREFLIGHT_READY`, event `89792`, operation `codex-resume-recovery-20261023`.
- The worktree already contained edits from other owners. No reset, checkout, stash, clean, commit, or push was performed.

## Recovery work

`tools/plan_launcher.py` now builds a private manual-launcher plan only from an explicit exact profile, fresh private inventory, SQLite `.backup`, and private launcher-host evidence. It binds the profile build directory through `build.profile_key`; validates the original assembly copy/hash, root path/type/owner/mode, launcher UID, entry owner/mode/SMACK, absent resource and native-image paths, source-input ID/label pairs, package registry snapshot hashes, and current app rows. The launcher host, viewer, native-settings app, and configured app shortcuts must each have exactly one installed and enabled row in both current inventory and snapshot. The generated archive is an exact profile-bound set of regular files; symlinks, special files, unsafe paths, malformed launcher configuration, and hash drift fail closed.

The generated `install.sh` is a manual file installation phase. It does not start the app, assign Home/Startup, change package flags, change settings routes, stop services, or touch native AV, HDMI, networking, Bluetooth pairing, or streaming data. `restore.sh` is bound to the same model, firmware, package paths, before/after hashes, metadata, and retained numbered backup. Neither generated script grants deployment approval or physical-device qualification.

`recipes/snapshot.sh` takes `EXPECTED_MODEL EXPECTED_FIRMWARE UNIQUE_PHASE_NAME`, compares one ModelName and one Version/FirmwareVersion value against the exact profile, allows one optional terminal firmware semicolon, and rejects duplicates or mismatches before creating the backup directory. It uses SQLite `.backup`, `quick_check`, no-overwrite checks, per-file checksum readback, and an aggregate checksum readback. On success it reports four `ARTIFACT|absolute_path|size|sha256` lines for the fixed database/preference backups. A partial failure leaves prior and partial backup files in place.

`tools/tv.py` now exposes a fixed `snapshot NAME` action for that capture. The action supplies model and firmware from the selected profile, checks live identity, and requires explicit temporary-root acknowledgement, an exact operation digest, exact plan-byte confirmation, and the dedicated `backup_capture` capability. Its plan is restricted to creating the one new numbered snapshot directory and requires current inventory plus a passing native AV/existing remote baseline; it does not require an older backup or rollback rehearsal. The action cannot route `backup_capture` authority into arbitrary `exec` or `push`. No such action was invoked in this task.

The legacy launcher/settings install and inverse recipes now exit 64 before any write. The direct service-marker recipe is also blocked because no generated profile-bound per-group marker inverse exists. The supported offline settings planner can update only `XF86More`; its inventory and snapshot cover both `XF86More` and `XF86SysMenu`, and generated SQL guards the captured System Menu route for both apply and restore without updating it. Cleanup planning retains exact group dependency evidence and one owner opt-in per selected group.

Two source workflow gates remain open. The `backup_capture` action requires
`/opt/usr/share/quiet-tizen-backups` to already exist as a real non-symlink
directory; its single allowed mutation is only the new phase child, and
current read-only inventory does not prove the parent path. An absent parent
therefore blocks capture. Also, there is no supported read-only producer for
the launcher-host/source-input evidence and original-entry copy, or a tool
that turns snapshot `ARTIFACT` output into the exact bounded-pull manifest.
The planner refuses to generate without these inputs; they remain BLOCKED
until those source workflows are implemented and reviewed.

## Verification

| Check | Expected | Observed | Verdict |
|---|---|---|---|
| Full offline suite: `rtk proxy env ANVIL_AGENT=codex-resume-recovery python3 -m unittest discover -s tests -v` | All tests pass | 91 tests, `OK` | PASS |
| Shell syntax: `rtk sh -n recipes/*.sh` | Every recipe parses | Exit 0 | PASS |
| Diff whitespace: `rtk git diff --check` | No whitespace errors | Exit 0 | PASS |
| Docs links: `rtk proxy env ANVIL_AGENT=codex-resume-recovery python3 -m unittest tests.test_package.PackageTests.test_relative_document_links_resolve -v` | Relative links resolve | 1 test, `OK` | PASS |

The recovery fixtures use synthetic model IDs, firmware values, package rows, input mappings, and filesystem paths. They do not establish a DU7200 profile or any hardware capability.

## Changed files and source hashes

These hashes bind the recovery worker's owned implementation, tests, recipes, and install/recovery documentation at handoff. The hash of this receipt is intentionally not self-referential.

| File | SHA-256 |
|---|---|
| `tools/plan_launcher.py` | `decc4b31608ee46eb8cef1d096a2e9e5631014f1abcc2987475260a563eb07e2` |
| `tools/plan_cleanup.py` | `01f128becdeb4a679bf4f6a062e29088fa3ea51ec019cd50760711ffb6c6ad66` |
| `tools/plan_settings_bridge.py` | `e734bd03dfe2bdd0003d5e50ab4e275393be3f9fa378e7cae540528a2c1e7176` |
| `tests/test_recovery.py` | `229fd6b9bf1bc6504063437fbe00bfce926edbe54d0d1b1087ef5e02bd89871b` |
| `tests/test_routing.py` | `a5b6e1648a0e7315f941d60dbcb39faa3223d2ed5ca60ef795ca4bf04b1822d3` |
| `recipes/snapshot.sh` | `8eeb79f66b6279ad4e31a0c0ad60981a85cda1e9f6293c7ea3ed460e280ad4e8` |
| `recipes/install-launcher.sh` | `37a14648d3e8f0281780b3aff0e875afb171d99dd8c6cf5144e909faf798dce2` |
| `recipes/restore-launcher.sh` | `8c7ad60f3b7332e91b2a279b52d008181bb156211b0de3d4a390d46afdaba3bb` |
| `recipes/install-settings-bridge.sh` | `ed0268c603b20982683a66a25be9f3cebaea585318f9cb2fcc37c22bc853d6a8` |
| `recipes/restore-settings-bridge.sh` | `ea240a400e2914d98492fefd995a425f2a2fb5e969dc8b13af830719b4c97d20` |
| `recipes/stop-marker.sh` | `8e7bd8035ddb7dd711f7c5490f4ec216b15bc7c623b06c40e26d985b57651c67` |
| `docs/du7200-install.md` | `1fb38c93b878940f8ed1774878834b926adb2703ee29d21dfab5b1d08768a6ce` |
| `docs/du7200-recovery.md` | `11e2d3a9954f8226b1a2a84c00490c1989267a0e868d3d6a33f1967969ba7539` |

## Limits and cleanup

- No DU7200 model/firmware pair, capability profile, or live firmware value was invented or promoted.
- No television was contacted. No SDB, root, upload, package install, Home/settings/cleanup mutation, network discovery, AV test, or stream test was performed.
- No `.NET` launcher build, hardware validation, or production qualification is claimed. Acceptance remains `OFFLINE_ONLY` / `NO-GO` pending fresh exact-device evidence and the full native AV/remote matrix.
- Synthetic test `TemporaryDirectory` fixtures removed their private temporary directories; a targeted scan found none under their test prefixes. The five `.pyc` files created by this worker's compile/test runs were listed, size-checked, and removed individually. Both `pgrep` checks returned no matches for unittest, launcher-planner, or snapshot-script processes. All test commands completed synchronously. No existing `.local/package` artifacts were deleted.
