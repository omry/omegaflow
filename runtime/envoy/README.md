# Runtime build

The production commands are `./cmd/omegaflow-envoy` and `./cmd/awsh`.
`.go-version` pins the exact compiler used by local build validation and CI.
The module's minimum Go version remains in `go.mod`.

From the repository root, build a fresh runtime candidate with:

```text
python tools/build_envoy_runtime.py --output FRESH_OUTPUT --assets QUALIFIED_RUNTIME_ROOT --bash-sha256 QUALIFIED_BASH_DIGEST --arch amd64 --source-revision FULL_SOURCE_REVISION
```

The release caller supplies the exact source revision. The version comes from
`pyproject.toml`. The selected canonical Bash profile must have retained
qualification and match the requested architecture. The asset root contains
the profile's complete `share/terminfo` and `lib/locale` trees. The builder
checks every supplied tree file against that profile, then packages the exact
`xterm-256color` entry, the complete selected locale tree, fixed rcfile and
empty inputrc, the canonical table, and both newly built executables.

Builds use `CGO_ENABLED=0`, `-trimpath`, `-buildvcs=false`, and
`-ldflags='-s -w -buildid='`. The builder rejects a different Go patch version,
source changes during compilation, invalid asset types and missing, additional
or changed asset files. It requires a new output directory, writes the existing
`omegaflow-runtime-manifest-v1` manifest last and makes the result read-only.
It does not overwrite or clean an earlier output.

This is a build candidate. C4.2 must qualify the exact packaged Awsh and trusted
inputs and reconcile the packaged table before full artifact acceptance.
Cross-compiling ARM64 does not establish ARM64 runtime qualification. A complete
ARM64 payload requires independently verified matching target assets.

The Envoy command requires `--session-id`, `--terminal-listen`,
`--telemetry-listen`, `--columns` and `--rows`. Its inspection-worker entry is
dispatched before session setup. The native client matrix in
`tests/envoy-session-client` uses this real command, the actual Python client
and retained qualified Awsh/Bash inputs. Public Reploy invocation remains C8.2.

`B1-PACKAGE-C4.1` maps to
`tests/test_envoy_runtime_build.py::test_independent_source_paths_produce_identical_binaries_and_manifest`
and its asset, identity and source-drift rejection tests. They build both real
commands and verify the output with the existing Go manifest verifier. The
client matrix adds `entry-idle-cancel`, `entry-idle-finalize`,
`entry-selected-eof` and `entry-signal` to prove fatal command exit and actual
Awsh/Bash disposal. `entry-idle-discard` proves idle bytes cannot enter the next
operation. The original 20 client scenarios retain ordinary lifecycle,
inspection-worker and crossed-control coverage. `B1-PACKAGE-C4.2` remains pending
the separate exact packaged-byte qualification gate.

C4.2 qualification measures one immutable builder candidate on all three
retained Linux/amd64 Bash tuples using the existing native qualifier's
`--runtime PATH` option. After admitting all three receipts and regenerating
consumers, rebuild into a fresh output. Both binaries and every behavioral asset
must equal the measured candidate. The packaged table contains qualified entries
only. Verify the final artifact without changing it:

```sh
python tools/build_envoy_runtime.py --verify-qualified /absolute/path/runtime \
  --source-revision FULL_COMMITTED_REVISION
```

This guard checks the manifest, exact inventory, hashes, modes, current version,
platform, pinned toolchain, generated consumers and every advertised receipt's
adapter map. Run the actual client matrix with the same `--runtime PATH` and
complete qualification evidence. Before acceptance, rebuild and verify at the
exact committed revision. A development base revision does not identify WIP.
ARM64 cross-compilation does not establish native qualification. Installed-source
staging and public Reploy launch remain separate acceptance boundaries.
