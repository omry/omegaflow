# B2.5 submission boundary

This is isolated candidate evidence, not supported-entry admission or a complete
Awsh execution path. Production `submission.Check` accepts only a qualified
generated table entry. The test binary verifies each acquired candidate digest
without adding a production override.

The Go tests compare the canonical emitter against the four independent frozen
B1 frames, check source/scalar/path bounds, run the two output-empty selected-Bash
parse passes, and exercise complete source-helper framing and short writes.
The checker uses its caller's deadline and rechecks the executable before each
fork. Integrity errors remain distinct from recoverable source rejection.
Its parse-only children use the fixed launch values and inherit no application
variables, Bash options, exported functions or startup-file controls. Hostile
parent parser settings must neither admit extglob syntax nor alter normal checks.
The test process protects inherited CI runner descriptors before the cases run,
matching Awsh's controlled parent. The descriptor fault case then deliberately
removes protection and still requires the production checker to reject it.

Run from the repository root with verified B2.3 acquisition evidence:

```sh
GOCACHE=/tmp/omegaflow-go-cache python3 tests/envoy-bash-submission/run.py \
  --acquisition /absolute/path/candidate-acquisition.json \
  --output /absolute/path/new-proof-directory --mutations
```

The driver reuses the existing launch acquisition and image-identity validation.
It derives read-only assets from each pinned source image and removes the declared
system rcfile only in the derived test image. It launches real interactive Bash
on a controlling PTY, uses the actual manifested source helper, and sends the
actual `0x18 0x02` submit macro. Every helper request and raw terminal byte is
retained. A deterministic peer supplies only the fixed source reply and the
existing startup-form prompt exchange. Before accepting `prompt_ready`, that
peer retains the complete workload terminal state, enables ICANON, clears ECHO,
and requires exact readback. A2.10 and B2.3.3 own this approved transition in the
actual startup module; its affected startup suite runs separately.
`START_RELEASED` is an explicit test-only
no-op: B2.6 owns its signal and active-operation machinery, and B2.7 owns the
completion handoff. This test cannot prove those absent implementations.

The 17 real helper cases cover minimum/maximum source, UTF-8, preceding status,
history and vi restoration, comments, quotes, heredocs, source-boundary LF,
errexit with a hostile or disabled colon, disabled `set`, malformed helper replies
and readonly Readline variables. The two special-variable cases also exercise
integer, array, nameref and case-conversion attributes, plus harmless export
and integer-cursor controls. Unsafe attributes fail-stop before copying the
frame, and the original readonly assignment and mutation cases remain intact.
Known and missing history event designators
remain literal while the saved workload history setting is on. The prompt disables
history expansion before Readline accepts the frame; `ENTER` restores the recorded
setting for the source. Raw output must contain the requested authored
output without source/frame redisplay. Four isolated rcfile mutations exercise
status restoration, loader binding, history restoration and readonly detection.

Five additional loader fault cases mount a test-only helper wrapper at the fixed
helper path. It delegates startup and fail-stop to the real binary and substitutes
only source stdout/status: missing, malformed or duplicate marker, empty success,
and nonzero exit. Each requires non-returning fail-stop and restop after SIGCONT.
These fixtures exercise wrapper failure handling, not production helper identity.

The earlier echo-on redisplay observation remains historical evidence. The
approved echo-off transition is required before Readline entry; these proofs
retain every raw byte and apply no terminal-output filtering.

B2.6/B2.7 start and completion proofs, B2.8 build admission, B3 public start
ordering, B5 real split setup, and B8 fatal crossings remain pending. Linux
arm64 cross-build checks do not qualify a genuine arm64 target.
