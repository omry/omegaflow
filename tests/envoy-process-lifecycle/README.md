# B6.5 actual lifecycle process ownership

Run from the repository root after freshly qualifying the current Awsh bytes:

```sh
GOCACHE=/tmp/omegaflow-go-cache python3 tests/envoy-process-lifecycle/run.py \
  --qualification /absolute/path/fresh-three-build-qualification \
  --output /absolute/path/new-evidence-directory
```

The runner validates three genuine Linux amd64 Bash receipts and immutable
images, the exact manifested Awsh executable and fixed startup assets. Each
non-root, network-isolated case launches actual Envoy session/relay/coordinator,
Awsh and Bash. A dedicated Envoy child enables subreaping before launch and
owns the concrete pidfd census before authored work. An outside sibling remains
outside that controlled tree.

The matrix covers real foreground jobs, cancellation and finalization, escaped
writers, split dual EOF, helper-window ignored SIGINT, observed-return winners,
post-result crossed controls followed by another operation, actual five-second
lifecycle timeouts, shell_exit/EOF/Awsh reap, a natural shell exit and a fast
active shell exit. It also
contains an actual sample-to-ioctl foreground-group switch performed by an
authored actor inside the Awsh/Bash terminal session and three positive
stale-PGID cases: a return winner with no signal, a later live eligible group
receiving one signal, and the original grace selecting timeout. Each stale case
proves the old positive group has no live census members before choosing its
winner. Every reusable result requires an empty final operation census and Bash
foreground reclaim.
Every shell-ended result requires an empty controlled tree and actual PTY EOF.

Explicit Go test overlays observe existing actions. A bounded channel is
notified after the actual signal ioctl, every real process census sample and
returned member count is observed, and authored actors running inside the
controlled terminal session perform the real foreground-group changes. Another
hook can pause the existing cleanup callback after input_close acceptance. They
select reproducible race boundaries without substituting census membership,
cleanup, signal success, private frames, grace deadlines or owner decisions.
The runner retains original and overlay hashes. These overlays are test
instrumentation; no production test override or hook is added.

Four negative overlays remove descendant cleanup, replace the selected-shell
SIGKILL with a pidfd signal-zero probe, omit the authored sample-to-ioctl
switch, or omit the returned empty-membership observation. The documented
runner must detect each corresponding missing boundary. Retain each mutation,
build command, native image, raw log and expected failure separately. Source
files remain unchanged.

`--release`, `--case` and `--no-negative` are development subsets. They do not
satisfy the complete matrix and counterproof gate. The native binaries use
CGO_ENABLED=0 and are not Go-race-instrumented. Separate Go race suites cover
host-side tests. ARM64 cross-compilation is not native qualification.

B6.6 owns Python interactive assertion rejection. B7 owns inspection. B8 retains
complete terminal/error hardening and the full actual-actor conformance gate.
No peer-only or compiler-only evidence satisfies that final gate.
