# Native source qualification checkpoint

**OFFLINE_ONLY. No TV operation or hardware qualification occurred.** These
checks qualify the open-source access payloads and catalog/configuration
logic, plus compilation of the standalone Probe. They do not compile the NUI Home launcher or establish a working root
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

The final review found that standalone Probe's runtime/dependency sidecars
were omitted from its package and that the .NET 6 wrapper did not explicitly
reject a 3.1-only runtime route. The package now includes `Probe.dll`,
`Probe.runtimeconfig.json` and `Probe.deps.json`; A-B-A regression checks
cover all three. The wrapper rejects non-6.0 profile runtimes before contact,
and the vendored probe independently rejects a mismatching observed route
before callback-listener creation or payload staging. New regressions bring
the Python suite to **110 passing tests**.

`dotnet build launcher/probe/Probe.csproj -c Release` with isolated output
and intermediate paths succeeded with zero warnings/errors. Invoking the
resulting DLL with adjacent sidecars on Linux reached managed `Main`, then
exited 1 with `System.DllNotFoundException` for unavailable Samsung libraries.
That expected loader smoke result does not establish native functionality.
The actual target host and private entry points remain untested.

An isolated `net6.0` library project imported the real
`launcher/src/QuietHomeContainer.targets` with synthetic safe app/root/viewer/
settings values. Real MSBuild generation and C# compilation succeeded with
zero warnings/errors, and the generated source contained those five exact
constants. This checks the container-code generation target without Samsung
references. It does not compile Home or QuickLaunch; their full compilation
still requires the target's matching private framework references.

```text
13e781d4e1f13494233e000124102caad8c9a5bb41bc244517f3bdcaea5ad730  Probe.dll
aeb46a15366e38511c192ccf65312442c64e8753b3c16fd2ca0b4cb34a5c59f3  Probe.runtimeconfig.json
db1c85d2cedb925e342a768839caf805dec898e4776b879f0c7136ae645dc9ad  Probe.deps.json
```

GitHub's fork Actions permission readback was `enabled=true`, but its
workflow list and branch run list were empty. Enabling `checks.yml` by the
documented API returned 404; no browser was available for activation. No CI
pass is claimed. The equivalent source checks above ran locally instead.

Target NUI compilation remains unrun because the exact TV's four private
framework references are unavailable. All physical acceptance stages,
including existing Wi-Fi remote pairing/reconnection and native AV/HDMI,
remain NOT_TESTED and production remains NO-GO.
