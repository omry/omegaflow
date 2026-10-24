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
`.venv/bin/python -m nox -s ci` profile. These leaves expose no executable capture
path. C2.2 adds the concrete `ControllerSession` evidence adapter against a
deterministic peer in `tests/test_controller_session.py`. C2.3 owns the full
fake-client crossed-event matrix and cumulative C2 gate. C3.3 owns actual Envoy
channel and raw-log barrier integration. C8.2 owns actual Reploy invocation.
A successful fake trace does not establish any of those later proofs.

`case-map.json` retains all C2 package requirements and the existing B1 C2 case
IDs with their original owners. Native B tests and approved fixtures remain
unchanged. A failed input permanently closes this core's action-admission path.

The C2.2 peer uses the same public codecs and executes returned effects. Tests
verify distinct Envoy cancellation and public Reploy termination, exact operation
identity, no action after recording fails, and first-failure context retention.
Public termination continues after a local capture failure. An early attachment
end remains an observed fact until the public termination boundary; a failed
launch reports that no attachment reader exists. Neither invents a Reploy event.
A clean attachment exit alone closes new recording admission and waits for
public lifecycle evidence. It does not fail capture because its observation
preceded `terminating`. A nonzero attachment exit remains a capture failure.

Normal and failed output finalization wait for attachment end and artifact
closure before `complete`. A startup-failure result does not require `complete`.
Every valid diagnostic, client-error and terminated event is written and flushed
to a caller-owned private event sink before interpretation or acknowledgement.
The adapter emits one acknowledgement, then requires JSONL EOF and the reaped
client's zero exit status to observe clean broker closure. That closure never
establishes successful capture or authoritative host success. A broken request
writer suppresses further requests while available result evidence is retained.

Client stderr is streamed to a separate caller-owned sink, preserving arbitrary
bytes. Only its in-memory tail is bounded by an explicit caller-supplied limit;
the tail does not replace the complete retained stream. Short writes are handled,
and write/flush failures remain capture failures with partial evidence retained.
These tests use actual temporary files for streaming retention and deterministic
failure sinks. Process execution, actual channel pumps and host invocation are
not established by this peer and retain their C3/C8 owners.
