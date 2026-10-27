"""Serial C2/C3 assembly; no Reploy subprocess or recording entrypoint."""

from __future__ import annotations

import asyncio
from collections import deque
from copy import deepcopy
import math
from typing import Iterable

from .controller_lifecycle import OperationState
from .controller_session import ControllerSession
from .envoy_client import EnvoyClient, EnvoyClientError
from .envoy_transport import EnvoyTransport


FINAL_DRAIN_SECONDS = 5.0
_RESULTS = {"operation_completed", "operation_finalized", "operation_failed", "operation_cancelled"}
_RESIZE_STATES = {
    OperationState.IDLE,
    OperationState.STARTING,
    OperationState.RUNNING,
    OperationState.GATED,
    OperationState.CONTINUING,
}


class EnvoyController:
    """Compose the selected pair with the caller's existing controller policy.

    One caller serializes request decisions and receive calls. A pending
    terminal-input write may coexist with lifecycle control, as in C3.2.
    Returned telemetry is available before its raw bytes. take_result() gives
    the caller the corresponding completed output barrier. Matching gate
    continuation also waits for that gate's raw output barrier. Local closed means
    only Envoy channel closure; ControllerSession still owns Reploy's result.
    The caller must execute failure_effects through its public Reploy writer.
    """

    def __init__(self, transport: EnvoyTransport, session: ControllerSession) -> None:
        if not transport.ready:
            raise EnvoyClientError("controller requires complete Envoy readiness")
        self.session = session
        self.client = EnvoyClient(transport, session.operation)
        session.lifecycle.envoy_ready()
        self.failure_effects = ()
        self._plan: tuple[dict, ...] = ()
        self._result: dict | None = None
        self._resizes: deque[tuple[int, int]] = deque()
        self._operation_deadline: float | None = None
        self._drain_deadline: float | None = None
        self._final_offset: int | None = None
        self._terminal_eof = False
        self._closed_seen = False
        self._closed = False
        self._disposed = False
        self._gate_barrier: tuple[str, str, int] | None = None
        self._receiving = False
        self._read: asyncio.Task | None = None
        self._cancel_reason: str | None = None
        self._finalize_reason: str | None = None

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def result_available(self) -> bool:
        return (self.session.failure is None and self._result is not None
                and self.client.output_available(self._result["output_through"]))

    def take_result(self) -> dict:
        """Release one terminal result only when its exact raw range is retained."""
        if not self.result_available:
            raise EnvoyClientError("terminal result output is not yet retained")
        result, self._result = self._result, None
        return deepcopy(result)

    async def request(self, kind: str, payload: dict, *, inputs: Iterable[bytes] = (),
                      deadline: float | None = None) -> None:
        """Submit through C3.2; retain only completely admitted request facts."""
        if (self._disposed or self._closed or self._drain_deadline is not None
                or not self.session.can_record):
            raise EnvoyClientError("request after Envoy drain or disposal")
        if deadline is not None and (kind != "execute" or type(deadline) not in {int, float}
                                     or not math.isfinite(deadline)
                                     or deadline <= asyncio.get_running_loop().time()):
            raise EnvoyClientError("invalid plan-owned operation deadline")
        if type(payload) is not dict:
            raise EnvoyClientError("request payload must be an object")
        value = deepcopy(payload)
        if kind == "continue" and self.session.operation.state is OperationState.GATED:
            operation_id = value.get("operation_id")
            gate_id = value.get("gate_id")
            current = self._gate_barrier
            if (current is None or current[:2] != (operation_id, gate_id)
                    or not self.client.output_available(current[2])):
                raise EnvoyClientError("matching gate output barrier is not retained")
        if kind == "resize":
            if self._resizes:
                raise EnvoyClientError("resize acknowledgement is pending")
            if self.session.operation.state not in _RESIZE_STATES:
                raise EnvoyClientError("resize is not allowed in current operation state")
        elif kind == "shutdown" and self._resizes:
            raise EnvoyClientError("resize acknowledgement is pending")
        if kind == "execute":
            if self._result is not None:
                raise EnvoyClientError("previous result output barrier is still owned")
            plan = value.get("inspections")
            if type(plan) is not list or any(type(item) is not dict for item in plan):
                raise EnvoyClientError("invalid authored inspection plan")
            for index, item in enumerate(plan, 1):
                if "inspection_id" in item:
                    raise EnvoyClientError("inspection IDs belong to the controller")
                item["inspection_id"] = f"inspection-{index}"
        try:
            await self.client.request(kind, value, inputs=inputs)
        except BaseException as error:
            if not self.client.transport.ready:
                self.failure_effects += self.session.fail("envoy-session", str(error))
                await self.close()
            raise
        if kind == "execute":
            self._plan = tuple(deepcopy(value["inspections"]))
            self._operation_deadline = deadline
            self._cancel_reason = self._finalize_reason = None
            self._gate_barrier = None
        elif kind == "resize":
            self._resizes.append((value["columns"], value["rows"]))
        elif kind == "cancel":
            self._cancel_reason = value["reason"]
            self._operation_deadline = None
        elif kind == "finalize":
            self._finalize_reason = value["reason"]

    async def write_input(self, operation_id: str) -> None:
        try:
            await self.client.write_input(operation_id)
        except BaseException as error:
            if not self.client.transport.ready:
                self.failure_effects += self.session.fail("envoy-session", str(error))
                await self.close()
            raise

    def _inspection_results(self, event: dict) -> None:
        results = event["inspection_results"]
        if len(results) != len(self._plan):
            raise EnvoyClientError("inspection result count does not match operation plan")
        for request, result in zip(self._plan, results):
            fields = ("inspection_id", "kind")
            if request["kind"] == "produces":
                fields += ("producer_id", "output_id")
            if any(request[key] != result[key] for key in fields):
                raise EnvoyClientError("inspection result does not match operation plan")

    def _event(self, event: dict) -> None:
        operation = self.session.operation
        kind = event["type"]
        if kind == "operation_started":
            operation.started(event["operation_id"])
        elif kind in {"operation_ready", "operation_continued", "operation_gate_interrupted"}:
            method = {"operation_ready": "gate_ready", "operation_continued": "continued",
                      "operation_gate_interrupted": "gate_interrupted"}[kind]
            getattr(operation, method)(event["operation_id"], event["gate_id"])
            if kind == "operation_ready":
                self._gate_barrier = (event["operation_id"], event["gate_id"],
                                      event["output_through"])
            else:
                self._gate_barrier = None
        elif kind in _RESULTS:
            if "inspection_results" in event:
                self._inspection_results(event)
            if kind == "operation_cancelled" and event["reason"] != self._cancel_reason:
                raise EnvoyClientError("cancellation result changed the selected reason")
            if kind == "operation_finalized" and event["reason"] != self._finalize_reason:
                raise EnvoyClientError("finalization result changed the selected reason")
            operation.terminal_result(event["operation_id"], kind.removeprefix("operation_"),
                                      shell_ended=event.get("shell_ended", False))
            self._result = deepcopy(event)
            self._gate_barrier = None
            self._operation_deadline = None
            self._plan = ()
        elif kind == "resize_applied":
            if not self._resizes or self._resizes.popleft() != (event["columns"], event["rows"]):
                raise EnvoyClientError("resize result does not match a pending request")
        elif kind == "draining":
            if self._drain_deadline is not None:
                raise EnvoyClientError("duplicate Envoy drain")
            self._drain_deadline = asyncio.get_running_loop().time() + FINAL_DRAIN_SECONDS
            operation.draining(event["reason"])
            self._resizes.clear()
            self._operation_deadline = None
            if self._terminal_eof:
                operation.terminal_eof()
        elif kind == "closed":
            operation.closed(event["reason"])
            self._closed_seen = True
            self._final_offset = event["output_through"]

    def _finish_close(self) -> None:
        if self._closed_seen and self._terminal_eof:
            if (self.session.operation.state is not OperationState.CLOSED
                    or self.client.transport.raw_offset != self._final_offset):
                raise EnvoyClientError("closed session has an incomplete final raw offset")
            self._closed = True
            self._drain_deadline = None

    def _check_terminal_eof_barrier(self) -> None:
        if (self._terminal_eof
                and self.client.output_through > self.client.transport.raw_offset):
            raise EnvoyClientError("terminal EOF before output barrier offset")

    async def receive(self) -> tuple[str, bytes | dict]:
        """Observe one fact without moving a deadline behind raw-log delivery.

        Keep the one pending pull alive when a plan deadline selects cancel.
        Cancelling that read would itself dispose C3.1's selected pair.
        """
        if self._receiving or self._disposed or self._closed:
            raise EnvoyClientError("receive requires a live serial owner")
        self._receiving = True
        try:
            loop = asyncio.get_running_loop()
            while True:
                now = loop.time()
                if self._drain_deadline is not None and now >= self._drain_deadline:
                    raise TimeoutError("Envoy final drain deadline expired")
                if self._operation_deadline is not None and now >= self._operation_deadline:
                    await self.request("cancel", {"operation_id": self.session.operation.active_id,
                                                   "reason": "deadline"})
                if self._read is None:
                    self._read = asyncio.create_task(self.client.receive())
                limits = [x for x in (self._operation_deadline, self._drain_deadline) if x is not None]
                remaining = max(0, min(limits) - loop.time()) if limits else None
                done, _ = await asyncio.wait({self._read}, timeout=remaining)
                if not done:
                    continue
                if (self._drain_deadline is not None
                        and loop.time() >= self._drain_deadline):
                    raise TimeoutError("Envoy final drain deadline expired")
                if (self._operation_deadline is not None
                        and loop.time() >= self._operation_deadline):
                    await self.request("cancel", {"operation_id": self.session.operation.active_id,
                                                   "reason": "deadline"})
                read, self._read = self._read, None
                kind, value = read.result()
                if kind == "event":
                    self._event(value)
                elif kind == "terminal-eof":
                    self._terminal_eof = True
                    if self._drain_deadline is not None:
                        self.session.operation.terminal_eof()
                elif kind == "telemetry-eof" and not self._closed_seen:
                    raise EnvoyClientError("telemetry EOF before Envoy closed")
                self._check_terminal_eof_barrier()
                self._finish_close()
                if self._closed:
                    await self.close()
                return kind, value
        except BaseException as error:
            self.failure_effects += self.session.fail("envoy-session", str(error))
            await self.close()
            raise
        finally:
            self._receiving = False

    async def close(self) -> None:
        """Join the pending pull and dispose sockets; preserve caller-owned logs."""
        if self._disposed:
            return
        self._disposed = True
        if self._read is not None:
            self._read.cancel()
            await asyncio.gather(self._read, return_exceptions=True)
            self._read = None
        await self.client.transport.close()
