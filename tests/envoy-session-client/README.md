# Python Envoy session client acceptance

C3.1 implements strict Envoy v1 public codecs and a selected terminal/telemetry
connection. The production modules have no recorder entrypoint and do not
launch Reploy, Envoy or Awsh. The socket tests use actual loopback TCP listeners
with deterministic test-only server behavior. They establish transport behavior,
not actual Envoy/Awsh or Reploy integration.

Run `python -m pytest -q tests/test_envoy_codecs.py tests/test_envoy_transport.py`.
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
remain C3.3. Source/input policy, operation submission and post-ready output
frontier matching remain C3.2. Actual Reploy termination remains C8.2. These
pending boundaries cannot be inferred from a local peer pass.
