# DU7200 port source preflight — 2026-10-23

Status: **OFFLINE_ONLY**. No current DU7200 hardware result is asserted.

The dedicated checkout starts at upstream `cf518d7a9278dca7990e27756218258e44044a29`,
on branch `codex/quiet-tizen-un65du7200b-compat`. It was cloned clean for this
task; no existing checkout, worktree, or device is owned by this branch.
Upstream main has no changes since the supplied SHA.

| Candidate | Current head | Relationship / review scope |
|---|---|---|
| PR 1 | `143949f22dc4968f013addbfc95de9c40f28f9ff` | Independent; script decoding fix needs stronger execution and failure checks |
| PR 2 | `c150858eaacc9cdb67655c5e40bee37d62f1d249` | Independent; firmware capture uses the injection transport and override wins; neither behavior is suitable for read-only doctor |
| PR 3 | `427070a4808d03be494f7542686e5c076791fb40` | Exact identity matching and host metadata; insufficient schema/capability validation |
| PR 4 | `2017943141f7a0a11aecf7a616356a18124299cb` | Includes all PR 3 commits; separately depends on PR 1; settings bridge remains specific |

GitHub check-runs API returned zero check runs for all four heads. This is
not a passing CI result. Candidate changes will be independently implemented
and attributed, rather than blindly stacked or merged.

Baseline: `python3 -m unittest discover -s tests -v` ran **19 tests, all passed**
on pristine upstream. Those tests did not exercise the broken script pipeline.
No dotnet SDK or Samsung SDB was found on Dell PATH. Neo was independently
identified as Darwin arm64; initial standard-path checks also found no SDB
or dotnet SDK. More targeted installed-tool checks remain eligible.

Governing sources read: AGENTS.md, README.md, CONTRIBUTING.md, docs/01-inventory.md
through docs/09-recovery.md, and current PR diffs/commit graph.

The current instruction authorizes source work and read-only inspection.
Persistent device writes require a separate explicit deployment decision,
actual-firmware backups and a rehearsed inverse. Proprietary references,
device facts, raw output, snapshots and identifiers belong only in ignored
`.local/`. No exact DU7200 profile can be published until current firmware is
observed. Historical firmware is not an executable compatibility profile.

Initial risks: false script success; injection hidden behind doctor; firmware
override hiding disagreement; cross-model profile confusion; unsafe cleanup
defaults; assumed native host/labels/UID; stale cross-profile build outputs;
incomplete multi-file rollback; private API incompatibility; loss of Home or
settings recovery. Hardware-dependent gates remain UNKNOWN.

Final source recheck: upstream main remains `cf518d7a9278dca7990e27756218258e44044a29`
and PRs 1–4 retain the heads above. A new PR 5 appeared during this work:
`c2d998d9932b557db5350d0b3f00c95394e8a64d`, a separate Frame QN43LS03DAFXZA
profile with contributor-reported firmware/runtime/access, another native host
and cleanup findings. Its only source change is that model's profile. It was
inspected and not imported: its `launcher_host` schema and model-specific
observations are not DU7200 facts or cleanup authority. The contributor's
timing-probe caveat is not evidence that the root route works on this TV.
