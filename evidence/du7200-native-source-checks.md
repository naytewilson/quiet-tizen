# Native source qualification checkpoint

**OFFLINE_ONLY. No TV operation or hardware qualification occurred.** These
checks qualify the open-source access payloads and catalog/configuration
logic. They do not compile the NUI Home launcher or establish a working root
route, private API, ABI, host container, HDMI mapping or runtime on a DU7200.

The first native run used commit
`bc6fea01af4c8e52cf4d146ba53d98ba444c4cbb` on
`codex/quiet-tizen-un65du7200b-compat` in `naytewilson/quiet-tizen`.
Publication evidence changes do not change the reviewed product source.
Final exact-head reruns are bound in the PR and private final receipt.

The development host was Linux x86_64. No system SDK or Python dependency
was installed. Microsoft .NET SDK **6.0.428** was extracted only under the
checkout's ignored `.local/dotnet-sdk`; CLI home, package cache, intermediate
files and build outputs were also confined to `.local/`. Telemetry and
workload update notifications were disabled. Build servers were shut down
after the checks; the temporary toolchain is removed at handoff.

The 188,484,831-byte SDK archive came from Microsoft's release metadata at
`https://builds.dotnet.microsoft.com/dotnet/release-metadata/6.0/releases.json`.
Its SHA-512 matched the published linux-x64 6.0.428 archive hash:

```text
04395f991ab50e4755ce1ae53e23592a7420b71b82160883bae3194dd1dfd5dcaed78743e4e0b4dd51ea43c49ec84b5643630707b3854f1471265dc98490d2f9
```

With that SDK on PATH, `DOTNET_ROOT=.local/dotnet-sdk`,
`DOTNET_CLI_HOME=.local/dotnet-home`, and `NUGET_PACKAGES=.local/nuget`
(resolved to absolute checkout paths), the checks were:

| Command / check | Actual result | Scope |
|---|---|---|
| `python3 tools/build.py payloads` | Exit 0; SamsungTvArchiveRoot and SamsungTvRootAgent both built with 0 warnings and 0 errors | Native compilation; no execution against a TV |
| `dotnet build tests/CatalogTests.csproj -p:BaseIntermediateOutputPath=<checkout>/.local/obj/catalog/ -p:BaseOutputPath=<checkout>/.local/build/catalog/ -p:UseSharedCompilation=false` | Compiled successfully as part of the initial `dotnet run` invocation | No Samsung reference assembly required |
| `dotnet .local/build/catalog/Debug/net6.0/CatalogTests.dll launcher/assets/apps.json` | Exit 0: `Catalog, launcher config and navigation checks passed.` | Catalog filtering/validation, input/configuration validation, setting fallback maps and navigation bounds |
| `dotnet build-server shutdown` | MSBuild and compiler server shutdown succeeded | No permanent build service required |

The initial `dotnet run` invocation built the isolated output successfully
but then looked for the executable under the default `tests/bin` path and
exited 129. Executing the actual isolated DLL passed. This was a command
output-path mismatch, not a catalog failure; the original log is retained.

Initial artifact SHA-256 values:

```text
d29ccb9b7b71573a6782ddbcd5badf42c53de1a9ed3d00ae0f78c08be78ecb1d  SamsungTvArchiveRoot.dll
f3175502c90cdfdcffee0d78eca56de1d9e5f66185e9a5362be87c6b9b3e3fde  SamsungTvRootAgent.dll
9832160a1cbfbd358a1e7a4dfd546722ae54d86420b9059a2baf96054a6c167a  CatalogTests.dll
```

GitHub's fork Actions permission readback was `enabled=true`, but its
workflow list and branch run list were empty. Enabling `checks.yml` by the
documented API returned 404; no browser was available for activation. No CI
pass is claimed. The equivalent source checks above ran locally instead.

Target NUI compilation remains unrun because the exact TV's four private
framework references are unavailable. All physical acceptance stages,
including existing Wi-Fi remote pairing/reconnection and native AV/HDMI,
remain NOT_TESTED and production remains NO-GO.
