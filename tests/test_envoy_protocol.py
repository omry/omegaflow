from __future__ import annotations

from pathlib import Path

import pytest

from omegaflow.envoy_protocol import (
    Closed,
    Draining,
    EnvoyClientState,
    EnvoyProtocolError,
    EnvoyReady,
    EnvoyStreamDecoder,
    OperationCompleted,
    OperationContinued,
    OperationReady,
    OperationStarted,
    ResizeApplied,
    decode_envoy_event,
)


FIXTURES = Path(__file__).parent / "fixtures" / "envoy-protocol-v1"


def _lines(name: str) -> list[bytes]:
    return (FIXTURES / name).read_bytes().splitlines(keepends=True)


def _idle_client() -> EnvoyClientState:
    client = EnvoyClientState()
    client.hello("session-1")
    client.accept(EnvoyReady(1, 41, 42, "/work", 80, 24))
    return client


def test_decodes_canonical_envoy_events() -> None:
    events = [decode_envoy_event(line.removesuffix(b"\n")) for line in _lines("envoy.jsonl")]

    assert [event.type for event in events] == [
        "ready",
        "operation_started",
        "operation_ready",
        "operation_continued",
        "operation_completed",
        "operation_cancelled",
        "operation_failed",
        "resize_applied",
        "diagnostic",
        "draining",
        "closed",
    ]


def test_encodes_canonical_controller_requests() -> None:
    expected = _lines("controller.jsonl")

    hello = EnvoyClientState().hello("session-1")

    execute_client = _idle_client()
    execute = execute_client.execute("op-1", "printf 'ok\\n'")

    continue_client = _idle_client()
    continue_client.execute("op-1", "true")
    continue_client.accept(OperationStarted(2, "op-1", 0))
    continue_client.accept(OperationReady(3, "op-1", "gate-1", 0))
    continued = continue_client.continue_gate("op-1", "gate-1")

    cancel_client = _idle_client()
    cancel_client.execute("op-1", "sleep 1")
    cancel_client.accept(OperationStarted(2, "op-1", 0))
    cancel_client.next_request_seq = 4
    cancelled = cancel_client.cancel("op-1", "controller-cancelled")

    resize_client = _idle_client()
    resize_client.next_request_seq = 5
    resize = resize_client.resize(100, 30)
    shutdown_client = _idle_client()
    shutdown_client.next_request_seq = 6
    shutdown = shutdown_client.shutdown("capture-complete")

    assert [hello, execute, continued, cancelled, resize, shutdown] == expected


def test_stream_decoder_preserves_fragmented_frames() -> None:
    payload = b"".join(_lines("envoy.jsonl")[:2])
    decoder = EnvoyStreamDecoder()
    events = []
    for byte in payload:
        events.extend(decoder.feed(bytes([byte])))
    decoder.finish()

    assert [event.type for event in events] == ["ready", "operation_started"]


@pytest.mark.parametrize(
    "payload",
    [
        b'{"schema":"omegaflow-envoy-telemetry-v1","type":"ready","type":"ready"}',
        b'{"schema":"omegaflow-envoy-telemetry-v1","type":"ready","seq":1,"envoy_pid":1,"shell_pid":2,"cwd":"/","columns":80,"rows":24,"extra":true}',
        b'{"schema":"omegaflow-envoy-telemetry-v1","type":"ready","seq":true,"envoy_pid":1,"shell_pid":2,"cwd":"/","columns":80,"rows":24}',
    ],
)
def test_rejects_noncanonical_envoy_events(payload: bytes) -> None:
    with pytest.raises(EnvoyProtocolError):
        decode_envoy_event(payload)


def test_stream_decoder_rejects_truncated_telemetry() -> None:
    decoder = EnvoyStreamDecoder()
    assert decoder.feed(_lines("envoy.jsonl")[0][:-1]) == ()
    with pytest.raises(EnvoyProtocolError, match="mid-frame"):
        decoder.finish()


def test_client_state_validates_complete_session() -> None:
    client = EnvoyClientState()
    client.hello("session-1")
    client.accept(EnvoyReady(1, 41, 42, "/work", 80, 24))
    client.execute("op-1", "printf ok")
    client.accept(OperationStarted(2, "op-1", 0))
    client.accept(OperationReady(3, "op-1", "gate-1", 3))
    client.continue_gate("op-1", "gate-1")
    client.accept(OperationContinued(4, "op-1", "gate-1", 3))
    client.accept(OperationCompleted(5, "op-1", 0, "/work", 0, 6))
    client.resize(100, 30)
    client.accept(ResizeApplied(6, 100, 30))
    client.shutdown("capture-complete")
    client.accept(Draining(7, "capture-complete", 6))
    client.accept(Closed(8, "shutdown", 6))
    client.finish()

    assert client.phase == "closed"
    assert client.output_through == 6


def test_client_state_rejects_early_eof_and_sequence_gap() -> None:
    client = _idle_client()
    with pytest.raises(EnvoyProtocolError, match="before closed"):
        client.finish()
    with pytest.raises(EnvoyProtocolError, match="does not match"):
        client.accept(OperationStarted(3, "op-1", 0))
