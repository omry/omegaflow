# B2.3.1: one-exec handoff and selected-shell launch

This is isolated launch evidence for the reviewed B2.3.1 child of the original
B2.3 requirement. B2.3.2 owns startup helpers, fixed adapter assets, Readline
readiness, terminal leases/drain, private ready and cumulative B2.3 acceptance.
The original `B1-PACKAGE-B2.3` case remains pending until that successor closes.
No supported Bash build is admitted and no production supervise entrypoint is
available. Test-only subprocess modes are compiled into Go test binaries only.

Run from the repository root with Go and Docker available:

```
GOCACHE=/tmp/omegaflow-go-cache .venv/bin/python tests/envoy-bash-launch/run.py \
  --acquisition /absolute/path/verified-candidate-acquisition.json \
  --output /absolute/path/new-proof-directory
```

The acquisition document supplies the verified candidate image reference,
immutable image ID, platform and `/bin/bash` digest. Before creating the fresh
output tree or invoking Go, the runner validates the candidate shape,
linux/amd64 platform, SHA-256 fields and Docker-tag-safe release value, then
verifies each retained image ID with Docker. Every release-derived artifact path
is resolved and checked to remain inside that output tree. The test process
verifies the actual executable digest. Tests use the exact fixed Bash argv. The
declared `/etc/bash.bashrc` is removed solely in a derived isolated test image;
the source candidate and host are untouched.
The image build uses a process-scoped legacy-builder setting so Docker can
consume the local immutable ID; it does not change daemon or global Docker
configuration.
The inert rcfile reports topology and invokes a test observer, then exits.
It emits no private readiness or adapter helper frames. Python is the already
resolved OS provider dependency in these Reploy acquisition images.

Each candidate runs every case three times: ordinary launch, wrong descriptor
direction, non-PTY slave, non-null or closed stdio, invalid session leadership, cancelled caller context, raw child
setup duplication failure, failed setup report write, release EOF and malformed
release. Ordinary launch checks direct parent/session, distinct shell process
group, foreground ownership before the first rcfile command, parent mask
preservation and child mask reset, reset inherited ignores, only descriptors
0/1/2 reaching the executed observer, intake CLOEXEC, successful reap and
idempotent disposal. Failure cases require rejection or raw child exit 127,
with no authored rcfile execution. The signal inventory comes from the exact
candidate's `trap -l`; it is test input, not qualification or advertised support.

Recorded local run: 2026-10-08, Reploy PR225 approved head
`cab280ed8289dc25973121abb6677168c754f60a`, Linux amd64. All 99 isolated scenarios
passed across these three executable identities:

| Release | Resolved executable | SHA-256 |
| --- | --- | --- |
| 5.2.15 | /usr/bin/bash | 55b89ab22bee4792a210f493a53fb066accd5d30b69837c28d98be5ff863efcf |
| 5.2.37 | /usr/bin/bash | 223fe8564b60636bc738b6178d3ba9a50ef7d791266b0efae6363bb716e4c47f |
| 5.3.9 | /usr/bin/bash | 3efccc187bafa75ff1e37d246270ab3e7aa559f242c7a52bf3ec2a1b5450bdbd |

Full logs and immutable acquisition/lock provenance are retained under
the delivery evidence root; the current complete-validation receipt identifies
the retained candidate proof. Earlier failed diagnostic runs remain there.
Repository CI passes (1061 passed); the Go race suite and vet pass; both Linux architectures cross-build with
`CGO_ENABLED=0 go build -buildvcs=false ./...`. These builds provide no genuine
arm64 runtime evidence. Full candidate qualification remains B2.8, and real
Envoy coordination remains B3–B8.

Go coverage from the actual subprocesses reaches every launch Go function
(86.1% of statements); remaining uncovered statements are syscall-error
branches, not additional accepted behaviors. Four isolated amd64 assembly
mutations (signal reset, signal mask, descriptor isolation and process-group
setup) each cause the focused normal-launch tests to fail. Exact mutated
source hashes and logs remain in `B2.3.1-native-mutations` under the evidence
root. No mutant is applied to the reviewed checkout.
