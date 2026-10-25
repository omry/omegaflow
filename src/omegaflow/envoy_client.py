"""C3 source/input admission and output facts, using the existing C2 policy.

The caller drives the C2 lifecycle from validated observations, matches
inspection results, and owns deadlines and final capture success. This module
starts no actor, implements no Bash parser, and does not wait behind output
barriers before returning telemetry to that lifecycle owner.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Iterable

from .controller_lifecycle import OperationLifecycle, OperationState
from .envoy_codecs import MAX_INTEGER, SCHEMA, encode, validate
from .envoy_transport import EnvoyTransport, MAX_RAW_BYTES


MAX_OUTPUT_MARKS = 1_000_000
_INPUT_STATES = {OperationState.RUNNING, OperationState.GATED, OperationState.CONTINUING}
_PRESTART_FAILURES = {"source-policy", "source-syntax", "input-barrier-timeout"}
_RESULTS = {"operation_completed", "operation_failed", "operation_cancelled", "operation_finalized"}


class EnvoyClientError(ValueError):
    """A source, input plan or observed output fact violates the contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise EnvoyClientError(message)


def _inputs(chunks: Iterable[bytes], written: int) -> tuple[bytes, ...]:
    """Freeze and scan the whole authored plan, including adjacent steps."""
    frozen = tuple(chunks)
    previous = b""
    total = written
    for chunk in frozen:
        _require(type(chunk) is bytes, "authored input must be immutable bytes")
        joined = previous + chunk
        _require(b"\x18\x01" not in joined and b"\x18\x02" not in joined,
                 "reserved terminal input sequence")
        if chunk:
            previous = chunk[-1:]
        total += len(chunk)
        _require(total <= MAX_INTEGER, "terminal input count exceeds wire bound")
    return frozen


class EnvoyClient:
    """One request writer and operation input plan for a ready selected pair.

    Share the caller's existing OperationLifecycle; do not create a second
    policy machine. After receive() returns an event, the caller must apply
    its matching C2 observation before another operation decision. In
    particular, receive() alone does not authorize a new execute or continue.
    Input chunks are already encoded by the recording compiler. Their whole
    immutable plan is admitted before execute; write_input sends its next
    chunk only after the matching public start and C2 input admission.
    """

    def __init__(self, transport: EnvoyTransport, operation: OperationLifecycle) -> None:
        _require(transport.ready, "client requires complete public readiness")
        self.transport = transport
        self.operation = operation
        self._sequence = 2  # C3.1 completely wrote hello at sequence 1.
        self._input_through = 0
        self._input_operation: str | None = None
        self._chunks: tuple[bytes, ...] = ()
        self._input_index = 0
        self._input_writing = False
        self._control_writing = False
        self._output_start: int | None = None
        self._frontier = transport.ready_event["output_through"]
        self._mark_offset = 0
        self._elapsed_us = 0
        self._marks = 0
        self._previous_mark = False

    @property
    def input_through(self) -> int:
        """Only fully successful terminal writes advance the usable count."""
        return self._input_through

    @property
    def output_through(self) -> int:
        """Last validated sender boundary; its bytes can still be in flight."""
        return self._frontier

    def output_available(self, offset: int) -> bool:
        _require(type(offset) is int and 0 <= offset <= self._frontier,
                 "offset is not a validated output frontier")
        return offset <= self.transport.raw_offset

    async def request(self, kind: str, payload: dict, *, inputs: Iterable[bytes] = ()) -> None:
        """Preflight before state changes or any request byte is written.

        schema/type/seq/input_through belong to this writer. Callers supply
        only the fields for the selected request. C2 remains authoritative
        for operation and gate admission, including crossed cancel/finalize.
        """
        _require(self.transport.ready and not self._control_writing,
                 "request requires a ready serial writer")
        _require(type(payload) is dict
                 and not {"schema", "type", "seq", "input_through"}.intersection(payload),
                 "caller supplied a writer-owned field")
        _require(kind in {"execute", "continue", "cancel", "finalize", "resize", "shutdown"},
                 "unsupported controller request")
        _require(kind not in {"execute", "continue"} or not self._input_writing,
                 "input watermark is uncertain while a terminal write is pending")
        frozen = _inputs(inputs, self._input_through)
        _require(kind == "execute" or not frozen, "input plan belongs to execute")
        value = {"schema": SCHEMA, "type": kind, "seq": self._sequence, **deepcopy(payload)}
        if kind in {"execute", "continue"}:
            value["input_through"] = self._input_through
        frame = encode(value)  # Includes source policy and enclosing JSONL bound.
        operation_id = value.get("operation_id")
        if kind == "execute":
            self.operation.execute(operation_id)
            self._input_operation = operation_id
            self._chunks = frozen
            self._input_index = 0
            self._output_start = None
        elif kind == "continue":
            self.operation.continue_operation(operation_id, value["gate_id"])
        elif kind in {"cancel", "finalize"}:
            getattr(self.operation, kind)(operation_id)
        elif kind == "shutdown":
            self.operation.shutdown(value["reason"])
        self._control_writing = True
        try:
            await self.transport.write_control(frame)
            self._sequence += 1
        finally:
            self._control_writing = False

    async def write_input(self, operation_id: str) -> None:
        """Write the next frozen chunk, with no control-frame timeout."""
        _require(self.transport.ready and not self._input_writing,
                 "input requires a ready serial writer")
        _require(self.operation.session.can_record
                 and operation_id == self._input_operation == self.operation.active_id
                 and self._output_start is not None and self.operation.state in _INPUT_STATES,
                 "input requires its matching public operation_started")
        _require(self._input_index < len(self._chunks), "no admitted input chunk remains")
        data = self._chunks[self._input_index]
        self._input_writing = True
        try:
            await self.transport.write_terminal(data)
            self._input_through += len(data)
            self._input_index += 1
        finally:
            self._input_writing = False

    def _observe(self, event: dict) -> None:
        # Transport already validated sequence and retained telemetry bytes.
        # Revalidate shape here so all field use has a single strict contract.
        validate(event, controller=False)
        kind = event["type"]
        _require(kind != "ready", "duplicate public readiness")
        if kind.startswith("operation_"):
            _require(event["operation_id"] == self._input_operation == self.operation.active_id,
                     "operation observation has the wrong identity")
        if kind == "output_mark":
            _require(self._marks < MAX_OUTPUT_MARKS, "session output mark budget exceeded")
            _require(self._frontier <= event["offset"] <= MAX_RAW_BYTES
                     and event["offset"] >= self._mark_offset
                     and event["elapsed_us"] >= self._elapsed_us, "regressing output mark")
            self._marks += 1
            self._mark_offset = event["offset"]
            self._elapsed_us = event["elapsed_us"]
            self._frontier = event["offset"]
        elif "output_through" in event or "output_start" in event:
            offset = event.get("output_through", event.get("output_start"))
            _require(self._previous_mark and self._frontier <= offset <= MAX_RAW_BYTES
                     and offset == self._mark_offset, "output boundary lacks its covering mark")
            if "elapsed_us" in event:
                _require(event["elapsed_us"] >= self._elapsed_us, "regressing boundary time")
                self._elapsed_us = event["elapsed_us"]
            if kind == "operation_started":
                _require(self._output_start is None, "duplicate operation start")
                self._output_start = offset
            elif kind in _RESULTS:
                prestart = (kind == "operation_failed" and event["code"] in _PRESTART_FAILURES
                            or kind == "operation_cancelled" and "status" not in event)
                if self._output_start is None:
                    _require(prestart and event["output_start"] == offset,
                             "terminal result has no opened operation range")
                else:
                    _require(not prestart and event["output_start"] == self._output_start,
                             "terminal result changed the operation range anchor")
                self._input_operation = None
            elif kind == "draining":
                self._input_operation = None
            self._frontier = offset
        self._previous_mark = kind == "output_mark"

    async def receive(self) -> tuple[str, bytes | dict]:
        """Return facts promptly, even if the raw-log frontier has not arrived.

        C3.3 owns matching gate/control results, inspection-plan validation,
        and final-drain timing. EOF stays an observation, not capture success.
        """
        try:
            kind, value = await self.transport.receive()
            if kind == "event":
                self._observe(value)
            return kind, value
        except BaseException:
            await self.transport.close()
            raise
