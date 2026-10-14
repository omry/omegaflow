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

## B3.3.2 complete start transaction

Run `go test -buildvcs=false -count=1 -race -run '^TestStart' ./session`.
The tests use the same real PTY/TCP relay plus actual private pipes. A bounded
persistent decoder keeps concatenated completion facts after matching release.
The serialized coordinator holds controller reads until that release, publishes
a stable output start before ack, and retains cancellation after the first
attempted private byte. A zero-byte failed write still crosses that boundary.

Setup and rollback are explicit deterministic peers. Their context exposes the
same original start deadline used by all private/public phases; progress cannot
renew it. A pre-attempt cancel or source rejection rolls back before an empty
terminal range, then resumes idle discard. A private error, malformed frame,
EOF, wrong ID/order or post-attempt shell exit is fatal and produces no ordinary
operation result. Watermark expiry alone is recoverable when the held-prefix result is delivered. Before operation-start, timeout or cancellation starts one non-resetting five-second publication allowance covering the finite admitted prefix, covering mark, complete empty-range result and idle input restoration. Blocked publication fails the session with `prestart-output-timeout`; source rejection and cancellation after operation-start retain the original start deadline. Source rejection wins
over a cancellation that became queued after the private attempt.

`TestStartEveryPhaseRejectsCrossedPrivateFacts` checks all four result phases;
`TestStartEpochIncludesSetupAndAllProgress` checks the actual five-second epoch;
`TestStartShellExitHandoffRetainsOriginalEpoch` checks both empty-ID shell-exit
handoffs preserve the exact absolute deadline and terminal-input-barrier versus
operation-start classification while EOF/reap remains withheld;
`TestStartShellExitRollbackAcceptsImmediateEOF` checks normal between-frame EOF
during split rollback is retained as closure evidence under that same epoch;
`TestStartCancelShellExitAfterRollbackEntryCannotHandoff` checks that a shell
exit crossing an already-started cancellation rollback cannot discard unfinished
cleanup or become a successful handoff, including an immediate private EOF;
`TestStartCompletedRollbackShellExitHandoff` checks that a later shell exit
crossing blocked result publication remains a successful handoff after rollback
completion, with the captured original deadline and no operation result;
`TestStartCancelRollbackExpiresAtOriginalDeadline` checks that a withheld
cancellation rollback fails at the captured original deadline without an
operation result;
`TestStartPublicWriteFailurePreventsPrivateAck` proves the public barrier;
`TestStartResultReaderContinuesIntoCompletion` proves reader continuity;
`TestStartUnstartedBoundaryDoesNotFreshDrainKernelBytes` distinguishes the
admitted-prefix terminal boundary from the required fresh prepared drain.
The other start tests cover input hold, both cancellation boundaries, repeat
source rejection and idle recovery, idle exit before private commitment,
private-reader uniqueness and joined teardown. The case map names every test.

These deterministic peers establish B3.3's cumulative start boundaries. B3.4
still owns real qualified Bash/Awsh start and ordinary-return assembly; B4–B7
retain controls, actual split resources, descendants and inspection. This leaf
adds no production execution entrypoint and changes no Awsh qualification input.

`TestStartPrestartDeliveryCapsBlockedTerminalOutput` uses real TCP backpressure for both watermark expiry and cancellation, proving the five-second fatal bound and no private execute or successful result. `TestStartPrestartDeliveryPublishesHeldPrefixAndRestoresIdle` proves exact binary prefix/mark/result ordering and idle reuse after either selection. `TestStartOutputBoundaryRequiresDeadline` rejects an unbounded publication without damaging the idle session.

The fact-owner regressions hold the coordinator unscheduled while the actual private decoder queues a fatal report or normal empty-ID shell exit. The result writer must wait for coordinator selection after acquiring telemetry. A queued fatal also wins over completed or expired work. Internal cancellation after telemetry acquisition but before any frame-write attempt leaves the session and public sequence intact; transport failures remain fatal. A frame already selected for publication cannot be retracted by a later cross-channel fact.

Normal internal interruption retains every unwritten byte of the admitted terminal chunk in the existing pending-output buffer. The bounded-chunk test covers interruption before output and during real TCP backpressure, then checks exact binary bytes and absolute offset after the retained suffix is flushed.
