# Restricted inspection worker (B7.2)

This leaf runs the existing B7.1 filesystem algorithms in a short-lived mode of
the same Envoy executable. It adds the isolated worker module and test assembly.
The session actor is not connected to it until B7.3.

`inspection.StartWorker` accepts a complete resolved plan. It validates and
copies the plan before process creation. The child receives an empty environment,
fixed cwd, a private request pipe and one private result pipe. Its stdout and
stderr use `/dev/null`. Broken parent CLOEXEC protection rejects launch rather
than changing arbitrary descriptors. The parent performs no workload filesystem
lookup or hashing.

`RunWorkerMode` accepts only the fixed `inspection-worker` argument and its
private pipe directions. It invokes the existing algorithms. Its bounded
internal reply is either an existing result array or one typed inspection
failure. The parent checks encoding, exact result shapes, duplicate fields,
scalar bounds and plan correlation. These pipe bytes are not session events.
No public or Awsh-private wire model changes.

Ten actual child-entry rejection cases cover environment/cwd drift, missing or
non-pipe descriptors, wrong pipe direction, aliased input/result pipes and
invalid stdio. Validly encoded requests with a different internal identity,
status or cwd also reject without results.

`Worker.Done` closes only after result transport and actual process wait join.
`Result` then returns an independent slice or an error without partial results.
`Kill` targets only the retained child identity. `Reap(ctx)` proves disposal;
it does not prove an inspection result is valid. A caller-bound timeout never
claims disposal. This module adds no timer or automatic cancellation policy.

Run the focused actual-process and codec checks from the repository root:

```sh
CHGDISABLE=1 GOCACHE=/tmp/omegaflow-go-cache go -C runtime/envoy test -race -buildvcs=false -count=1 -timeout=90s ./inspection -run '^TestWorker'
```

Run the committed malformed-worker and missing-check proofs:

```sh
python3 tests/envoy-inspection-worker/run.py --output /tmp/omegaflow-worker-proof-new
```

The output path must not already exist. The runner builds the actual test
executable. Parent and child use that same executable, including in renamed
test-only fault fixtures. Production exposes no executable override, injected
failure option or environment-controlled test mode. The fixture entry checks
that private parent pipe/socket/terminal descriptors and parent environment did
not cross into the child before calling the real worker mode.

Seven live fixtures cover malformed and trailing JSON, a streaming oversized
reply without EOF, mismatched identity, nonzero exit after a valid reply, a
valid reply/EOF before delayed exit, and a blocked child. Six compiled negative
controls omit environment isolation, descriptor protection, ID correlation,
duplicate rejection, reap joining and the streaming bound. A detected control
must compile and fail its named assertion; timeout or compilation failure does
not count as proof. Each owned fixture is terminated and reaped during cleanup.

Outer Go coverage measures parent execution. It does not account for restricted
child execution under an empty environment. The proof runner uses binaries
without coverage instrumentation and checks that every removal control differs
from the baseline. Actual child behavior is established by process tests and
detected controls, not inferred from a coverage percentage.

The B1 package case maps here. Full session channel-isolation and worker-result
versus cancellation cases remain owned by B7.3. Its existing five-second fatal
cancellation-timeout behavior and all B8 actual-actor conformance remain pending.
Linux ARM64 evidence is compile-only until genuine target execution is available.
