# B2.8 native qualification

Run from the repository root with the retained Reploy acquisition receipt:

```sh
GOCACHE=/tmp/omegaflow-go-cache python3 tests/envoy-bash-qualification/run.py \
  --acquisition /absolute/path/acquisition.json \
  --output /absolute/path/new-evidence-directory
```

The runner verifies each immutable acquired image and derives an isolated Linux
amd64 test image. It builds the actual Awsh helper and existing Go test binaries,
stages the fixed Bash assets, removes only the declared system rcfile and runs
the strict qualifier twice against that image's genuine `/bin/bash`. It does
not write the supported-build table. `--release` selects one acquired release.

Each repetition retains raw startup bytes, the complete terminal state,
startup export changes, the selected signal inventory, actual Readline bindings,
UTF-8 and maximum source behavior, helper/start/completion results and a mapping
of all 258 native cases to successful assertions. Missing assertions, skipped
Go tests, unexpected diagnostics, changed inputs and nonrepeatable measurements
reject qualification. Negative cases inject faults only in the disposable test
image and restore the bound bytes and modes before input verification.

Review `results.json`, each `entry.json` and the per-repetition assertion logs
before admitting entries to `runtime/envoy/bash-builds.json`. Regenerate consumers
with `tools/bash_builds.py generate` and verify them with `generate --check`.
The manifest-bound runtime table is an output and does not enter its own
qualification inputs; the exact Awsh helper identity remains bound.
Copied table outputs are also excluded from the proof input tree. After paired
qualification, the runner writes its isolated table output, checks the generated
host consumer's exact admission and stale-input rejection, and launches real
Bash through production `Start` using that receipt and manifest-bound table.
Its startup measurement must match the qualified measurement exactly.

These proofs exercise the B2 adapter against deterministic Envoy/setup/cleanup
peers. Public startup and ordinary-return integration belong to B3; real split
setup, descendants and inspection belong to their later owning leaves. Authored
controller-input rejection remains with C3.2/C3.3. Cross-builds and amd64 results
do not qualify arm64 targets.

C4.2 adds `--runtime /absolute/path/runtime` to this same command. This selects
one immutable builder artifact for every target. Only test executables are
compiled. Awsh, Envoy, the rcfile, inputrc, terminal and locale files are copied
from that artifact. The receipt binds every behavioral input. Table and manifest
outputs are excluded to avoid self-reference; Envoy is a separate probe input.
The caller's complete artifact must remain unchanged. Admission writes a table
only inside each disposable image. Review all three entries before replacing
the canonical table; a subset run cannot establish complete package acceptance.
