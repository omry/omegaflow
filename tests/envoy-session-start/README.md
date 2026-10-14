# B3.3.1 relay and input-boundary acceptance

Run from `runtime/envoy`:

```sh
GOCACHE=/tmp/omegaflow-go-cache go test -buildvcs=false -count=1 -race -run '^TestRelay' ./session
```

The tests use the production relay, actual loopback TCP connections and Linux
PTYs. A deterministic startup peer establishes readiness; no real Awsh/Bash
start, completion, descendant cleanup, split setup, inspection, or supported
build qualification is claimed by this leaf.

`NewRelay` borrows the existing Workload master and Channels. It claims one
reader for that workload's session and retains the session mark budget.
`Drain` joins the reader, delivers its admitted chunk and the available kernel
prefix, then publishes a covering `pty` mark under the original caller context.
The output writer advances the raw offset by actual positive writes; marks are
constructed atomically with their public sequence. Natural PTY EOF is reported
as a fact for the lifecycle owner. Other output failures close the public
channels and preserve the cause. Relay close joins its I/O without closing the
borrowed master; it cannot restart the session budget or a stopped reader.

`HoldInput` uses the caller's original deadline, counts actual socket reads,
discards idle input, and suspends the one controller reader at the watermark.
The in-flight application data remains bounded by one fixed reader chunk and
the existing event queue. Held input remains in TCP with socket backpressure.
Only the caller's matching release opens a current operation ID; queued input
retains the ID at its read boundary and cannot be replayed into another one.
The fixed `0x18 0x02` trigger shares PTY write ownership with eligible input.
Eligibility closure interrupts and joins a blocked old input write before a
later writer can use the descriptor.

| Test | Observable proof |
|---|---|
| `TestRelayExactBinaryDrainAndMarks` | Binary PTY/TCP equality; delivered offset; monotonic and repeated boundary marks |
| `TestRelayFreshDrainCannotLoseReaderAdmittedPrefix` | Previously admitted and kernel-buffered bytes survive the reader/drain handoff; a second reader is refused |
| `TestRelayBudgetAndOverflowFailBeforeOutput` | Mark budget and offset overflow fail without unmarked bytes escaping |
| `TestRelayInputHoldUsesSocketBackpressure` | An actual sender blocks while read count stays fixed; resumed idle reads discard retained bytes |
| `TestRelayWatermarkInputReleaseAndStaleEventDiscard` | Watermark completion, current-operation input, no stale replay and no regressing watermark |
| `TestRelayInputHoldCancellationDoesNotResetEpoch` | Original cancellation or deadline ends the waiting hold with its cause while channels stay live for idle discard and later input |
| `TestRelayTriggerAndControllerInputAreSerialized` | Exact trigger bytes precede the released authored bytes |
| `TestRelayConcurrentMarksAndPublicEventsHaveOneSequence` | Concurrent marks and public events retain one increasing wire sequence |
| `TestRelayEOFAndCloseRetainDescriptorOwnership` | EOF fact, joined close, retained master, and no reader or budget restart |
| `TestRelayDrainCapsAlreadyBlockedWriterWithOriginalEpoch` | Existing output backpressure cannot extend the caller epoch; failure closes the channels |
| `TestRelayPTYSerializationDoesNotReplaceEarlierWriterDeadline` | A queued trigger's short deadline cannot disturb the active input writer; eligibility closure ends that writer |
| `TestRelayInputAPIsRejectInvalidBoundaries` | Missing/expired contexts, invalid IDs and duplicate holds are rejected |

Delivery mutation evidence is retained outside these source tests. Moving marks
after bytes, allowing stale-event replay, omitting the fresh kernel drain, or
disabling held socket reads makes the corresponding focused test fail.

B3.3.2 owns the complete operation-start coordinator and its setup/rollback
peers, private phase validation, public start/ack order and matching
`start_released`. B3.4 repeats the boundaries with real qualified Awsh/Bash and
adds ordinary completion coordination. Clearing workload line-discipline input
and actual descendant/split/inspection implementations retain their governing
cleanup and later-slice owners. The static corpus remains declarative and its
full B3.3 package acceptance is pending until the start successor passes.
