# B7.3 inspection lifecycle acceptance

The session dispatcher owns inspection-result and cancellation acceptance.
`Coordinator.completeOperation` uses actual census/cleanup, final Awsh state and
the restricted worker from the same executable. No controller path probe supplies
inspection evidence. The cleanup deadline ends after the final census and output
barrier. Inspection uses the original operation lifetime; cancelling its worker
has the existing five-second stop/reap bound.

Run the complete B7.3 proof with genuine retained qualification inputs:

```sh
python3 tests/envoy-inspection-lifecycle/run.py \
  --qualification /absolute/qualification-directory --output /new/evidence-directory
```

The runner verifies the complete three-build qualification matrix and exact Awsh
and startup asset bytes before staging each test executable. It retains native
image IDs, executable hashes, commands, times and logs. Six cases per target prove
file/directory results from final exported environment/cwd, typed missing-file
failure with no partial array, split output, cancellation, planned finalization,
and cancellation crossing finalization. All successful cases prove actual worker
reap and a subsequent operation in the same persistent shell. Cancellation cases
hold acceptance of an actually reaped worker reply; they do not substitute the
filesystem algorithm or invent a child result.

Focused Go tests also prove cleanup cancellation skips the worker, late finalize
loses to observed return, inspection survives the retired cleanup deadline, plan
correlation, malformed/launch/session failures, and actual lifecycle publication.
The result-first test blocks telemetry, then proves a crossed cancel was consumed
through a following FIFO resize before releasing publication. The cancel-first
test holds reap and forbids a result or next operation until it joins.

A separate crossing proves the existing owner observes joined worker completion
while resize delivery is blocked, so the cancellation timer cannot fail later
result publication. The disposal bound remains available after caller failure.

The unreapable-kernel branch uses a controlled handle: it never reports reap,
forcing the actual five-second fatal inspection-cancel-timeout path. An ordinary
CI test cannot deliberately create a permanently uninterruptible kernel task.
The test also requires the fatal diagnostic on writable telemetry and preserves
the original deadline and cause. Busy telemetry serialization cannot delay failure;
the diagnostic attempt uses only the existing individual control-write bound.
This test does not claim a genuine unkillable target process. Eight source-omission
controls must fail with their named invariant diagnostic. They remove early
commit, result discard, joined reap, cancellation expiry selection, cleanup
retirement, cancellation-timer retirement after joined reap, typed failure routing
or request/plan count correlation. Full Go race
verification remains required. `--proof-only`, `--native-only` and `--release`
select explicitly partial development runs and do not satisfy complete acceptance.

B7.1 algorithms and B7.2 isolation/result validation remain required cumulative
checks. B8.4 owns the full B1–B8 actual-actor conformance gate. This isolated native
assembly is not a new production entrypoint. Only the original three genuine
Linux amd64 Bash targets are exercised. ARM64 builds establish compilation only.
