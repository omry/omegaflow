# Python Envoy session client acceptance

C3.1 implements strict Envoy v1 public codecs and a selected terminal/telemetry
connection. C3.2 adds source/input admission, request writing and output-frontier facts. The production modules have no recorder entrypoint and do not
launch Reploy, Envoy or Awsh. The socket tests use actual loopback TCP listeners
with deterministic test-only server behavior. They establish transport behavior,
not actual Envoy/Awsh or Reploy integration.

Run `python -m pytest -q tests/test_envoy_codecs.py tests/test_envoy_transport.py tests/test_envoy_client.py`.
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
is immutable here. Actual local Envoy/Awsh operation and lifecycle crossings
remain C3.3. Actual Reploy termination remains C8.2. These
pending boundaries cannot be inferred from a local peer pass.


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
