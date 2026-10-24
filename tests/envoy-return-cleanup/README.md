# Actual ordinary-return cleanup (B6.3)

Run from the repository root with the existing genuine Linux amd64 qualification:

```sh
python3 tests/envoy-return-cleanup/run.py \
  --qualification temp/growth/b4-b5-delivery/B4.4/qualification-01 \
  --output /tmp/omegaflow-return-cleanup-evidence
```

The output directory must not exist. The runner verifies all three original
qualification receipts, fixed adapter inputs and immutable Docker image IDs.
It builds the current Envoy test binary and runs the actual session, relay,
coordinator, manifested Awsh and selected Bash. Each case uses a dedicated Envoy
child which enables subreaping before launching Awsh. The shared harness keeps
its original subreaper setting. Each independent outside sibling must survive.
No production launch override or supported target is added.

The ordinary matrix covers background, disowned, nohup, setsid, double-forked
and SIGTERM-ignoring authored descendants. A split case leaves both actual FIFO
writers in an escaped daemon. Successful return must terminate the exact pinned
lifetime, clear Bash jobs/waits, remove split resources, prove final idle state
and publish only after a fresh covering output mark. A second real operation
must retain the shell state, cwd and preceding status through the same private
reader and the same concrete process owner. Split stdout/stderr include UTF-8,
NUL, invalid UTF-8 and CR/LF bytes and are compared exactly.

Closed ownership and a lost root pin must fail the session without a terminal
result. In two further cases the outside controller retains one actual FIFO
writer. The other stream must independently reach EOF, while the actual helper
remains blocked. Either missing EOF must fail under the original five-second
cleanup epoch. The controller-owned writer is outside the cleanup tree and
closes only after the dedicated Envoy child exits.

The runner also compiles four disposable Go overlays and measures them on the
first selected genuine target. These are explicit failure models, not natural
kernel failures or production callbacks. They test a corrupted cleanup helper
identity, a real FIFO path replaced before cleanup, a real process introduced
after cleanup but before final idle proof, and a drain syscall held until its
original context expires. All must fail closed. Overlay source, build requests,
image IDs, raw logs, receipt hashes and test binary hashes remain in the output.
The checkout's production bytes must stay unchanged.

Use `--release 5.2.15` for a partial development run. `--faults-only` runs the
four overlays; `--fault NAME` runs one overlay. These selections are recorded
as partial evidence and do not establish the complete three-build matrix.
ARM64 compilation is not genuine target qualification. Foreground/cancel/end
assembly, evidence eligibility and actual-actor conformance remain with their
B6.4/B6.5/B8 owners. Existing foundation suites retain the detailed malformed
private-fact, split-removal and blocked-output checks; their deterministic
cleanup peers alone do not prove real process termination.
