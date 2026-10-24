# Controller lifecycle proofs

C2.1 implements serialized controller decisions against the deterministic
`FakeClient` in `tests/test_controller_lifecycle.py`. The peer uses the real C1
codec for every public event and request. Attachment socket values are passed
as one opaque argv element. No private Reploy protocol is opened. Tests use an
explicit monotonic clock and no sleeps. Effects authorize the owning adapter's
next action; they do not assert that a process started or that a request won.

The tests cover ordered startup, a single attachment deadline and bootstrap,
recording admission only after the C3 readiness/barrier confirmation, normal
operation and gate transitions, exact active identities, shell-ended and
requested closure, and the distinction between Envoy operation outcomes and
Reploy termination/output finalization. Both attachment EOF and output
finalization precede artifact closure and `complete`, including failed output
finalization. Structured session results remain complete private evidence.

Run `.venv/bin/python -m pytest tests/test_controller_lifecycle.py` and the full
`.venv/bin/python -m nox -s ci` profile. This leaf exposes no executable capture
path. Subprocess launch, draining, cancellation, acknowledgement, bounded stderr
and failure retention are still pending with C2.2's adapter. C2.3 owns the full
fake-client crossed-event matrix and cumulative C2 gate. C3.3 owns actual Envoy
channel and raw-log barrier integration. C8.2 owns actual Reploy invocation.
A successful fake trace does not establish any of those later proofs.

`case-map.json` retains all C2 package requirements and the existing B1 C2 case
IDs with their original owners. Native B tests and approved fixtures remain
unchanged. A failed input permanently closes this core's action-admission path.
