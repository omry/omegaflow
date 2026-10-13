# B3.1.1 Envoy transport foundation

`runtime/envoy/session` owns the two TCP listeners and accepted channels. Both
listeners bind before the shared ten-second accept epoch. The hello epoch starts
after both accepts; complete strict hello validation starts the independent
launch epoch handed to the startup owner. All public sequence checks use the
existing strict codec. The trusted session ID must match exactly.

Run the actual transport and deterministic startup-peer checks from
`runtime/envoy`:

```sh
GOCACHE=/tmp/omegaflow-go-cache go test -buildvcs=false -race ./session
```

The tests use loopback TCP, full and fragmented public frames, connection EOF
and reset, queued and concurrent unused extra connections, listener refusal after handshake, and
partial bind/accept cleanup. The startup peer writes a complete strict public
ready frame before the exact buffered terminal bytes. It commits input readiness
only after those writes, in one coordinated publication group. Immediate valid
controller responses cannot be classified against a stale pre-publication flag. Tests observe the independent ten-second hello epoch
without replacing production timeouts and prove that fragment progress cannot
extend it. They also retain the original launch deadline for the caller.

Public and private control writes share the existing complete-write loop. Tests
cover positive short writes, zero progress, partial delivery with error,
phase-capped deadlines, cancellation during a blocked write, the actual
five-second individual write budget, independent public sequences, and refusal
to advance a sequence after failed delivery. Allocation is bounded for both
unterminated and coalesced JSON Lines frames.

Unused extras are refused or closed without reading their application data or
failing the selected pair. The actual TCP tests send hostile data on queued
extras, verify disposal and selected pair continuity, and cover 128 closure
iterations per endpoint under the unchanged caller deadline. A second handshake
cannot replace the pair. Selected-channel EOF/reset and forbidden traffic remain
fatal. This implements the approved A2.13 policy; it does not establish the
superseded stronger duplicate-fatal guarantee.

The runtime case mapping is isolated evidence for this owning leaf. The static
corpus remains immutable. B3.2 owns actual PTY/Awsh launch, identity/build checks,
startup-output comparison and teardown. B3.3 owns relay watermarks, its input
hold and private start coordination. B3.4 closes ordinary-return coordination.
This package has no production execution entrypoint or later-slice conformance
claim. Qualification of Bash remains bound to each target's existing evidence;
these transport-only changes do not change the Awsh artifact.
