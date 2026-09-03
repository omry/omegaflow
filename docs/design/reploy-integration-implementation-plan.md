# Reploy Integration Implementation Plan

## Status

- Temporary delivery plan.
- Trusted implementation boundary: the rebuilt stack's base, the tip of
  `main`, which carries the formerly approved implementation stack. That
  stack's final pre-rebuild PR number, 8, has since been reused by an open PR
  in the rebuilt stack, so PR numbers are not boundary evidence; within the
  rebuilt stack, the `approved` label on a PR is. Node identities are not
  recorded here because every restack rewrites them.
- Updated: 2026-09-04.
- A2.6 is a fresh, unreviewed design-only worktree on the approved A2.5 base;
  no earlier A2.6 implementation, attestation, or approval is evidence.
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

**A2. Complete the external-Awsh amendment in bounded design-only slices**

- A2.1 fixes the external supervisor architecture and actor ownership.
- A2.2 fixes the shell-neutral Envoy/Awsh lifecycle boundary.
- A2.3 fixes Bash launch and readiness: the one-exec descriptor handoff,
  helper transport, process and controlling-terminal topology, termios
  readiness proof, terminal-control leases, and partial-launch cleanup.
- A2.4 fixes exact private source fields, helper IPC, canonical Bash framing,
  adapter-reserved state, Readline submission, `PS0`, positive helper markers,
  the post-`PS0` release signal and private `start_released` result,
  non-returning fail-stop behavior, split-entry proof, bounded helper-stream
  framing, and the public operation-start barrier.
- A2.5 fixes ordinary completion and persistent-state handoff: the
  completion-side Bash prompt hook and canonical pre-cleanup and final
  shell-neutral state reports;
  the direct-exec completion helper identity; exact private
  `input_close`, `input_closed`, and `completed` fields and ordering; immutable
  adapter-state and empty-job-table validation; completion-side
  state-bearing completion `prompt_ready` and Readline termios proof; saved
  source status plus final cwd/environment and inspection-plan handoff; and PTY
  plus split-stream output, keepalive,
  dual-EOF, and FIFO-removal barriers. Functions, aliases, positional
  parameters, unexported variables, and non-reserved options remain live in
  Bash rather than becoming wire state. The `CHLD`, `DEBUG`, `ERR`, and
  `RETURN` traps are adapter-reserved and must remain unset; non-reserved signal traps
  remain ordinary selected-Bash behavior and are preserved. The final
  completion `prompt_ready` carries the saved source status and recaptures live
  cwd, editing/history state, and exported environment after helper and cleanup
  child exits so Awsh uses current state for readiness, path resolution, and
  `completed`. This restriction applies to the selected persistent Bash, not a nested shell's
  own child-exit trap. Operation-created jobs must be terminated, reaped, and
  absent from the job table before completion is reusable. A2.6
  owns controls and crossed lifecycle races; A2.7 owns final private-schema
  closure.
- A2.6 fixes controls and crossed lifecycle races as one fresh design-only
  successor. Envoy is the sole owner of operation-lifecycle state, lifecycle
  deadlines, timeout-result selection, operation-process lifetime identity
  tracking and census, cleanup, and crossed outcomes; Awsh remains
  the selected-shell launcher/reaper and A2.5 completion handoff, with no
  duplicate lifecycle state machine. Running-operation cancel and finalize use
  one Envoy-owned `ioctl(PTY_MASTER, TIOCSIG, SIGINT)` on the retained PTY
  master; the kernel targets the current slave foreground group, and failure is
  fatal with no terminal operation result. A successful ioctl starts the one
  existing grace deadline. Envoy makes that target safe through its
  controlling-terminal session and pidfd-backed census invariant: before the
  ioctl it samples the foreground group with
  `ioctl(PTY_MASTER, TIOCGPGRP, &foreground_pgid)`, requires a positive live
  group, and validates every `/proc` member against the controlled session ID
  and pidfd-backed controlled-tree census, while every group
  eligible to become foreground is persistent Bash, an adapter helper, or a
  current-operation descendant. The kernel atomically targets the then-current
  group within that session; inability to prove the invariant is fatal before
  a signal or terminal result. A cancel accepted during ordinary-return cleanup
  sends no signal, finishes the existing cleanup deadline, skips inspection,
  and emits `operation_cancelled`; the inspection worker race applies if the
  worker is already running. A finalize accepted after `input_close` is
  observed-return-wins and stays Envoy-local, while cancel crossing finalization
  before `input_close` changes the intended result without a second ioctl or
  timer reset. Resize is serialized wholly by Envoy around its output frontier
  and direct `TIOCSWINSZ`.
  The only new narrow Awsh control is the gate helper. The fixed rcfile installs
  a readonly `awsh` function that accepts exactly `awsh gate GATE_ID` and invokes
  `/omegaflow-runtime/bin/awsh bash-helper --socket=/run/omegaflow/session/bash/helper.sock gate GATE_ID`
  without application-`PATH` lookup. Awsh reports `gate_ready`, writes a
  successful `continue` reply completely through the exact stream-write loop
  before committing `gate_continued`, and retries positive short writes under
  one non-resetting five-second reply deadline. Peer close, terminal write
  error, or deadline expiry before the complete reply emits exactly one
  `gate_interrupted` outcome.
  Envoy's serialized acceptance is the public winner: if the gate outcome is
  accepted first, publish `operation_continued` or
  `operation_gate_interrupted`, then apply a later lifecycle request from
  `Running`; if a lifecycle request is accepted first, do not send an unsent
  `continue`, and if it was already sent consume Awsh's exactly-one outcome
  without public gate telemetry while continuing from `Cancelling` or
  `Finalizing`. The selected persistent Bash reserves `INT`; recorded top-level
  source cannot install or change its trap. Completion may temporarily ignore
  `INT`, then restores the one canonical unset state with reserved
  `builtin trap - INT` and requires `builtin trap -p INT` to be empty. The
  direct-exec completion `prompt_state` helper inherits the temporary ignored
  disposition and its exec entry and runtime preserve `SIGINT` ignore while it
  blocks through Envoy's acceptance of `input_close` and matching
  `input_closed`; cancel and finalize cases in that interval must
  preserve Bash/helper survival, consume `input_close` as the existing A2.5
  return fact, and complete the already-selected lifecycle outcome. Nested
  shells and ordinary child programs retain normal signal handling. Public
  telemetry fields remain unchanged.
- A2.7 closes all private schemas and establishes the exact B1 base.

Gate: each A2 slice is a design-only successor of the preceding approved slice
and must complete deep design review, current-document attestation, required
checks, and exact-head PR approval before its successor is published. No B
implementation starts until A2.7 is approved.

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
Freeze every supported Bash-build entry's exact zero-to-4,096-byte startup PTY
string and `ready.output_through`. Cover fragmented and delayed startup bytes,
cross-connection delivery before `ready`, zero and maximum-size entries,
mismatch, extra byte, overflow, premature EOF, and an incomplete barrier. Prove
the raw range begins at offset zero as `pty` at elapsed time zero and contains
no real Bash prompt byte.
Freeze the exact A2.4 private frames and helper messages, canonical brace frame,
independent source/frame syntax checks, source and aggregate bounds, the hard
post-source LF boundary, `0x18 0x01` loader and `0x18 0x02` submit bindings in
the fixed idle keymap, command-substitution marker, source rejection codes, and
one non-resetting operation-start deadline. Prove the canonical suffix performs
no command lookup when source defines a function named `:` or disables the `:`
builtin, and preserves zero and nonzero status under source-enabled `errexit`.
Freeze readonly `trap` and `enable` mediation for the adapter-sensitive
`CHLD` (`SIGCHLD`), `INT` (`SIGINT`), `DEBUG`, `ERR`, and `RETURN` traps and the
exact adapter-required builtin set. Before deciding whether a target is reserved,
the readonly mediation must canonicalize every selected-Bash `signal_spec`
after expansion using the selected Bash build's case-insensitive signal-name
grammar and signal-name table, including aliases and decimal values, so the
selected build's numeric `SIGCHLD` or `SIGINT` spelling cannot bypass the
reservation; no portable hardcoded numeric constant is assumed. Queries and other numeric
trap mutations retain ordinary selected-Bash behavior. The `CHLD` reservation
prevents an adapter-owned helper child exit from asynchronously running a
selected-shell `CHLD` trap and mutating persistent shell state across adapter
boundaries. Recorded top-level source cannot install or change the selected
persistent Bash's `INT` trap. The completion hook may temporarily ignore `INT`
during its helper and cleanup window, then must restore the one canonical unset
state with reserved `builtin trap - INT` and require `builtin trap -p INT` to
be empty. Nested shells and ordinary child programs retain their own normal
signal handling. Prove
prohibited direct and expanded-argument mutations fail-stop before state
changes, including direct and expanded `CHLD`/`SIGCHLD` and `INT`/`SIGINT`
spellings, lowercase names, aliases, and direct and expanded decimal spellings
of the selected build's numeric values (17 and 2 on the supported Linux builds),
`trap 'exit 42' DEBUG`, `trap 'exit 42' CHLD`,
`trap 'cd /tmp' SIGCHLD`, and `enable -n kill`. Also prove
dynamic load or replacement and dynamic unload cannot target a required
builtin, and that recorded source cannot redefine or unset the readonly `awsh`
function. Cover combined options, multiple names, and mixed reserved/non-
reserved targets, with complete post-expansion argument preflight before any
partial mutation. Reserved-state queries, including numeric `SIGCHLD` and
`SIGINT` queries, and positive enablement remain allowed, while trap changes using other
numeric signals and every operation on non-reserved traps and builtins preserve
ordinary selected-Bash behavior.
A nested Bash case proves that the child shell may install and own its own
`CHLD` and `INT` traps without weakening the selected-shell reservation; an
ordinary child program may install its own signal handler. Gate-command cases
prove that bare `awsh gate GATE_ID` reaches only the fixed absolute helper
despite a hostile application `PATH`; explicit function bypass is an ordinary
authored command, not a structured gate.
A nominal completion case must also prove that helper and cleanup child exits
after `prompt_state` leave the final state-bearing `prompt_ready`, reported, and
live cwd/exported environment aligned, including when an allowed non-reserved
signal trap changes them during cleanup.
Classify explicit builtin-lookup bypass as fatal same-identity interference
rather than a supported operation.
Cover minimum and maximum source,
multiline source,
comments, quotations, heredocs including an unterminated heredoc whose selected
Bash checker warns while returning zero, zero status plus empty stdout and
stderr as the success condition for both parse checks, trailing LF,
parser-state-dependent rejection, fixed interactive-comment handling, reserved
names and input sequences including adjacent-step boundaries, duplicate and
crossed helper phases,
mismatched IDs, no-redisplay behavior, and failures on both sides of public
`operation_started`. Freeze the source-loader and `PS0` positive success
markers, the adapter-owned post-`PS0` signal and private `start_released`
result, argument-free fail-stop mode, split `INNER_ENTERED` sentinel, and
the post-sentinel restoration that makes the first authored expansion or
command observe the exact preceding shell status. Freeze the Linux
helper-stream length prefix, half-close, and EOF boundaries. Cover partial helper
output, malformed or missing markers, nonzero exit, signal and disconnect;
stdout and stderr redirection-open failures versus authored status 1; split
setup and rollback under the original start timer; fragmented and short
prefixes and payloads, zero and oversized lengths, trailing bytes, ancillary
data, deliberately small socket buffers, and exact maximum request and reply
payloads. Cover cancellation before the first private `execute` byte, while
`rejected` or `submit` is pending, after public start but before
`start_released`, and after every later private-start phase; prove a committed
start publishes `operation_started` and accepts `start_released` before
ordinary cancellation and never abandons a loaded frame or adapter helper.
Freeze the exact A2.5 ordinary-return frames and helper reports: completion
`prompt_state` with `STATUS`, `HISTEXPAND`, `EDITING_MODE`, `PHYSICAL_CWD`,
`LOGICAL_CWD_OR_EMPTY`, and `EXPORTED_ENV_JSON`;
startup no-state `prompt_ready` and completion state-bearing `prompt_ready`
with the saved source `STATUS` plus final `HISTEXPAND`, `EDITING_MODE`,
physical/logical cwd, and exported environment; Awsh `input_close` with the
active operation ID and exact direct-exec helper PID; Envoy `input_closed` with
only the operation ID; and Awsh `completed` with status, physical cwd, and
`RESOLVED_INSPECTIONS_JSON`. Freeze their ordinary-return ordering, the exact
completion-helper cleanup exclusion, post-cleanup `wait`/empty-job-table proof,
repeated adapter validation, final-state validation and use by Awsh, Readline
termios transition, and final PTY drain.
Prove functions, aliases, positional parameters, unexported variables, and
non-reserved options remain live in Bash without entering a helper or private
frame. Cover malformed and duplicate completion reports, stale or extra helper
PIDs, early helper release, stale status/cwd/environment, and each PTY and split
output/EOF boundary. Leave cancellation, finalization, gates, and crossed
lifecycle cases to A2.6 and final private-schema closure to A2.7.

**B2. Awsh boundary alignment**

Align execution-policy framing, persistent Bash state, inspection-path
resolution, and descriptor non-inheritance with the amended protocol. Awsh
must retain no PTY-slave descriptor after readiness; every later shell-side
terminal operation uses the bounded terminal-control lease fixed by A2.3.
Do not add an Awsh lifecycle or resize state machine, lifecycle-deadline or
timeout-result selection, process-census, cleanup, or cancel/finalize
machinery: Envoy owns operation-process lifetime identity tracking and census,
all lifecycle decisions, direct PTY signal/resize ioctls, lifecycle deadlines,
timeout-result selection, and crossed outcomes. Awsh retains only selected-
shell and completion-helper identity validation, selected-shell launch/reaping,
the A2.5 completion handoff, and the narrow gate-helper exchange, including its
one local gate-reply transport deadline.
Implement Awsh's exact one-exec descriptor intake, digest-selected generated
Bash-build-table consumer, fixed rcfile/helper startup exchange, empty primary
prompt, signal reset, process/session/foreground topology, Readline termios
proof, first terminal drain, private readiness, selected-shell reaping, and
partial-launch cleanup. Implement the A2.4 source checker and private active
operation record, parent-side `SIGUSR1` reception installed before Bash launch,
fixed helper request/reply arities, canonical source-frame
emitter, readonly adapter namespace, canonical parser/trap/trace/job-control
entry state, whole-request readonly trap/builtin mediation, reserved top-level
`INT`, fixed-keymap
loader/submit Readline macro, output-empty blocking
`PS0`, private source capture followed by positive-marker validation, the
manifested non-returning `bash-fail-stop` mode, source-visible
status/history/editing restoration, validation and canonical redirection of
split FIFO paths, the split-entry sentinel, the readonly first-command
post-`PS0` signal to the direct Awsh parent, and fail-closed start phases. Set
the fixed four-byte big-endian length framing on every helper request and reply,
require request half-close and reply EOF, reject ancillary data and trailing
bytes, and use exact bounded stream read/write loops under the existing phase
deadline. Implement the completion-side prompt hook and its pre-cleanup
`prompt_state` snapshot before `input_close`; direct-exec and identify the one
completion helper; have Awsh send `input_close` with only the active operation ID
and exact helper PID; validate adapter-reserved state before and after Envoy
cleanup; use the reserved `wait` and `jobs` builtins to require an empty job
table; and preserve returned status, cwd, exported environment, functions,
aliases, positional parameters, unexported variables, and non-reserved options
without serializing the Bash-only state. After cleanup, wait-record removal, and
adapter validation, the completion hook must carry the saved source `STATUS`
and recapture `HISTEXPAND`, `EDITING_MODE`, physical/logical cwd, and exported
environment in state-bearing `prompt_ready`; Awsh validates and uses that state
for readiness and path resolution without altering non-reserved signal traps,
then recaptures the complete post-cleanup pre-Readline termios state, proves
Readline re-entry against that fresh state, and sends `completed`.
Before the helper snapshot, the completion hook must validate the four unset
adapter-sensitive traps `CHLD`, `DEBUG`, `ERR`, and `RETURN`, plus the required
temporary ignored `INT` disposition. The direct-exec `prompt_state` helper
inherits that ignored disposition across fork and exec; its exec entry and
runtime preserve it while the helper blocks through Envoy's acceptance of
`input_close` and matching `input_closed`.
This exception is limited to the completion helper; gate and all other helpers
retain their existing signal behavior.
After helper/cleanup child exits, the hook must restore canonical unset `INT`
with reserved `builtin trap - INT`, verify `builtin trap -p INT` is empty, and
validate all five adapter-sensitive traps as unset. Repeat the
selected-build Readline termios proof before Awsh sends `completed` with the
saved source status, cwd, and resolved inspection plan.
It must keep the selected persistent Bash's reserved top-level `INT` trap
unset. The completion hook saves status, may ignore `INT` only for its
helper/cleanup window, restores canonical unset with reserved
`builtin trap - INT`, and verifies `builtin trap -p INT` is empty before final
validation. Fixtures cover direct, expanded, case-insensitive, alias, numeric,
mixed, and multiple-target mutation rejection; allowed queries; temporary
ignore and canonical restore; and normal nested-shell and child-program signal
handling.

**B3. Envoy session foundation**

Implement listeners, the controller-generated session-ID handshake, the
independent actor-local connect/hello/ready deadlines, one PTY, persistent
Awsh/Bash startup, shared PTY execution, exact byte relay, bounded control
writes, an empty `HISTFILE` for controlled Bash after application
environment delegation, and orderly shutdown. Own the exact Awsh exec handoff,
startup-output pump and 4,096-byte cap, build-entry comparison, complete public
`ready` write before terminal release, `ready.output_through`, and takeover of
incomplete launch cleanup. Own `execute.input_through` before private submit,
start the one operation-start timer before any split directory/FIFO setup, own
bounded rollback of every partial setup, mode-0600 split FIFO creation and
reader/keepalive ownership, the serialized
internal `0x18 0x02` PTY write, the fresh pre-start drain and mark,
`start_release`/`started` ordering, complete public `operation_started` before
`started_ack`, acceptance of private `start_released`, and the operation-start
deadline and teardown. Serialize cancel with the first attempted private
`execute` byte: retain later cancellation, allow `rejected` to commit pre-start
failure, and after `submit` finish public start and wait for `start_released`
before Envoy applies the ordinary started-operation cancellation path. The path
issues one `ioctl(PTY_MASTER, TIOCSIG, SIGINT)` directly on the retained PTY
master; a successful ioctl starts the existing grace deadline and a failed
ioctl is fatal with no terminal result. It uses the exact B4
controlling-terminal session and pidfd-backed foreground-group invariant; an
unprovable boundary is fatal before the signal. Queue a cancel first accepted between
public start and `start_released` under the same rule; it is never forwarded to
an Awsh lifecycle transaction.
For ordinary return, sequence the `input_close` proposal/timer boundary with
only the active operation ID and exact completion-helper PID; permanently close
operation input, terminate live authored descendants and reap adopted children
while preserving only the exact completion helper and descriptor-free Bash wait
records, close both split writer keepalives, drain both split readers to
independent EOF, remove the FIFOs, and send `input_closed`. Only then does Awsh
release the blocked `prompt_state` helper; Bash clears its own wait records with
reserved `wait`, proves the empty job table and adapter state, recaptures the
final state in state-bearing `prompt_ready`, and Awsh validates it and resolves
paths before the terminal-control handoff and Readline re-entry. Awsh then sends
`completed` with the saved source status, physical cwd, and resolved inspection
plan. The one non-resetting five-second operation-cleanup deadline ends only after Envoy has
proved the final census and performed the fresh PTY drain/output-through
barrier after `completed`; workload inspection runs afterward under the
controller-owned operation deadline and its existing inspection-cancellation
timeout. A failure at any cleanup, helper, readiness, EOF, drain, or removal
step is fatal and emits no terminal operation result.

**B4. Operation boundaries and controls**

Implement output barriers, completion, input, resize, cancellation, action
gates, planned finalization, and final drain. Linearize every accepted resize in
the Envoy output pump, close and carry its preceding `output_through` frontier
across the PTY before Envoy performs
`ioctl(PTY_MASTER, TIOCSWINSZ, winsize{columns, rows})`, and acknowledge it with `resize_applied`
only after the resize is applied. Cover queue-order ties, PTY output
immediately preceding a resize, and a continuously writing PTY
workload. Cover a resize accepted while `execute` remains in
`Starting` across `operation_started` and the replacing pre-start failure,
cancellation, or drain; a drain that resolves the request before
`resize_applied` emits no applied-resize telemetry. Also cover delayed
`execute.input_through` with a resize across each outcome. From
`operation_started` through the terminal event, preserve each accepted
resize's covered PTY prefix until it is applied. Carry the cumulative
terminal-input watermark on each
`continue` and keep the gate closed until the Envoy has received those bytes;
cover delayed terminal input and cancellation while waiting. A continuation
watermark timeout must take fatal session teardown with no terminal operation
result or private gate-abort mechanism. Add the active split-stream resize
frontier equivalent with B5. Cover both
resize/shell-end race outcomes: `resize_applied` resolves a resize applied
before drain, while `draining` resolves a superseded outstanding resize without
reporting it as applied. Prove that a shell-ended drain crossing both an unstarted
`execute` and its deadline-derived `cancel` resolves both requests with no
terminal operation result and leaves the planned beat to fail as unrunnable.
Make every failed `TIOCSWINSZ` fatal to the session in idle and active-operation
states: emit best-effort `resize-failed`, no `resize_applied` or terminal
operation result, close the channels, and exit nonzero. Before any terminal
operation result, drain the PTY bytes and emit their covering mark.
For running cancel and finalize, keep the operation-lifecycle decision,
lifecycle deadline, timeout-result selection, operation-process lifetime
identity tracking and census, cleanup, and every
crossed outcome in Envoy. After `start_released`, issue exactly one
`ioctl(PTY_MASTER, TIOCSIG, SIGINT)` on the retained PTY master; the kernel
targets its current slave foreground group, a successful ioctl starts the
existing grace deadline, and a failed ioctl is fatal with no terminal result.
Before the ioctl, require Envoy's controlling-terminal session and
pidfd-backed census invariant to prove that every group eligible to become
foreground consists only of persistent Bash, an adapter helper, or a
current-operation descendant. Envoy must sample the live group with
`ioctl(PTY_MASTER, TIOCGPGRP, &foreground_pgid)`, require a positive live group,
and validate every `/proc` member against the controlled session ID and
pidfd-backed controlled-tree census; the kernel then atomically targets the then-current group in
that session. An unprovable boundary is fatal before any signal or terminal
result. Cover a clean pre-operation census, nested foreground programs, a
foreground switch among controlled groups between `TIOCGPGRP` and `TIOCSIG`,
and fatal no-signal outcomes for an unexpected member, wrong session, dead or
unclassifiable group, and unprovable clean boundary. Do not add an Awsh lifecycle signal or resize transaction. Prove that
`input_close` and `completed` remain the A2.5 shell-local return facts, that a
cancel during ordinary-return cleanup wins without a second signal or timer
reset and skips inspection, that a finalize after `input_close` is
observed-return-wins, and that `shell_exit` before timeout selection wins while
the later shell exit after timeout selection is only reap evidence.
Add explicit completion-boundary cases that inject `cancel` and `finalize`,
respectively, after the direct-exec `prompt_state` helper has connected and
Awsh has validated its identity and report but before Envoy accepts the
matching `input_close`. Require persistent Bash and the blocked helper to
survive the existing Envoy signal path with the helper's inherited ignored
`SIGINT` disposition unchanged; then require Envoy to consume `input_close` as
the A2.5 return fact and complete the already-selected cancellation or
finalization outcome without a second signal, lifecycle owner, or frame shape.
Implement the narrow gate helper without an Awsh lifecycle state machine. The
fixed rcfile installs a readonly `awsh` function accepting only
`awsh gate GATE_ID`; it invokes
`/omegaflow-runtime/bin/awsh bash-helper --socket=/run/omegaflow/session/bash/helper.sock gate GATE_ID`
without application-`PATH` lookup and blocks. Awsh reports `gate_ready`, Envoy
sends `continue` only after the input watermark, and Awsh commits
`gate_continued` only after the complete success reply write. Use the exact
stream-write loop under one non-resetting five-second reply deadline beginning
with the first attempted byte: retry positive short writes, and emit exactly one
`gate_interrupted` only when peer close, terminal write error, or deadline
expiry prevents the complete reply. If the private
gate outcome is accepted first, Envoy publishes the corresponding
`operation_continued` or `operation_gate_interrupted` event and a later
lifecycle request applies from `Running`. If a lifecycle request is accepted
first, Envoy does not send an unsent private `continue`; if it was already sent,
Envoy consumes Awsh's exactly-one outcome without public gate telemetry while
continuing from `Cancelling` or `Finalizing`. Cover all of those crossings and
prove no acknowledgement repair loop or private state-machine regression.

**B5. Split execution**

Implement separate stdout/stderr supervision, ordered terminal forwarding,
sender-stamped output marks, and split-stream conformance. Extend B4's active
operation resize frontier across every split stdout/stderr source before
`TIOCSWINSZ`. Use `output_through` as a covered prefix for each logical stream
through acknowledgement. Cover a long-running operation whose resize arrives
before any split-stream prefix is covered. Before any terminal operation
result, close both writer keepalives only after descendant cleanup, observe
independent EOF on both operation split pipes, remove their paths only after
both EOFs and reader closure, drain the pipe and PTY bytes, and emit their
covering marks. Prove the sender-marked ranges preserve
the complete exact logical stdout and stderr byte sequences under interleaved
presentation; do not normalize, merge, or reorder those retained inputs to
match presentation order.

**B6. Process cleanup and exclusive observation**

Implement fail-closed exclusive evidence ranges for checked, suppressed,
replaced, and presentation-timed operations. Reject `output_contains` and
`output_regex` at plan compilation when an interactive operation or any of its
continuations sends bytes through `text`, `key`, or `control`; retain
`wait_for` as visible-terminal synchronization rather than assertion evidence.
Prove that planned recording-end finalization closes the intentionally open
operation's output range and returns the completed workload status and distinct
finalization outcome needed by controller-owned assertion evaluation; do not
present synthetic termination status as workload exit status. Prove that
finalization failure and user cancellation return failure or cancellation
outcomes that invalidate the range for assertion evaluation. Envoy remains the
sole lifecycle owner: before `input_close`, a running cancel or finalize issues
one `ioctl(PTY_MASTER, TIOCSIG, SIGINT)` on the retained PTY master and starts
the existing grace deadline only on successful ioctl; no Awsh lifecycle signal
or resize transaction is added. A failed ioctl is fatal with no terminal
operation result, and the exact B4 foreground-group invariant is required before
the signal.
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
the result to `operation_cancelled` after timely return to the selected shell's
backend boundary and cleanup, or to `cancel-timeout` with `shell_ended: true`
on expiry. Prove
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

Resolve configured paths in persistent Bash state from the final
state-bearing `prompt_ready`, perform bounded workload existence/type/hash
inspection in Envoy only after the universal operation cleanup and final
output-through barrier, and return private typed results without controller
filesystem access or probe commands. The five-second operation-cleanup
deadline ends after `completed`, the final census, and that output barrier;
inspection then runs under the controller-owned operation deadline. Run the
resolved plan in a short-lived,
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
For ordinary selected-shell exit, require one complete terminal `shell_exit`,
no later `closed`, private EOF, and a zero-status Awsh reap. Prove that premature
EOF, a trailing result frame, reset, signal, or nonzero Awsh exit remains fatal
after either terminal result. A processed controller-requested shutdown instead
requires one terminal `closed`, private EOF, and a zero-status Awsh reap.
For that terminal reap, prove the existing timer mapping: an active-operation
`shell_exit` remains under the Envoy operation-cleanup deadline, while an idle
`shell_exit` or `closed` remains under the already-running Envoy final-drain
deadline. A terminal result starts or resets neither timer and leaves both
existing five-second budgets and failure mappings unchanged.
Cover both orderings of `shutdown` crossing an idle persistent-shell exit:
observed shell exit first sends terminal `shell_exit` and resolves
`ShutdownSent` through `shell_ended`, while accepted shutdown first preserves
the requested shutdown reason whether Awsh sends `closed` or a crossed terminal
`shell_exit`.

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
fixtures, including inspection results and cross-channel output barriers. Its
readiness path buffers bounded terminal bytes received before public `ready`,
appends them only after validating `ready.output_through`, and permits the
first planned prompt or request only after the raw log reaches that barrier.
Validate source UTF-8, size and reserved namespace plus authored terminal input
against both reserved Readline sequences before `execute`; keep
`execute.input_through` controller-local and require the exact public
`operation_started` barrier before operation-authored terminal input.

**C4. Runtime build artifact**

Add reproducible platform builds and the manifest for Envoy, Awsh, and their
required runtime files. Generate host preparation, Envoy, and Awsh consumers
from one canonical digest-keyed Bash-build table containing the system rc path,
startup-export transform, catchable-signal inventory, startup Readline behavior,
source-loader/submit-macro keymap, no-redisplay, UTF-8 cursor, and maximum-line
behavior, and exact bounded startup PTY bytes. Build and manifest the fixed
`etc/awsh-bashrc` with
its output-empty primary-prompt and `PS0` hooks, readonly adapter namespace,
canonical parser state, whole-request readonly `trap` and `enable` mediation,
private bindings, positive helper markers, split-entry sentinel, post-`PS0`
release signal, and readonly fail-stop wrapper. Build the
same manifested `bin/awsh` with its fixed socket-helper requests, bounded
stream-framing validation, and
argument-free non-returning `bash-fail-stop` mode, plus the fixed empty
`etc/inputrc`.

**C5. Runtime staging**

Materialize a manifest-validated read-only `/omegaflow-runtime` tree without
blueprint composition or controller execution. Prove staging rejects missing or
additional payloads, symlinks, special files, unreadable files, escaping or
duplicate paths, invalid modes, size or digest mismatches, and malformed or
additional executable and script payloads, including every trusted terminal,
Readline, locale, and Bash rcfile asset named by the runtime manifest. Prove
the rcfile installs the required startup hooks without sourcing another file
and preserves the output-empty primary-prompt and start-barrier invariants.
Prove its literal helper path, socket, request names, bindings, readonly state,
canonical frame and whole-request reserved-state mediation functions, positive
source/start markers, post-`PS0` release signal, split-entry sentinel, and
fail-stop invocation match the frozen fixtures. Prove the exact staged Awsh
binary passes the stream-framing and non-returning fail-stop fixtures. Prove
the host copies only verified installed artifacts into a fresh
private directory, writes the manifest last, makes the staged tree
non-writable, and never assembles it from a project checkout.

**C6. Blueprint schema and composition**

Add typed controller/workload blueprint models, Hydra composition, read-only
controller configuration, resolved YAML retention, and fixture conformance.
Reject the complete normative launch-control environment enumeration before
materialization, including application-provided `HISTFILE` and `INPUTRC`.
After application composition, add only the reserved launch values: an empty
`HISTFILE`, `INPUTRC=/omegaflow-runtime/etc/inputrc`, `TERM=xterm-256color`,
both `TERMINFO` and `TERMINFO_DIRS` set to
`/omegaflow-runtime/share/terminfo`, `LC_ALL=C.UTF-8`, `LANG=C.UTF-8`, and
`LOCPATH=/omegaflow-runtime/lib/locale`. Re-materialize the final launch
environment and require those exact values, every other exact forbidden name
to be absent, no `BASH_FUNC_`, `LD_`, or `AWSH_` prefix, and no `LC_` name
except the reserved `LC_ALL`. Prove neither application `HISTFILE` nor the
default under its `HOME` can block Bash before OmegaFlow types the Envoy
bootstrap command. Prove the final blueprint cannot shadow the trusted
terminal, Readline, or locale tree and fails before controlled Bash starts if a
required mounted asset is missing, non-regular, unreadable, or mismatched.
Resolve and hash `/bin/bash`, reject an unsupported build or present declared
system rc path, and require the exact generated build-table entry. Compose the
reserved `omegaflow_session_runtime` environment mount after the application
at writable target `/run/omegaflow` with `update_policy: preserve`, extend it
with the same-named Docker `tmpfs`, retain that effective plan, and reject a
missing, read-only, overridden, differently materialized, or overlapping mount.

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
capability error until a bare-metal controller is deliberately introduced.
Also preflight the complete first-release controlled-session boundary before
preparation or capture: require a Linux host using Docker, a Linux `amd64` or
`arm64` controller image, exactly one attachment, no reconnect, and no
configured private environment on either controller or workload deployment;
reject every unsupported selection with a targeted capability error. For fatal
Envoy outcomes, including continuation-watermark timeout and
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
reviewed host-Envoy parity and cutover gate. Prove newline-sensitive
`stdout + stderr` assertion decoding from the exact logical stream bytes under
interleaved presentation; do not normalize, merge, or reorder the assertion
inputs to match their presentation order. Prove PTY-attached assertions without
authored input against the exact post-line-discipline bytes treated as logical
stdout, including CRLF processing, without terminal-byte normalization. At
planned recording end, evaluate completed non-exit assertions over the closed
output range, do not use synthetic termination status to satisfy or fail an
authored exit-code assertion, and invalidate assertions on finalization failure
or user cancellation.

**D2. Direct terminal artifacts**

Write the private raw log, asciicast, and timeline directly from controller
presentation events plus Envoy terminal and telemetry events. Do not introduce
a controller-side PTY or `asciinema record` process. Prove that synthesized
prompt and displayed-command events remain distinct timeline events in authored
order and are not flattened into terminal output. Prove artifact synthesis with
fragmented terminal and telemetry bytes, fragmented and invalid UTF-8 at cast
boundaries, and terminal text that resembles protocol content; retain the exact
private raw bytes and never interpret terminal payload as protocol data.
Classify each resize when the controller authors it. A request sent during a
synthesized prompt or typing span retains that tag through acknowledgement and
publication: use the then-current frontier if its acknowledgement is dequeued
before the span closes, otherwise use the final prompt-and-typing frontier.
Treat a resize accepted while `execute` remains in `Starting` as part of the
preceding prompt-and-typing closing seam and publish a matching applied resize
at the final typing frontier after `operation_started` or the replacing
pre-start failure, cancellation, or drain; publish no resize when drain resolves
the request before `resize_applied`. From `operation_started` through the
terminal event, classify every resize accepted during a presentation-timed
operation as part of that operation's authored span, including before schedule
commitment, and publish it after the latest authored output event covered by its
`output_through` frontier. Cover queue-order ties, zero-duration schedules,
acknowledgement delayed until after schedule commitment, delayed
`execute.input_through` without leaking the wait into the cast, PTY and active
split-stream output immediately preceding a resize, and continuously writing
workloads. For split stdout/stderr, publish the resize after the latest authored
event derived from every covered prefix. When raw interleaving makes both sides
of the frontier impossible to preserve, require authored order to win: once
stderr is covered, all stdout precedes the resize and only uncovered stderr
remains after it. Cover a long-running operation whose resize arrives before
any authored event is committed, plus stderr-before-resize-before-stdout and
stdout-before-resize-before-stderr schedules. Require the controller's raw-log
writer to reach the covered frontier before publishing the resize event.

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
| A1 | Approved prefix | PRs 23 through 25 are approved at their exact current heads |
| A2.1–A2.2 | Approved prefix | PRs 30 and 31 are approved at their exact current heads |
| A2.3 | Approved prefix | PR 33 is approved at its exact current head with current A2.3 attestations |
| A2.4–A2.5 | Approved prefix | Approved design predecessors; their contracts remain the base for the fresh A2.6 successor |
| A2.6 | Unreviewed worktree | Fresh design-only successor on approved A2.5; requires deep review, current attestation, green checks, and exact-head approval |
| A2.7 | Pending | Final private-schema closure after A2.6 approval; no implementation starts before this gate |
| B1–B8 | Pending | Raw material only |
| C1–C8 | Pending | Raw material only |
| D1–D3 | Pending | Raw material only |
| E | Deferred | Requires terminal-only gate |

Update this table only from executed checks and current review state. Historical
PR labels and tests from superseded commits are context, not completion
evidence for the rebuilt stack.
