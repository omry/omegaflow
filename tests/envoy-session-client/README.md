# Python Envoy session client acceptance

C3.1 implements strict Envoy v1 public codecs and a selected terminal/telemetry
connection. C3.2 adds source/input admission, request writing and output-frontier
facts. C3.3 composes those facts with the existing C2 lifecycle. The production
modules have no recorder entrypoint and do not
launch Reploy, Envoy or Awsh. The socket tests use actual loopback TCP listeners
with deterministic test-only server behavior. They establish transport behavior,
not actual Envoy/Awsh or Reploy integration.

Run `python -m pytest -q tests/test_envoy_codecs.py tests/test_envoy_transport.py tests/test_envoy_client.py tests/test_envoy_controller.py`.
Run the existing controller codec/lifecycle/session/crossing suites cumulatively.
The full repository Python check remains `python -m nox -s ci`.

The codec suite reads the frozen B1 controller, Envoy, maximum-size and invalid
public fixtures. It checks canonical nested field order, recursive duplicate
rejection, exact scalar types, integer/UTF-8 byte bounds, source and enclosing
frame bounds, optional presence, inspection wire shapes, arbitrary fragmentation,
coalesced frames, exact sequences and incomplete EOF. Wire inspection validation
does not establish a matching requested inspection or filesystem truth.

The transport uses pull-based nonblocking sockets under standard asyncio
deadlines. It creates no StreamReader that can prefetch terminal bytes before
the bounded readiness reader runs. Address candidates belong to the initial
coordinate resolution and share the original connect deadline;they do not
replace a selected connection or reconnect an established session.

The transport suite proves terminal-first connection and one shared deadline,
bounded async address resolution, canonical hello and its independent write
budget, the ready deadline from completed hello, zero/4096-byte early prefixes,
and the exact raw-log barrier before caller readiness. Early bytes cannot enter
the raw log before valid ready. A raw-log write must complete, flush and fsync
before its offset advances. Raw output requires a fresh separate regular binary
file. File-backed telemetry cannot alias that file,including through a duplicate
descriptor. Unexpected raw-file position or size changes fail before an offset
is acknowledged. Tests cover hidden library buffering,binary terminal text, short and failed writes, EOF, overflow,
duplicate or forbidden frames, cancellation and partial-connection cleanup.
EOF is an observation; it does not establish successful session closure.

Shortened clocks and a smaller raw limit are test-only monkeypatches. They do
not add a production timeout override or change the fixed ten-second connect,
ten-second ready, five-second write or eight-GiB raw-output limits. Tests that
observe private buffering do so only to establish the exact cross-channel phase.

`case-map.json` retains the B1 anchors and owning tests. The shared B1 inventory
is immutable here. C3.3 adds actual local Envoy/Awsh operation and lifecycle
crossings below. Actual Reploy termination remains C8.2 and cannot be inferred
from either the TCP peer tests or the local actor matrix.


C3.2 admits the complete source and immutable encoded input plan before any
execute byte. It uses the existing codec for source and enclosing-frame bounds;
Bash syntax remains Awsh's check. Both reserved input sequences are rejected
within chunks and across adjacent chunks, including empty non-writing steps.
The writer owns sequence numbers and input_through. Only completed terminal
writes advance that count; partial failure or cancellation closes the pair.
Terminal input has no control-frame timer. Execute/continue cannot capture a
watermark while a terminal write is unresolved, while cancel remains writable.
The shared C2 operation object and a matching observed public start gate input.

Frontier tests distinguish retained raw bytes from observed sender offsets.
They reject regressing marks, timestamps, uncovered boundaries, wrong operation
identities, and changed range anchors. Equal-offset marks can change stream.
A terminal result's output_start remains its earlier opening anchor; pre-start
failure/cancellation instead uses a later empty range. Telemetry returns before
waiting for its raw bytes, preserving C3.3's original final-drain timer epoch.
C3.3 still applies matching gate/control/result observations, verifies inspection
plans, drives output barriers, and closes the actual local Envoy/Awsh cases.
C3.2's TCP peers establish no such actual-actor closure or Reploy outcome.

C3.3's `EnvoyController` assigns and freezes inspection IDs, matches result
order, kinds and producer/output identities, and applies matching lifecycle
observations to the caller's existing C2 session. A result becomes available
only after its exact raw range is retained; the next operation waits for result
consumption. The adapter preserves committed results across later controls,
including ordinary completed and planned finalized inspection results when a
typed cancel crosses before Python observes the held original result frame.
Those crossings retain the original inspection IDs, producer/output IDs,
resolved paths, file digest and `directory-v2` digest value, then release the
next operation only after the raw barrier. It admits one wire resize at a time
and matches its acknowledgement, and retains its pending receive across the
plan-owned operation deadline. Its fixed five-second final-drain budget starts
at accepted `draining`, independently of raw delivery, and closure needs
`closed`, the exact final offset and terminal EOF. A terminal EOF that leaves a
validated output frontier above the retained raw offset fails capture promptly
with the existing partial-artifact and termination-request effects; valid
EOF-first shutdown with a complete barrier remains allowed. Local closure
establishes no Reploy result. The caller owns execution of any returned failure
effects.

An `operation_ready` observation remains prompt, but a matching `continue`
request is admitted only after that gate's `output_through` is retained in the
raw log. A rejected preflight writes no control frame, changes no lifecycle
state, and consumes no input watermark. Cancellation, finalization and
deadline-selected cancellation remain available while the gate raw barrier is
pending.

Run `python tests/envoy-session-client/run.py --qualification QUALIFICATION --output FRESH_OUTPUT`.
Use a genuine qualification receipt from `tests/envoy-bash-qualification`; the
runner checks exact qualified Awsh bytes and immutable Bash images. The matrix
uses the production `cmd/omegaflow-envoy` executable with its five
required arguments. The controller observes the child's actual bound listeners
without opening probe connections. The duplicated Go test entrypoint is removed;
the session and restricted inspection workers use the real command.
It runs the original 20 scenarios, five production-entrypoint cases and one
completion-input regression on each
of amd64 Bash 5.2.15, 5.2.37 and 5.3.9. The original cases cover ordinary state,
source rejection, inspections, gated input, cancellation, finalization,
finalized inspections, split output, terminal input, reserved-input admission,
resize, delayed raw output, completed and finalized inspection-result winners
crossed by cancel, crossed cancel/finalize at `operation_ready`, and crossed
cancel/finalize at `operation_continued` and `operation_gate_interrupted`. The
crossed cases hold the original telemetry frame in the opaque TCP proxy, issue
the lifecycle request before Python observes that frame, and then release the
unchanged frame. The two `operation_gate_interrupted` cases use the real
`awsh gate held` source, retain the original interrupted-gate frame and
following frames FIFO until the actual terminal result is committed, then send
the typed cancel/finalize before releasing those unchanged frames. They compare
the observed result with that captured committed result; an ordinary completed
winner, including its actual status, is valid for this late-control ordering.
The raw range remains behind its barrier until the result frame has crossed, and
the next operation is admitted only after result consumption. Inspection-result
crossings hold terminal bytes and the original result independently, issue the
typed cancel after the real result is committed, and verify orderly fixture reap
plus admission of the next operation. `--release` is a development subset and
cannot satisfy the complete matrix.

The completion-input regression sends terminal input during an operation, lets
the idle owner run after its result, and requires a later operation and orderly
shutdown. Input from the completed operation must not enter the later operation.
This normal scenario does not force the socket reader's scheduling order.

The dedicated non-root Go fixture runs production Envoy/Awsh/Bash process,
inspection and cleanup owners. Only Reploy prerequisite observations are
simulated. The TCP proxy delays original bytes or a gate frame without
replacing production transports or wire data. Raw PTY ranges retain terminal
control bytes, including Readline's bracketed-paste enable sequence. Each
scenario requires orderly Envoy closure and actual fixture exit/reap. The
receipt binds runtime and client source hashes/modes and retains raw output,
telemetry, observations, actor logs and scenario results. This matrix establishes
no ARM64 qualification or actual Reploy invocation or termination.
