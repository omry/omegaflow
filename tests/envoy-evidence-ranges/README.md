# Exclusive evidence ranges (B6.4)

Run from the repository root against the existing genuine qualifications:

```sh
python3 tests/envoy-evidence-ranges/run.py \
  --qualification temp/growth/b4-b5-delivery/B4.4/qualification-01 \
  --output /tmp/omegaflow-evidence-ranges
```

The output directory must not exist. The runner verifies the three original
Linux amd64 Bash qualification receipts, fixed Awsh inputs and immutable image
IDs. It builds the current session test binary and runs the actual Envoy
session, relay, coordinator, manifested Awsh and selected Bash. Each scenario
uses a dedicated Envoy child. That child enables subreaping before launch;
the shared harness must retain its original subreaper setting.

Ten scenarios cover PTY zero/nonzero status, empty authored output, split real
output, suppression, replacement, finalization, cancellation, finalization
after accepted input closure and cleanup failure. The fixture consumes the
existing raw terminal channel and public marks. It checks the original start,
the final covering mark, monotonic public sequence and time, exact stream
attribution, and exclusion of idle output before and after the operation.
A following operation must preserve the closed range and reuse the shell.

Bytes include UTF-8, NUL and invalid UTF-8. Split stdout ends with a partial
UTF-8 sequence and stderr begins with its continuation byte; they remain
separate exact byte sequences. PTY checks retain CRLF and the fixed xterm
Readline controls. Empty authored output still has terminal control bytes.
No fixture strips those bytes from assertion evidence. Suppression and
replacement must retain the complete raw output; later controller work owns
publication and assertion evaluation.

Natural-return cases use the concrete process census and ordinary cleanup.
Control-outcome cases use the existing B4 bounded census peer, with actual
Envoy, Awsh and Bash and no authored descendants. They check result typing,
not full lifecycle census assembly. Finalization before accepted input closure
must produce a status-free finalized result. Finalization after input closure
must retain the real completed status. Cancellation cannot become completed
evidence. An injected cleanup failure must close the session without a terminal
operation result. Full actual lifecycle crossings belong to B6.5.

The default runner also builds two disposable Go overlays on the first genuine
target. One changes the completion's original output start. The other emits
a synthetic completed status for planned finalization. Each must fail with
its specific diagnostic. The checkout's production files must remain intact.
The output retains source and binary hashes, overlay requests, qualification
receipt hashes, image IDs, command vectors and raw logs.

Use `--release 5.2.15` for partial development evidence. It does not establish
the full three-build matrix. ARM64 compilation alone is not qualification.
Python interactive-assertion rejection belongs to B6.6. Inspections, controller
decoding and publication, and full actual-actor conformance retain their later
owners. This suite adds no production entrypoint or supported target.
