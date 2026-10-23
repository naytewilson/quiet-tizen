# DU7200 source execution contract

The owner's 2026-10-23 brief is the approved scope. The implementation uses
the existing Python standard-library tooling and .NET source, with no Docker
or ANVIL runtime dependency. Only Luna 6 helpers may be used.

1. Repair script delivery with real POSIX-shell regression tests, a per-run
   sentinel, explicit decoder failure handling and verified transfer limits.
2. Add strict exact-identity profiles and separate evidence/capability gates;
   reject contradictory firmware, malformed or duplicate profiles, essential
   cleanup targets and unqualified host paths. Do not invent a DU7200 profile.
3. Separate supported, read-only SDB inventory from the temporary-root route.
   Prepare private machine-readable facts and sanitized discovery/recovery
   checklists without staging payloads, injection, reboot or system writes.
4. Generalize profile-isolated package builds and native launcher inputs,
   paths and capability fallback. Preserve the U8000F source behavior and
   test cross-profile packaging using synthetic outputs. Actual private-reference
   compilation and native runtime remain separate hardware gates.
5. Make launcher/settings/cleanup planning explicit and reversible, validate
   SQL against synthetic schemas, and fail closed when actual-firmware
   qualification or recovery evidence is absent.
6. Run the full offline suite, shell syntax, available .NET checks, privacy
   and diff review. Fix review findings. Commit, publish a branch/PR only in
   the owner's fork, read back the exact remote SHA, and checkpoint ANVIL.

Review focus: decoder zero-exit no-op; identity conflict before root; inventory
profiles accidentally authorizing writes; cross-profile stale files; partial
filesystem/SQL failure preserving the original immutable backup. No offline
test or source commit promotes a physical-device gate to PASS.
