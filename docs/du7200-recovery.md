# DU7200 phase-specific recovery and rollback

**Recovery readiness: UNKNOWN. Production decision: NO-GO.** No DU7200
snapshot, original package hash, package metadata, selector value, key-filter
state, or actual hardware recovery test is available in this worktree. The
checklists below define the evidence and stop points for a future exact,
authorized change. They are not proof that rollback was performed or tested.
See the [current compatibility matrix](du7200-compatibility.md), the
[install gates](du7200-install.md), and the
[native AV and remote-control matrix](du7200-native-av-matrix.md).

## Recovery invariants

- Match the current exact model and full firmware to the plan and the saved
  pre-change snapshot before restoring anything. A mismatch, update, missing
  field, changed row set, or uncertain TV identity blocks automated rollback.
- Use the backup from the phase that is being reversed. Verify its digest
  before using it and verify the restored target file or selector afterward.
  Never reuse a backup from another model, firmware, install ID, or phase.
- Restore only objects that the matching plan records as changed. Do not
  restore a full package registry or replace a whole SQLite database to fix
  one app row or one key_filter entry.
- Keep original owner/group, mode, SMACK label, metadata, and any original
  native-image cache state. Do not delete a cache or vendor file because a
  path looks familiar.
- Preserve user accounts, app data, signed-in streaming apps, Wi-Fi settings,
  Samsung settings, network-remote service, discovery, WebSocket/JSON
  behavior, and pairing credentials.
- If the exact backup is absent or verification fails, stop. Preserve logs
  privately and use Samsung support or an already-qualified recovery route.
  Do not improvise with a factory reset, credential reset, or broad cleanup.

## Read-only recovery precheck

Before any inverse write, collect a new explicit-target doctor inventory and
compare it with the plan's model, firmware, current app IDs, selectors, and
registry completeness. The inventory lives in ignored .local/inventory and
contains private target identity. A current inventory cannot replace a
phase-specific backup or package filesystem evidence.

Check the private plan and snapshot for:

- Exact model, full firmware, timestamp, selected SDB target, and complete
  package_app_info/key_filter evidence.
- Phase identifier, intended target path or semantic row selector, original
  and new hashes/values, owner/group, mode, SMACK label, package flags, cache
  evidence, and verified inverse.
- Backup filename, digest, size, and preserved original metadata.
- A recovery contact/path if the TV cannot reach Home, native settings, or
  the existing network remote.

Do not print secrets or raw database contents into public evidence. If the
snapshot is partial, the row count exceeds the inventory cap, or any
precondition differs, mark recovery BLOCKED and stop.

Current host metadata and source-input evidence must come from actual read-only
observations, never a hand-authored fixture. Missing field evidence blocks its
exact dependent plan. The capture action's reviewed mutation list covers its
project-owned parent (created only if absent) and one numbered phase directory.
It refuses symlinks, preserves existing parent metadata and creates no vendor
ancestor.

## Use the matching generated inverse

For a future manual launcher phase, keep the exact private output directory
from `tools/plan_launcher.py` together: `before.json`, the
`install.sh`/`restore.sh` pair and their SHA-256 sidecars, the named package
archive, private inventory and host evidence, SQLite snapshots, and the
original-entry copy identified by host evidence. The generated plan records
its exact profile, model/firmware, package build key, launcher app row and
configured app targets, file hashes, package root, uid:gid, mode, SMACK label,
resource path, and backup path. Do not reconstruct a restore command from an
older profile or a different phase.

`restore.sh` must run from the same separately authorized deployment plan
after a new exact-identity precheck. It rechecks the phase snapshot and staged
archive hashes, current entry hash and metadata, and the generated resource
tree. If the current assembly is neither the recorded original nor installed
hash, a resource has changed or an unexpected path exists, or the target
firmware differs, it stops and preserves the numbered backup for review. It
retains `launcher-entry-before.dll`; any native-image directory created after
the initially absent cache is moved into the same phase backup before restore.
Do not retry the installer to clear a partial failure. Inspect the exact
partial state and use only the matching inverse when its preconditions pass.

The manual launcher phase never assigns Home/Startup and does not change
package flags or settings routes. A settings bridge, if separately qualified,
uses `tools/plan_settings_bridge.py` and its exact `apply.sh`/`restore.sh`
pair. Its inventory and snapshot include both More and System Menu rows, but
the planner can update only `XF86More`; `XF86SysMenu` remains the native
Settings route only when that exact baseline has been verified on the TV. The
planner preserves the snapshot value and guards it unchanged; it does not
infer the native destination from the key name. If current inventory and
snapshot disagree on either selected key, do not apply or restore
automatically.

## Inverse checklist by phase

| Changed phase | Exact inverse to review and execute only from matching evidence | Read-back and acceptance |
|---|---|---|
| Files staged but no target change | Remove only the single staging item named by the plan after its expected hash is checked. Preserve the private source artifact and prior logs. | Confirm the TV's package paths, selectors, flags, and settings remain equal to baseline. |
| Launcher assembly replaced | Restore the original assembly from that phase's verified backup to the exact inventoried package path. Restore its original uid:gid, mode, SMACK label and package metadata. Restore the pre-change native-image cache exactly as recorded. Do not remove or recreate neighboring package files. | Re-read the target assembly digest, ownership, mode and label; launch the Samsung app/Home path; verify native Home and settings. |
| Launcher registered or package flags changed | Restore only the changed package_app_info fields for the exact app ID in the reviewed plan. Preserve all unrelated IDs and rows. | Read the selected app row and selectors back; require exact baseline values and complete registry evidence. |
| Home/startup selector changed | Restore the exact pre-change home_appid and startup_appid values recorded from this unit. Do not assume Samsung defaults or borrow values from another television. | Read both selectors back and exercise Home, Back, app return and restart through the ordinary remote. |
| Settings key_filter bridge changed | Restore only the qualified `XF86More` row matching the recorded app ID, selector and arguments. The inverse also requires the `XF86SysMenu` native Settings route to remain exactly equal to its snapshot. | Re-read More and System Menu routes; test the native Settings key and More key, native menu return path and relevant remote keys. |
| Vendor service or cleanup flags changed | Re-enable only the exact service/package entries changed in this phase. The plan must have inventoried the unit definition and original negated conditions. If those facts are missing, this phase has no safe inverse. | Read service state and package flags back; test app launch, native settings, standby/wake and cold boot. |
| App crash or Home unavailable | Use the TV's ordinary physical or already-authorized remote to return to Samsung Home/Settings if responsive. If not, stop further writes and use the exact approved service route or vendor support. Do not rely on the launcher itself as the only recovery control. | Verify Home, full native settings and existing apps work before reattempting any phase. |
| Network remote no longer reconnects | Preserve the TV's existing wired network, remote listener/server, WebSocket/JSON endpoint behavior, discovery and existing pairing credentials from the baseline. Restore only changed app/server configuration from its exact backup. Do not re-pair to mask a regression. | From the existing Wi-Fi client, verify discovery and Home, Back, volume, mute, power, app launch and HDMI/input navigation; reboot/standby and reconnect without pairing. |
| Cold boot, standby or wake failure | Stop selector and cleanup phases. Restore only phase-specific files and settings from verified copies. Keep a physical-control path available while testing. | Repeat cold boot, standby/wake, app return, native menu access and remote-client reconnect against the baseline. |

After any restoration, rerun every affected baseline check and mark the
restored stage in evidence/du7200-acceptance.json only when there is actual,
timestamped, model-and-firmware-bound hardware evidence. Missing or stale
evidence stays NOT_TESTED. Functional checks establish only observable
behavior; latency, picture fidelity, HDR accuracy, audio sync and codec
equivalence require their own appropriate measurements.

## Current state

No install or rollback occurred. The current hardware facts, backup status,
package paths and metadata, Home/startup selectors, network listener and
pairing state, app sessions, HDMI/CEC setup, AV capabilities, and recovery
route remain UNKNOWN or NOT_TESTED. The acceptance checker must remain NO-GO
until all baseline, post-install and restored rows pass on the actual unit.
