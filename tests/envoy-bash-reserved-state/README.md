# B2.4: readonly Bash state and whole-request mutation preflight

This leaf installs canonical startup state and readonly `trap`, `enable`, `awsh`
and adapter functions in the fixed rcfile. Mutation preflight examines the
complete expanded argument vector before invoking a native builtin. Selected
Bash validates and canonicalizes signal specifications; no portable signal
number is assumed. Queries, positive enablement, and ordinary non-reserved
operations delegate unchanged arguments to the selected builtin. POSIX requests
retain native status/diagnostics under readonly-unset `POSIXLY_CORRECT`.

Run from the repository root with the verified Reploy candidate acquisition:

```
PYTHONOPTIMIZE=1 .venv/bin/python tests/envoy-bash-reserved-state/run.py \
  --acquisition /absolute/path/candidate-acquisition.json \
  --output /absolute/path/new-proof-directory --mutations
```

The runner reuses the B2.3 image/provenance validator, checks the actual regular
`/bin/bash` digest, and derives network-disabled test images with the exact rcfile
and built Awsh helper. Python is the existing OS provider dependency. Candidate
images and the host stay unchanged; only derived images remove the declared
system rcfile. A private controlling terminal exercises the selected interactive
Bash through its real `--noprofile --rcfile ... -i` initialization boundary.
Test-only `-c` scripts exercise mediation independently of the future source-frame
and operation-start implementation. This is not a production execution path.

`cases.json` maps each B2.4-owned B1 static case to an executable scenario, except
the cumulative package marker. Ordinary cases compare status, exact stdout and
stderr against a test-only native copy that removes mediation while retaining
canonical inputs and POSIX reservation. Positive state checks independently
require canonical options, readonly functions, preserved positional parameters,
child signal handlers, and the fixed helper path under hostile PATH. The gate
helper is not implemented here: its current unsupported-request status proves
fixed-path invocation only. Actual gate transport remains B4.3.

Fatal cases observe the actual manifested helper in stopped state, send three
SIGCONT requests, observe each restop, kill/reap that helper, and obtain a
post-refusal state snapshot from the still-blocked Bash through an ordinary
WINCH trap. Exact before/after trap and builtin inventories prove no partial
mutation, including mixed target requests. Each case retains its command,
stdout/stderr, state snapshots and timestamped scheduling observations. The
returning-helper fault uses a test-only bind mount and requires the builtin
fallback to remain non-returning and output-empty. Its 50 ms negative-return
observation is bounded evidence, not exhaustive scheduling proof.

`--mutations` removes each principal safeguard from an isolated asset copy and
requires the corresponding focused case to fail with its expected diagnostic.
The original and mutated hashes, commands and failure logs remain in the output.
These faults never change checkout assets or production configuration.

Case-matching regressions enable `nocasematch` and then disable the ordinary
`shopt` builtin. Native uppercase builtin/option errors remain ordinary errors;
lowercase reserved and mixed mutations remain fail-stop. Exact matching uses
shell parameter expansion, preserving workload options without depending on
`shopt`. Prompt-helper frames must retain history-off state despite lowercase
`h` in Bash's flags. A separate test-only returning-helper mount distinguishes
the rejected `GATE` token from accepted lowercase `gate`; it proves token
dispatch only, without claiming the later gate transport.

Tracing cases compare ordinary calls and native diagnostics with `set -x`
enabled, require tracing to remain enabled, and check descriptor 3 is restored
whether originally open or closed. Definition-scoped redirections hide adapter
internals while the native builtin keeps its diagnostic stream. The temporary
stderr descriptor is closed on preflight and helper paths. Refused-call probes
retain the authored command's trace but require no adapter trace or output.

The prompt regression disables the ordinary `local` builtin, enables source-visible
history, and returns status 1 before calling the exact prompt hook.
The real Awsh helper must send the saved state and startup readiness in order to
a deterministic socket peer. Both complete request frames remain in the proof.
Prompt cases append their command to a test-only rcfile and use actual `-i`
startup. The selected Bash `-i -c` form skips Readline initialization and crashes
when `bind -x` is followed by `set -o emacs`; it is not Awsh's launch form.
This exercises the existing prompt forms, not the later completion/cleanup path.

The approved adapter reservations are cooperative. Explicit `builtin`/`command`
bypasses or shadowing adapter command names are unsupported same-identity
interference. B2.7 owns immutable completion-state validation and its fatal
bypass cases; B8.4 closes real-actor failure evidence. This leaf does not claim
those proofs or add wrappers around `builtin` and `command`.

The B1.3.1 owning successor corrects `B1-C022-combined-options` to the approved
ordinary query outcome. Its literal `trap -p CHLD INT` input is preserved, and
this runner compares its status and output with native selected-Bash behavior.
The static oracle still rejects actual reserved and mixed-target mutations.
Approved PR45 remains immutable; B1.3.1 changes only the successor corpus and
its pending-correction annotations. Build qualification remains with B2.8.

Actual Awsh/Bash startup is checked separately with
`tests/envoy-bash-startup/run.py` after this trusted rcfile change. Source frames,
recurring entry restoration, start markers and completion belong to B2.5–B2.7;
full candidate qualification and concrete build expectations remain B2.8.
Real Envoy controls, cleanup and gates remain B3–B8. These candidate observations
admit no supported entry and provide no genuine arm64 execution evidence.
