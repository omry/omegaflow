# Reploy Integration Implementation Plan

## Status

- Temporary delivery plan.
- Trusted implementation boundary: the rebuilt stack's base, the tip of
  `main`, which carries the formerly approved implementation stack. That
  stack's final pre-rebuild PR number, 8, has since been reused by an open PR
  in the rebuilt stack, so PR numbers are not boundary evidence; within the
  rebuilt stack, the `approved` label on a PR is. Node identities are not
  recorded here because every restack rewrites them.
- Updated: 2026-08-28.
- Retire this document after terminal-only Reploy integration is complete and
  the remaining work has moved to separately approved plans.

This document owns delivery order, review boundaries, and progress tracking for
the Reploy integration. Product contracts remain in
[OmegaFlow Workload Envoy Design](omegaflow-envoy-design.md),
[Reploy Recording Environments Design](reploy-environments-design.md), and
[OmegaFlow Envoy Protocol v1](envoy-protocol-v1.md).

## Starting point

The approved implementation contains neither the Envoy protocol models and
fixtures nor an Awsh or Envoy implementation. Independent documentation slices
establish the Workload Envoy design, protocol amendment, and this delivery plan
before implementation is rebuilt in the bounded B slices. Later accepted
architecture amendments remain additional documentation predecessors for their
affected implementation slices; they are not implementation evidence. The
approved base also contains no production Envoy or Reploy integration. A slice
must therefore treat implementation artifacts as arriving only with their
owning reviewed implementation slice rather than as already present.

The former PR 9 through PR 13 stack and the off-stack controlled-session work
are raw material only. They may supply tests, fixtures, implementation ideas,
or small extracted patches. Their previous topology, completion claims, and PR
boundaries carry no authority into the rebuilt stack.

Raw material currently includes:

| Source | Useful material |
| --- | --- |
| PR 9, `7194221b6ea9` | Envoy session, PTY, pump, failure, and Awsh changes |
| PR 10, `3b34c6b34faa` | runtime build, manifest, staging, and blueprint models |
| PR 11, `1021110dc073` | controller lifecycle, codecs, Envoy client, and terminal adapter |
| PR 12, `0e8caf4caf44` | browser endpoint and readiness integration |
| PR 13, `f70886d84bc3` | self-contained controller runtime and backend UX |
| `e98bdf5a18` | broader public Reploy codec fixtures and conformance tests |

A second, independent rebuild of the same material is visible as a local draft
stack. It was never pushed, carries no pull-request mapping, and is not a
successor of the commits above, so its contents differ. These commits are
optional raw material, not delivery prerequisites: the plan and each slice must
remain implementable without them. They may be consulted while the
corresponding implementation work is brought onto the rebuilt stack, where any
selected changes receive the normal slice review. Their current hashes are
temporary and are expected to change during that integration.

| Source | Useful material |
| --- | --- |
| `84d7a637bdb8` | alternative Envoy design, protocol amendment, and Awsh prototype |
| `0e3646096486` | alternative Go Envoy protocol v1 |
| `67a6212191ff` | split-screen console for the Awsh prototype |
| `45e15c03eb75` | pytest import fix from the repository root |
| `9a5d6743a568` | alternative completed Awsh Bash adapter prototype |
| `3f7615751985` | alternative workload Envoy runtime |
| `0abe92bc4146` | alternative Reploy workload runtime packaging |
| `ef6ec0f80c94` | alternative controller and terminal capture integration |
| `49ff3cc3cecc` | alternative browser capture integration |
| `90542d769a91` | alternative self-contained controller runtime |
| `c43590a8945c` | alternative Reploy controlled-session codecs |

These local draft hashes are intentionally not remote references and are not
evidence that their implementation is approved or complete. Update or remove
each row when its selected material is integrated into a reviewed stack slice.

## Fixed delivery decisions

1. Reploy runs the recording controller. Workload placement is selected
   separately as `host` or `reploy`; `host` remains the default.
2. The first delivery milestone is a terminal-only isolated Reploy workload.
   Browser capture, publication cutover, and host-workload parity follow only
   after that milestone passes.
3. Envoy protocol v1 is unreleased. This plan amends it before implementation
   to carry bounded `file_exists` and `produces` inspection requests, typed
   workload-side results, and sender-stamped output marks.
4. Incremental implementation PRs may implement only their declared portion of
   v1. OmegaFlow does not claim v1 conformance or enable the production path
   until the applicable complete conformance gate passes.
5. Hydra produces complete typed controller and workload Reploy blueprints.
   OmegaFlow materializes and retains those resolved blueprints without
   post-composition repair.
6. OmegaFlow-owned workload files are staged from a manifest and mounted
   read-only and executable at `/omegaflow-runtime`.
7. The existing FIFO runner remains available until a separately reviewed
   host-Envoy parity and cutover change removes it.
8. Bash is the only top-level shell backend in this plan. Multi-shell,
   multi-terminal-pane, project discovery, blueprint refresh, and secret
   delegation are outside the terminal-only milestone.

## Review discipline

- Start the rebuilt stack from the trusted base: the tip of `main`.
- Keep at most three unapproved PRs live at once.
- Once a PR is approved, do not rewrite it. Corrections go into a successor PR
  and receive their own review.
- Give each PR one contract or production subsystem responsibility.
- Target fewer than 800 handwritten changed lines. Split a PR before 1,200
  handwritten changed lines unless the excess is mechanically generated or a
  focused golden-fixture corpus.
- Do not combine design, runtime packaging, controller lifecycle, terminal
  adaptation, browser work, or publication work merely because raw material
  previously placed them in one commit.
- Run focused tests and the relevant broader suite before review. Advance the
  stack bottom-up only when CI is green and review feedback is resolved.
- A passing partial implementation is recorded as partial. Removing or
  narrowing a claim is not evidence that the underlying behavior exists.

## Delivery sequence

### A. Design delta

**A1. Freeze the rebuilt contracts and delivery plan**

- Review the Workload Envoy product direction in its own documentation slice.
- Review the Envoy protocol v1 amendment in its own documentation slice,
  including bounded workload inspection, its private Awsh boundary, and
  sender-stamped output marks that carry stream identity and timing as offsets
  rather than as a second copy of the output bytes.
- Add this implementation plan in a third documentation slice and replace the
  obsolete embedded implementation plans in both product design documents with
  concise phase summaries and a link here.
- Correct status language so approved work, raw material, and remaining work
  are distinguishable.

Gate: each independent documentation slice is reviewed bottom-up and approved
at its exact current head before code is extracted from the raw stack. Approval
of one slice is not evidence for another. Any later accepted architecture
amendment is an additional documentation gate before its affected B work.

### B. Local Envoy and Awsh conformance

**B1. Protocol models and fixtures**

Add Go validation and canonical fixtures for inspection requests, deterministic
IDs, resolved plans, typed results, aggregate frame bounds, malformed cases,
failure codes, matching and mismatching handshake session IDs, and every
startup/control-write deadline epoch. Cover an earlier writer exiting with
bytes still buffered, and require the fresh exclusive pre-start drain before
`operation_started`. Freeze path-resolution and file-digest
compatibility with the native runner, including undefined variables, `~user`,
symlinks, and nested special entries. Directory digests are deliberately not
native-compatible: freeze separately tagged fixtures for the native `directory`
encoding and the protocol's `directory-v2` framing instead.
Also freeze zero-byte boundary fixtures for the first PTY and split operation
and for a first operation that fails or is cancelled before starting. Require
the initial `pty` boundary-only stream, repetition of the current stream when no
new byte selected one, and a same-offset `stdout` or `stderr` mark before the
first actual split-stream byte.

**B2. Awsh boundary alignment**

Align execution-policy framing, persistent Bash state, inspection-path
resolution, and descriptor non-inheritance with the amended protocol.

**B3. Envoy session foundation**

Implement listeners, the controller-generated session-ID handshake, the
independent actor-local connect/hello/ready deadlines, one PTY, persistent
Awsh/Bash startup, shared PTY execution, exact byte relay, bounded control
writes, an empty `HISTFILE` for controlled Bash after application
environment delegation, and orderly shutdown.

**B4. Operation boundaries and controls**

Implement output barriers, completion, input, resize, cancellation, action
gates, planned finalization, and final drain. Freeze resize placement at the
serialized writer's authored frontier, including queue-order ties and
zero-duration schedules. Classify each resize when the controller sends it. A
request sent during synthesized prompt or typing retains that authored-span tag
through acknowledgement and publication: use the then-current frontier if its
acknowledgement is dequeued before the span closes, otherwise use the final
prompt-and-typing frontier. Cover an acknowledgement delayed until after
schedule commitment. Treat every resize accepted while `execute` remains in
`Starting` as part of the preceding prompt-and-typing span's closing seam;
buffer it through `operation_started` or the replacing pre-start failure,
cancellation, or drain and place a matching applied resize at the final typing
frontier. Cover delayed `execute.input_through` with a resize across each of
those outcomes and prove that the wait does not enter the cast; when a drain
resolves the request before `resize_applied`, require no resize event instead.
From `operation_started` through the terminal event, treat every resize accepted
during a presentation-timed operation as part of that operation's authored span
even before schedule
commitment begins; buffer it until the schedule is known and place it after the
latest authored PTY output event covered by its `output_through` frontier. Carry
the cumulative terminal-input watermark on each
`continue` and keep the gate closed until the Envoy has received those bytes;
cover delayed terminal input and cancellation while waiting. A continuation
watermark timeout must take fatal session teardown with no terminal operation
result or private gate-abort mechanism. Linearize each accepted resize in the
output pump,
close and carry its preceding `output_through` frontier across the PTY before
`TIOCSWINSZ`, and make the controller reach that raw-log frontier before
publishing the resize. Cover PTY
output immediately preceding a resize and a continuously writing PTY workload
in B4 conformance; add the active split-stream equivalent with B5. Cover both
resize/shell-end race outcomes: `resize_applied` resolves a resize applied
before drain, while `draining` resolves a superseded outstanding resize without
publishing it. Prove that a shell-ended drain crossing both an unstarted
`execute` and its deadline-derived `cancel` resolves both requests with no
terminal operation result and leaves the planned beat to fail as unrunnable.
Make every failed `TIOCSWINSZ` fatal to the session in idle and active-operation
states: emit best-effort `resize-failed`, no `resize_applied` or terminal
operation result, close the channels, and exit nonzero. Before any terminal
operation result, drain the PTY bytes and emit their covering mark.

**B5. Split execution**

Implement separate stdout/stderr supervision, ordered terminal forwarding,
sender-stamped output marks, and split-stream conformance. Extend B4's active
operation resize frontier across every split stdout/stderr source before
`TIOCSWINSZ`. Use `output_through` as a covered prefix for each logical stream
and place the resize after the latest authored output event derived from any
covered prefix. For stdout-then-stderr publication, require authored order to
win when raw stream interleaving makes both sides of the raw frontier impossible
to preserve: once stderr is covered, all stdout precedes the resize and only
uncovered stderr remains after it. Cover a long-running operation whose resize
arrives before any authored event is committed, plus
stderr-before-resize-before-stdout and stdout-before-resize-before-stderr
split-stream cases. Before any terminal operation result, observe EOF on the
operation's split pipes, drain the pipe and PTY bytes, and emit their covering
marks. Prove newline-sensitive `stdout + stderr` assertion decoding from the
exact logical stream bytes under interleaved presentation; do not normalize,
merge, or reorder the assertion inputs to match their presentation order.

**B6. Process cleanup and exclusive observation**

Implement fail-closed exclusive evidence ranges for checked, suppressed,
replaced, and presentation-timed operations. Reject `output_contains` and
`output_regex` at plan compilation when an interactive operation or any of its
continuations sends bytes through `text`, `key`, or `control`; retain
`wait_for` as visible-terminal synchronization rather than assertion evidence.
Prove that planned recording-end finalization closes the intentionally open
operation's output range and evaluates its completed non-exit assertions, while
the synthetic termination status neither satisfies nor fails an authored
exit-code assertion. Prove that finalization failure and user cancellation
invalidate assertions rather than evaluating them.
Independently of that evidence mode, implement Envoy-owned process cleanup after
every submitted Bash operation: subreaper adoption, pidfd tracking, repeated
`/proc` census,
termination, reap, EOF, and drain before the terminal result. Cover ordinary
background jobs, `disown`, `nohup`, `setsid`, rapid double-fork daemonization,
cancellation, cancellation received after the Awsh result while mandatory
cleanup is in progress, planned finalization, the five-second monotonic cleanup
deadline, a cancellation racing a command that ends the persistent shell, and a
setup-launched service outside the controlled tree remaining unaffected. Prove
that cancellation and finalization grace-period expiry terminates and reaps
persistent Bash, emits `operation_failed` with the corresponding timeout code
and `shell_ended: true`, and enters the `shell_ended` drain without another
prompt or operation. Prove that a deadline cancel accepted during the original
finalization grace sends no second signal, does not reset the timer, and switches
the result to `operation_cancelled` after timely driver return and cleanup or to
`cancel-timeout` with `shell_ended: true` on expiry. Prove
that post-result cancellation does not signal idle persistent Bash, does not
reset the cleanup deadline, waits for successful cleanup, and then emits
`operation_cancelled`; cleanup failure still produces no terminal operation
result and takes fatal session teardown. Also prove the same outcome when
cancellation arrives during post-finalize cleanup.
Also prove that the shell-ended result wins its cancellation race without losing
its reaped status. Prove that `finalize` received after the Awsh result likewise never
signals idle persistent Bash, never resets cleanup, and never replaces the
returned status with a synthetic status-free result; successful cleanup
preserves the returned status, while cleanup failure remains fatal with no
terminal operation result. Do not add a controller process-lifetime option or a
per-operation numeric descendant-admission guarantee. V1 does not preserve
processes across operations; session-lifetime support may be added
later if setup cannot handle a compelling use case. Any future deterministic
process ceiling belongs to a Reploy-owned kernel-enforced workload/session
domain.

**B7. Workload inspection**

Resolve configured paths in persistent Bash state, perform bounded workload
existence/type/hash inspection in Envoy only after the universal operation
cleanup and output drain, and return private typed results without controller
filesystem access or probe commands. Run the resolved plan in a short-lived,
restricted worker mode of the Envoy executable with no inherited session
channels. Serialize worker-result acceptance against `cancel`; cover the normal
result winner and both serialized winners when cancellation crosses ordinary or
planned-finalization inspection. A cancellation winner after the Awsh result
must not signal idle persistent Bash or reset the cleanup deadline; after the
required cleanup, stop and reap the worker within five seconds and emit
`operation_cancelled`. A `finalize` received after the Awsh result must continue
through inspection and preserve the returned status. Cover a blocked worker
exhausting the cancellation deadline: emit the fatal
`inspection-cancel-timeout` diagnostic, emit no terminal operation result,
prevent a later operation, and take fatal session teardown. Also cover mutation
races, cleanup and drain failures, and every inspection resource limit.

**B8. Failure and isolation hardening**

Prove socket and private-descriptor isolation, stable failure classes, channel
loss, shell exit, malformed traffic, cleanup, and repeated shutdown behavior.
Cover both orderings of `shutdown` crossing an idle persistent-shell exit:
observed shell exit first resolves `ShutdownSent` through `shell_ended`, while
accepted shutdown first preserves the requested shutdown reason.

Gate: the complete local Envoy/Awsh conformance suite passes. No Reploy or
terminal-runner integration is required to review the individual B slices.

### C. Controller and Reploy boundaries

**C1. Public Reploy codecs**

Extract strict public controlled-session event, request, and host-result codecs
with the broad fixture corpus, without controller lifecycle or subprocess code.

**C2. Controller lifecycle state machine**

Implement lifecycle ordering, attachment startup, completion, termination,
acknowledgement, cancellation, stderr retention, and failure handling against a
deterministic fake client. Accept ordinary completion from the controller's
`Cancelling` state when the serialized inspection result wins before
cancellation.

**C3. Envoy session client**

Implement the Python terminal/telemetry client against the canonical Envoy v1
fixtures, including inspection results and cross-channel output barriers.

**C4. Runtime build artifact**

Add reproducible platform builds and the manifest for Envoy, Awsh, and their
required runtime files.

**C5. Runtime staging**

Materialize a manifest-validated read-only `/omegaflow-runtime` tree without
blueprint composition or controller execution. Prove staging rejects a missing,
additional, non-regular, unreadable, or hash-mismatched trusted terminal,
Readline, or locale asset.

**C6. Blueprint schema and composition**

Add typed controller/workload blueprint models, Hydra composition, read-only
controller configuration, resolved YAML retention, and fixture conformance.
Reject the complete normative launch-control environment enumeration before
materialization, including application-provided `HISTFILE` and `INPUTRC`.
Compose an empty `HISTFILE` after the application, validate that final
value, and prove neither application `HISTFILE` nor the default under its
`HOME` can block Bash before OmegaFlow types the Envoy bootstrap command. Prove
the final blueprint cannot shadow the trusted terminal, Readline, or locale
tree and fails before controlled Bash starts if a required mounted asset is
missing, non-regular, unreadable, or mismatched.

**C7. Controller run input**

Prepare the bounded `omegaflow-controller-run-v1` manifest and declared assets,
stage them as a read-only controller-only `/omegaflow-input` mount, and add the
internal controller command that validates the schema, paths, hashes, bounds,
and recording plan before starting the session client.

**C8. Controlled-session invocation**

Prepare separate deployments, invoke the public controlled-session command,
resolve only trusted opened endpoints, and retain the exact host result and
stderr. Before preparation, validate `studio.recording_backend` against the
typed `host | reploy` domain, default omission to `reploy`, route `reploy` to
the controlled-session controller, and reject explicit `host` with the targeted
capability error until a bare-metal controller is deliberately introduced. For
fatal Envoy outcomes, including continuation-watermark timeout and
`TIOCSWINSZ`, operation-cleanup, and inspection-cancellation failures, retain a
bounded explanation and partial artifacts and log the Reploy termination
request and result.

Gate: controller, codec, staging, and blueprint tests pass independently before
the terminal runner consumes them.

### D. Terminal-only integration milestone

**D1. Envoy-backed terminal runner**

Adapt `PersistentTerminalRunner` to the Envoy session while preserving command
status, cwd, input, resize, Ctrl-C, output policies, assertions, action gates,
produced outputs, ranges, and structured diagnostics. Add pre-cutover backend
routing without collapsing configuration presence: omission continues to use
the FIFO-backed host path, explicit `workload_backend=reploy` selects the
isolated path, and explicit `workload_backend=host` fails with the targeted
pre-cutover capability error. Keep the presence fact internal and out of the
Reploy blueprint, and do not make Reploy the default before the separately
reviewed host-Envoy parity and cutover gate. Prove PTY-attached assertions
without authored input against the exact post-line-discipline bytes treated as
logical stdout, including CRLF processing, without terminal-byte
normalization.

**D2. Direct terminal artifacts**

Write the private raw log, asciicast, and timeline directly from controller
presentation events plus Envoy terminal and telemetry events. Do not introduce
a controller-side PTY or `asciinema record` process. Prove that synthesized
prompt and displayed-command events remain distinct timeline events in authored
order and are not flattened into terminal output. Prove artifact synthesis with
fragmented terminal and telemetry bytes, fragmented and invalid UTF-8 at cast
boundaries, and terminal text that resembles protocol content; retain the exact
private raw bytes and never interpret terminal payload as protocol data.

**D3. Isolated Reploy end-to-end proof**

Run one internal demo or tutorial through a real isolated Reploy workload.
Exercise a curses application and one nested interactive shell while hostile
application `TERMINFO`, `TERMINFO_DIRS`, `$HOME/.terminfo`, `INPUTRC`,
`$HOME/.inputrc`, `LANG`, `LANGUAGE`, `LOCPATH`, and `LC_*` inputs cannot affect
either Reploy bootstrap-shell or controlled-Bash launch. Exercise nominal
capture plus startup failure,
controller cancellation, workload exit, terminal output-finalization failure,
controller artifact failure, result-delivery failure, acknowledgement failure,
cleanup failure, and recovery-action reporting, with repeated nominal runs for
race coverage. Prove the complete pre-cutover routing matrix, including
omission remaining distinguishable from the normalized `host` value and the
Reploy-backed workload path never becoming the default. Separately prove that
omitted and explicit `recording_backend=reploy` both select the Reploy
controller, while explicit `recording_backend=host` fails before preparation.
Across `real`, `suppress`, and `replace` presentation modes, prove incremental
realtime publication, buffered stdout-then-stderr publication after the logical
post-enter pause, compressed command wall time, and replacement publication
only after its `output_through` frontier.

Gate: terminal-only Linux conformance is green, resources are accounted for,
and retained artifacts and diagnostics match the contracts.

### E. Later delivery stacks

The terminal-only gate authorizes planning, not automatic implementation, of:

1. browser endpoint readiness and Playwright recording;
2. cast, media, diagnostics, and publication-candidate finalization;
3. clean-install packaging and public CLI integration;
4. host-workload Envoy connectivity and parity;
5. FIFO retirement; and
6. bootstrap, discovery, and blueprint refresh.

Each item receives a separate bounded plan or stack. Browser and publication
work must not be folded backward into the terminal-only slices.

The browser-readiness plan must require an operation-scoped `awsh` gate in the
trusted service launch path before controller endpoint probing can authorize
browser work. Its conformance cases distinguish that causal gate from the
separate pre-start stale-listener probe and post-gate endpoint health check, and
reject a temporal unready-to-ready transition without the current operation's
gate.

## Progress ledger

| Slice | State | Evidence |
| --- | --- | --- |
| A1 | Approval-gated | Workload Envoy design, protocol amendment, and delivery plan are independent bottom-up documentation slices; each exact current head requires its own review and approval evidence before B work |
| B1–B8 | Pending | Raw material only |
| C1–C8 | Pending | Raw material only |
| D1–D3 | Pending | Raw material only |
| E | Deferred | Requires terminal-only gate |

Update this table only from executed checks and current review state. Historical
PR labels and tests from superseded commits are context, not completion
evidence for the rebuilt stack.
