from __future__ import annotations

import json
from pathlib import Path

import pytest

from omegaflow.envoy_protocol import (
    AwshCancel,
    AwshClosed,
    AwshCompleted,
    AwshContinue,
    AwshExecute,
    AwshGateContinued,
    AwshGateReady,
    AwshProtocolError,
    AwshReady,
    AwshShutdown,
    AwshStarted,
    AwshStreamDecoder,
    Cancel,
    Closed,
    Continue,
    Diagnostic,
    Draining,
    EnvoyProtocolError,
    Execute,
    Hello,
    MAX_OPERATION_SOURCE_BYTES,
    MAX_TELEMETRY_FRAME_BYTES,
    OperationCancelled,
    OperationCompleted,
    OperationContinued,
    OperationFailed,
    OperationReady,
    OperationStarted,
    PresentationUtf8Decoder,
    Ready,
    Resize,
    ResizeApplied,
    SessionProtocolState,
    Shutdown,
    TelemetryStreamDecoder,
    decode_awsh_request,
    decode_awsh_result,
    decode_controller_frame,
    decode_envoy_frame,
    encode_awsh_request,
    encode_awsh_result,
    encode_controller_frame,
    encode_envoy_frame,
    sanitized_bash_environment,
)


FIXTURES = Path(__file__).parent / "fixtures" / "envoy-protocol-v1"


def fixture_lines(name: str) -> list[bytes]:
    return (FIXTURES / name).read_bytes().splitlines(keepends=True)


def assert_error(code: str, action) -> None:
    with pytest.raises(EnvoyProtocolError) as exc_info:
        action()
    assert exc_info.value.code == code


def test_controller_golden_frames_round_trip_exactly() -> None:
    for frame in fixture_lines("controller.jsonl"):
        assert encode_controller_frame(decode_controller_frame(frame)) == frame


def test_envoy_golden_frames_round_trip_exactly() -> None:
    for frame in fixture_lines("envoy.jsonl"):
        assert encode_envoy_frame(decode_envoy_frame(frame)) == frame


def test_jsonl_decoder_accepts_arbitrary_fragmentation() -> None:
    payload = b"".join(fixture_lines("controller.jsonl"))
    decoder = TelemetryStreamDecoder("controller")
    messages = []
    for byte in payload:
        messages.extend(decoder.feed(bytes([byte])))
    decoder.finish()
    assert [type(message) for message in messages] == [
        Hello,
        Execute,
        Continue,
        Cancel,
        Resize,
        Shutdown,
    ]


@pytest.mark.parametrize(
    ("frame", "code"),
    [
        (b"{}", "invalid-framing"),
        (b"{}\r\n", "invalid-framing"),
        (b"\xff\n", "invalid-utf8"),
        (b"{broken}\n", "invalid-json"),
        (
            b'{"schema":"omegaflow-envoy-telemetry-v1","type":"hello",'
            b'"seq":1,"seq":2,"session_id":"s"}\n',
            "duplicate-field",
        ),
        (
            b'{"schema":"omegaflow-envoy-telemetry-v1","type":"hello",'
            b'"seq":1,"session_id":"s","extra":true}\n',
            "unknown-field",
        ),
        (
            b'{"schema":"omegaflow-envoy-telemetry-v1","type":"hello",'
            b'"seq":1}\n',
            "missing-field",
        ),
        (
            b'{"schema":"omegaflow-envoy-telemetry-v1","type":"hello",'
            b'"seq":true,"session_id":"s"}\n',
            "invalid-field",
        ),
    ],
)
def test_controller_decoder_fails_closed(frame: bytes, code: str) -> None:
    assert_error(code, lambda: decode_controller_frame(frame))


def test_oversized_message_and_unterminated_stream_fail_closed() -> None:
    assert_error(
        "invalid-field",
        lambda: encode_controller_frame(
            Execute(seq=1, operation_id="op", source="x" * (MAX_OPERATION_SOURCE_BYTES + 1))
        ),
    )


def test_encoders_reject_non_utf8_unicode() -> None:
    assert_error(
        "invalid-field",
        lambda: encode_controller_frame(Execute(1, "op", "\ud800")),
    )
    assert_error(
        "invalid-field",
        lambda: encode_awsh_request(AwshExecute("op", "\ud800")),
    )
    decoder = TelemetryStreamDecoder("controller")
    assert_error(
        "frame-too-large",
        lambda: decoder.feed(b"x" * MAX_TELEMETRY_FRAME_BYTES),
    )


def test_telemetry_early_close_is_distinct_from_clean_boundary() -> None:
    decoder = TelemetryStreamDecoder("envoy")
    decoder.feed(b'{"schema":"omegaflow')
    assert_error("early-close", decoder.finish)


def test_stream_decoders_reject_unknown_directions() -> None:
    with pytest.raises(ValueError, match="unsupported telemetry direction"):
        TelemetryStreamDecoder("sideways")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unsupported awsh direction"):
        AwshStreamDecoder("sideways")  # type: ignore[arg-type]


def test_awsh_golden_frames_round_trip_and_fragment() -> None:
    entries = json.loads((FIXTURES / "awsh-frames.json").read_text())
    request_models = {
        "request_execute": AwshExecute("op-1", "printf ok\n"),
        "request_continue": AwshContinue("op-1", "gate-1"),
        "request_cancel": AwshCancel("op-1", "controller-cancelled"),
        "request_shutdown": AwshShutdown(),
    }
    result_models = {
        "result_ready": AwshReady(42, "/work"),
        "result_started": AwshStarted("op-1"),
        "result_gate_ready": AwshGateReady("op-1", "gate-1"),
        "result_gate_continued": AwshGateContinued("op-1", "gate-1"),
        "result_completed": AwshCompleted("op-1", 0, "/work"),
        "result_protocol_error": AwshProtocolError(
            "truncated-request", "request ended"
        ),
        "result_closed": AwshClosed("shutdown", "/work"),
    }
    for entry in entries:
        frame = bytes.fromhex(entry["frame_hex"])
        if entry["name"] in request_models:
            model = request_models[entry["name"]]
            assert decode_awsh_request(frame) == model
            assert encode_awsh_request(model) == frame
            direction = "request"
        else:
            model = result_models[entry["name"]]
            assert decode_awsh_result(frame) == model
            assert encode_awsh_result(model) == frame
            direction = "result"
        decoder = AwshStreamDecoder(direction)
        observed = []
        for byte in frame:
            observed.extend(decoder.feed(bytes([byte])))
        decoder.finish()
        assert observed == [model]


def test_awsh_truncation_and_unknown_message_fail_closed() -> None:
    decoder = AwshStreamDecoder("request")
    decoder.feed(b"awsh-v1\0execute\0op-1\0partial")
    assert_error("early-close", decoder.finish)
    assert_error(
        "unsupported-message",
        lambda: decode_awsh_request(b"awsh-v1\0future\0"),
    )


def test_session_state_accepts_gate_and_ordered_shutdown() -> None:
    state = SessionProtocolState()
    state.accept_controller(Hello(1, "session-1"))
    state.accept_envoy(Ready(1, 41, 42, "/work", 80, 24))
    state.accept_controller(Resize(2, 100, 30))
    state.accept_envoy(ResizeApplied(2, 100, 30))
    state.accept_controller(Execute(3, "op-1", "printf ok"))
    state.accept_envoy(OperationStarted(3, "op-1", 0))
    state.accept_envoy(OperationReady(4, "op-1", "gate-1", 3))
    state.accept_controller(Continue(4, "op-1", "gate-1"))
    state.accept_envoy(OperationContinued(5, "op-1", "gate-1", 3))
    state.accept_envoy(OperationCompleted(6, "op-1", 0, "/work", 0, 6))
    state.accept_controller(Shutdown(5, "capture-complete"))
    state.accept_envoy(Draining(7, "capture-complete", 6))
    state.accept_envoy(Closed(8, "shutdown", 6))
    assert state.phase == "closed"


def test_session_state_accepts_structured_cancellation() -> None:
    state = SessionProtocolState()
    state.accept_controller(Hello(1, "session-1"))
    state.accept_envoy(Ready(1, 41, 42, "/work", 80, 24))
    state.accept_controller(Execute(2, "op-1", "sleep 30"))
    state.accept_envoy(OperationStarted(2, "op-1", 0))
    state.accept_controller(Cancel(3, "op-1", "controller-cancelled"))
    state.accept_envoy(
        OperationCancelled(3, "op-1", 130, "/work", "controller-cancelled", 0, 2)
    )
    assert state.phase == "idle"


def test_session_state_rejects_out_of_state_and_regressing_output() -> None:
    state = SessionProtocolState()
    assert_error(
        "out-of-state",
        lambda: state.accept_controller(Execute(1, "op-1", "true")),
    )

    state = SessionProtocolState()
    state.accept_controller(Hello(1, "session-1"))
    state.accept_envoy(Ready(1, 41, 42, "/work", 80, 24))
    state.accept_controller(Execute(2, "op-1", "true"))
    state.accept_envoy(OperationStarted(2, "op-1", 5))
    state.accept_envoy(OperationReady(3, "op-1", "gate-1", 8))
    state.accept_controller(Continue(3, "op-1", "gate-1"))
    assert_error(
        "invalid-output-order",
        lambda: state.accept_envoy(OperationContinued(4, "op-1", "gate-1", 7)),
    )


def test_session_state_rejects_barrier_before_operation_start() -> None:
    state = SessionProtocolState()
    state.accept_controller(Hello(1, "session-1"))
    state.accept_envoy(Ready(1, 41, 42, "/work", 80, 24))
    state.accept_controller(Execute(2, "op-1", "true"))
    state.accept_envoy(OperationStarted(2, "op-1", 10))
    assert_error(
        "invalid-output-order",
        lambda: state.accept_envoy(OperationCompleted(3, "op-1", 0, "/work", 10, 9)),
    )


def test_session_state_accepts_startup_failure_range() -> None:
    state = SessionProtocolState()
    state.accept_controller(Hello(1, "session-1"))
    state.accept_envoy(Ready(1, 41, 42, "/work", 80, 24))
    state.accept_controller(Execute(2, "op-1", "true"))
    state.accept_envoy(
        OperationFailed(2, "op-1", "driver-failed", "gone", "/work", 7, 9)
    )
    assert state.phase == "idle"


def test_session_state_allows_only_one_outstanding_resize() -> None:
    state = SessionProtocolState()
    state.accept_controller(Hello(1, "session-1"))
    state.accept_envoy(Ready(1, 41, 42, "/work", 80, 24))
    state.accept_controller(Resize(2, 100, 30))
    assert_error("out-of-state", lambda: state.accept_controller(Resize(3, 120, 40)))


def test_session_state_rejects_reused_gate_id() -> None:
    state = SessionProtocolState()
    state.accept_controller(Hello(1, "session-1"))
    state.accept_envoy(Ready(1, 41, 42, "/work", 80, 24))
    state.accept_controller(Execute(2, "op-1", "true"))
    state.accept_envoy(OperationStarted(2, "op-1", 0))
    state.accept_envoy(OperationReady(3, "op-1", "gate-1", 0))
    state.accept_controller(Continue(3, "op-1", "gate-1"))
    state.accept_envoy(OperationContinued(4, "op-1", "gate-1", 0))
    assert_error(
        "reused-gate",
        lambda: state.accept_envoy(OperationReady(5, "op-1", "gate-1", 0)),
    )


def test_session_state_correlates_cancel_and_shutdown_reasons() -> None:
    state = SessionProtocolState()
    state.accept_controller(Hello(1, "session-1"))
    state.accept_envoy(Ready(1, 41, 42, "/work", 80, 24))
    state.accept_controller(Execute(2, "op-1", "sleep 30"))
    state.accept_envoy(OperationStarted(2, "op-1", 0))
    state.accept_controller(Cancel(3, "op-1", "deadline"))
    assert_error(
        "cancellation-reason-mismatch",
        lambda: state.accept_envoy(
            OperationCancelled(3, "op-1", 130, "/work", "other", 0, 0)
        ),
    )

    state = SessionProtocolState()
    state.accept_controller(Hello(1, "session-1"))
    state.accept_envoy(Ready(1, 41, 42, "/work", 80, 24))
    state.accept_controller(Shutdown(2, "capture-complete"))
    assert_error(
        "shutdown-reason-mismatch",
        lambda: state.accept_envoy(Draining(2, "other", 0)),
    )


def test_operation_failure_and_diagnostic_are_typed() -> None:
    diagnostic = Diagnostic(1, "error", "pty-read-failed", "read failed")
    assert decode_envoy_frame(encode_envoy_frame(diagnostic)) == diagnostic
    failure = OperationFailed(2, "op-1", "shell-exited", "gone", "/work", 0, 0)
    assert decode_envoy_frame(encode_envoy_frame(failure)) == failure


def test_presentation_utf8_is_incremental_and_raw_bytes_are_unchanged() -> None:
    raw = b"a\xe2\x82\xac\xffz\xe2"
    decoder = PresentationUtf8Decoder()
    text = "".join(decoder.feed(bytes([byte])) for byte in raw) + decoder.finish()
    assert text == "a€�z�"
    assert raw == b"a\xe2\x82\xac\xffz\xe2"


def test_bash_environment_removes_control_plane_injection() -> None:
    assert sanitized_bash_environment(
        {
            "PATH": "/app/bin",
            "APP_MODE": "recording",
            "BASH_ENV": "/tmp/evil",
            "SHELLOPTS": "xtrace",
            "AWSH_BASH": "/tmp/bash",
            "BASH_COMPAT": "42",
            "BASH_XTRACEFD": "9",
            "BASH_FUNC_driver%%": "() { :; }",
            "POSIXLY_CORRECT": "1",
            "TMOUT": "1",
        }
    ) == {"PATH": "/app/bin", "APP_MODE": "recording"}


def test_wrong_sequence_is_rejected() -> None:
    state = SessionProtocolState()
    assert_error(
        "invalid-sequence",
        lambda: state.accept_controller(Hello(2, "session-1")),
    )
