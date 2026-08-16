from pathlib import Path

import pytest

from omegaflow.reploy_protocol import (
    BrokerReady,
    ClientError,
    Opened,
    Ready,
    ReployProtocolError,
    Terminated,
    WorkloadExit,
    WorkloadOutputsFinalized,
    decode_client_event,
    decode_run_result,
    encode_client_request,
)


FIXTURES = Path(__file__).parent / "fixtures" / "reploy-controlled-session-v1"


def _lines(name: str) -> list[bytes]:
    return (FIXTURES / name).read_bytes().splitlines()


def test_decodes_public_reploy_client_event_fixtures() -> None:
    events = [decode_client_event(line) for line in _lines("client-events.jsonl")]
    assert isinstance(events[0], BrokerReady)
    assert isinstance(events[1], Opened)
    assert isinstance(events[2], Ready)
    assert isinstance(events[3], WorkloadExit)
    assert isinstance(events[6], WorkloadOutputsFinalized)
    assert isinstance(events[8], Terminated)
    assert isinstance(events[9], ClientError)
    opened = events[1]
    assert isinstance(opened, Opened)
    assert opened.endpoints[0].host == "workload"


def test_encodes_public_reploy_client_request_fixtures() -> None:
    assert [
        encode_client_request("resize", columns=120, rows=40).rstrip(b"\n"),
        encode_client_request("terminate").rstrip(b"\n"),
        encode_client_request("complete").rstrip(b"\n"),
        encode_client_request("acknowledge-terminated").rstrip(b"\n"),
    ] == _lines("client-requests.jsonl")


def test_decodes_public_reploy_host_result_fixtures() -> None:
    failed, succeeded = [decode_run_result(line) for line in _lines("run-results.jsonl")]
    assert not failed.ok and failed.session_result is None
    assert succeeded.ok and succeeded.result_acknowledged is True
    assert succeeded.controller_output is not None
    assert succeeded.controller_output.kind == "directory-retained"


@pytest.mark.parametrize(
    "payload",
    [
        b'{"schema":"reploy-controlled-session-client-v1","type":"ready","type":"ready"}',
        b'{"schema":"reploy-controlled-session-client-v1","type":"ready","extra":true}',
        b'{"schema":"other","type":"ready"}',
        b'{"schema":"reploy-controlled-session-client-v1","type":"opened","operations":[],"endpoints":[],"columns":0,"rows":24,"output_finalization_timeout_milliseconds":1}',
    ],
)
def test_rejects_malformed_client_events(payload: bytes) -> None:
    with pytest.raises(ReployProtocolError):
        decode_client_event(payload)
