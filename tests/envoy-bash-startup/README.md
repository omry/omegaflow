# B2.3.2/B2.3.3 — Startup readiness and exact terminal restoration

This isolated runner exercises the actual local Awsh startup module and socket
helper against verified Reploy Bash candidates. It follows the approved PR48
selected-child launch/reaper, then validates the two startup helper phases,
complete echo-off pre-Readline termios readback, Readline entry and terminal topology.
The complete workload reference remains separate from the active Readline state.
The isolated supervisor invokes the restoration primitive after readiness and
compares every field with that reference. Normal and initially raw/no-echo
starts retain their selected flags and control characters. This invocation tests
the primitive; B2.6 still owns its actual start-helper integration and ordering.
Private `ready` follows inherited-slave closure and a fresh descriptor-free
terminal-control lease and drain. Helpers emit no terminal bytes; the rcfile's
visible primary prompt is empty.

Run from the repository root with the retained acquisition file:

```sh
PYTHONOPTIMIZE=1 .venv/bin/python tests/envoy-bash-startup/run.py \
  --acquisition /path/to/B2.3-candidate-acquisition.json \
  --output /path/to/new-startup-proof
```

The output directory must be new. Docker and Go are required. The runner reuses
PR48's acquisition validation, exact source-image checks and selected-build
signal inventory. It builds the current Awsh binary and a test-only supervisor,
copies the tracked startup assets, and stages each candidate's terminal and
locale files at the trusted paths in a derived image. Only that derived image
removes its declared system Bash rcfile. Source images, host global paths and
the live Reploy checkout remain untouched. Runs use exact derived image IDs,
no network, hostile application PATH/HOME and fixed terminal/locale inputs.

Retain `inputs.json`, image IDs, build/test logs and `results.json`. The input
record hashes the acquisition, local binary/rcfile/inputrc, runner and shared
input rules. Each failure variant records its read-only rcfile hash. The
source image and derived image identify the measured trusted asset tree;
these test artifacts are not a production runtime manifest.

Each candidate runs three nominal starts and three repetitions per fault:
early selected-shell exit, ready before state, duplicate state, missing state,
missing ready, helper fail-stop, context cancellation, a wrong socket peer,
unexpected startup release signal, a preexisting helper directory,
a blocked private-result writer, and release-signal/shell-exit crossings while
private readiness is blocked. The latter must interrupt before the launch deadline
and publish no readiness bytes. Failure assertions require no private
ready, nonzero supervisor outcome, selected-shell reap, no live launch member
before supervisor exit, and removal of the owned helper directory. A preexisting directory and its
owner marker remain untouched; the test removes its own fixture afterward.
Nominal runs also prove that a rejected terminal lease closes its descriptor. A pending
helper zombie is reap evidence for the future Envoy/subreaper owner, not a live
helper or an Awsh operation-cleanup claim. The blocked writer retains exactly
its deliberately prefilled bytes and publishes no readiness frame.

Each candidate also runs three starts with raw/no-echo terminal input and altered
control characters. The retained logs include the complete workload, active
Readline and restored states. Restoration rejects a missing phase deadline and
cancelled context without publishing helper success. The separate real-PTY lease
suite checks wrong session/foreground, kernel-denied TCSETS or TCGETS, normalized
readback mismatch, expired/cancelled context, cancellation before a lease returns,
and descriptor closure/non-inheritance. Kernel faults use seccomp only in isolated
test processes; no production injection or alternate terminal backend exists.

The test-only supervisor supplies a deadline and candidate signals. Production
`Start` requires an exact qualified generated-table entry and the caller's
existing absolute launch deadline; the current table admits none. The CLI adds
only the exact startup helper arities alongside fail-stop, with no supervise
entrypoint or candidate configuration switch. Context closure interrupts
blocked helper and result I/O without extending the launch epoch. Failed
startup freezes the selected shell before helper disposal, preventing a new
fail-stop helper from being spawned during interruption. It signals only
validated startup-helper lifetime identities and uses PR48's selected-child
reap owner. Envoy retains the launch deadline and takeover responsibility if a
kernel process cannot reach the cleanup boundary.

These are isolated local proofs, not a supported-build or complete-protocol
claim. B2.4 owns canonical reserved state and mediation; B2.5 source submission;
B2.6 start handshakes; B2.7 completion/persistent state; B2.8 full qualification
and generated supported entries. B3.2 supplies the actual Envoy startup-output
comparison and public-ready barrier. B6 owns actual operation descendants and
adopted-child cleanup; B8 closes cumulative fatal/terminal variants. Linux
arm64 cross-build success is not genuine target qualification.
