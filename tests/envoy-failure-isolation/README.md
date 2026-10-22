# B8.1 descriptor isolation and failed-channel disposal

Run the actual local actor matrix with retained qualification inputs:

```sh
python3 tests/envoy-failure-isolation/run.py \
  --qualification /absolute/qualification-directory --output /new/evidence-directory
```

The runner verifies the three genuine Linux amd64 qualification entries and
unchanged Awsh/startup assets. It stages the session owner's test executable
with real Awsh and Bash. A dedicated Envoy process owns the actual subreaper,
process census, relay, private decoder and disposal. There is no simulated Awsh
or successful cleanup callback in these native failure cases.

Two ordinary exec observers run through direct and nested Bash paths. Their
literal procfs descriptor targets must differ from Envoy sockets and private
Awsh pipes. Awsh receives `/dev/null` on its bootstrap streams and must not
inherit an Envoy socket. The Envoy channels retain close-on-exec. This proves
inheritance isolation. It does not protect against deliberate interference by
another process with the same identity.

Ten fault cases inject malformed public input, a partial frame followed by
EOF, terminal EOF, telemetry EOF/reset, malformed private input, a valid unknown
private error code, loss of either private pipe, and malformed public input
during an actual split-stream operation. Private fault injection
opens the actual Awsh result descriptor from the trusted test process; it
neither replaces Awsh nor claims that authored workload code cannot interfere.
Every case runs an actual escaped descendant. The operation owner must return
failure before disposal. No operation terminal result or successful `closed`
may follow that failure. Original channel EOF/error causes remain observable
through cancellation of joined I/O.

The failed-session disposal method accepts only the matching process owner and
a caller-supplied deadline after the active operation joins. It reuses the
existing relay/private joins, process cleanup, selected-shell lifetime pin,
Awsh wait and final empty census. It preserves the original failure and any
additional disposal error. Killing a process does not prove reap. Repeated
successful disposal retains the same failure, and the failed session cannot
start another operation.

`--release` and `--case` select partial development proofs. They do not satisfy
the complete three-target matrix. The complete runner also requires four
source-omission controls to fail with their specific invariant diagnostics:
an Envoy socket added to the Awsh handoff, omitted escaped-descendant cleanup,
omitted original channel cause retention, and omitted split resource release.
The overlays never modify the
owning source files. `--native-only` omits those controls; `--proof-only` runs
only the controls. Both are partial proofs. ARM64 compilation does not establish support.
B8.2 owns terminal-result ordering, B8.3 owns exhaustive deadline crossings and
B8.4 owns the full actual-actor B1–B8 conformance gate. This test assembly adds
no production controller/runtime entrypoint, wire form, policy or timer.
