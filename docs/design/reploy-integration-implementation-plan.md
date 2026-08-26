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
fixtures nor an external Awsh supervisor. The later Bash-resident prototype and
protocol implementation in the current stack predate the external-supervisor
amendment and are rewrite material, not an implementation dependency or
approval baseline. B1 and B2 schedule the conforming replacements. The trusted
base also contains no production Envoy or Reploy integration.

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
9. Envoy owns the PTY master, controller protocol, input/source/resize
   ordering, process-tree policy, and final drain. One external Awsh
   supervisor directly parents and reaps persistent Bash, owns the private
   selected-shell adapter boundary, serializes the requested resize ioctl with
   adapter termios work, and performs a cancellation interrupt in
   the same serialized action that classifies source execution. Non-Bash
   adapters remain deferred.

## Review discipline

- Start the rebuilt stack from the trusted base: the tip of `main`.
- Keep at most three unapproved PRs live at once.
- Once a PR is approved, do not rewrite it. Corrections go into a successor PR
  and receive their own review.
- Give each PR one contract or production subsystem responsibility.
- Target fewer than 800 handwritten changed lines. Split a PR before 1,200
  handwritten changed lines unless the excess is mechanically generated or a
  focused golden-fixture corpus.
- A1 and its documentation-only A2 successor are exempt from that limit by
  owner decision: each contract reconciliation
  stays one coherent PR however many review rounds grow it, because splitting a
  single self-consistent specification across review units would fragment it
  without adding review value. The line caps above bind the implementation
  slices, which is where reviewable size actually controls risk.
- Do not combine design, runtime packaging, controller lifecycle, terminal
  adaptation, browser work, or publication work merely because raw material
  previously placed them in one commit.
- Run focused tests and the relevant broader suite before review. Advance the
  stack bottom-up only when CI is green and review feedback is resolved.
- A passing partial implementation is recorded as partial. Removing or
  narrowing a claim is not evidence that the underlying behavior exists.

## Delivery sequence

### A. Design delta

**A1. Freeze the rebuilt plan and protocol amendment**

- Add this implementation plan.
- Add bounded workload inspection to Envoy protocol v1 and its private Awsh
  boundary.
- Replace controller-timed output evidence with sender-stamped output marks, so
  stream identity and timing travel as offsets rather than as a second copy of
  the output bytes.
- Replace the obsolete embedded implementation plans in both future design
  documents with concise phase summaries and a link here.
- Correct status language so approved work, raw material, and remaining work
  are distinguishable.

Gate: one documentation-only PR is reviewed again and approved before code is
extracted from the raw stack.

**A2. External Awsh supervisor amendment**

- Record the proven `Envoy -> external Awsh -> persistent Bash` process tree in
  all four authoritative documents.
- Keep Envoy as the sole PTY-master writer and preserve the controller-facing
  terminal and telemetry request shapes; add the one typed
  `operation_gate_interrupted` event needed to leave a gate after terminal
  Ctrl-C without inventing lifecycle cancellation.
- Amend the private protocol for Awsh/Bash identity, cooperative PTY source
  submission, start acknowledgement, source rejection, explicit shell exit,
  post-source terminal-input closure and slave-queue flush, shell-ended close,
  split-stream FIFOs, and orderly shutdown.
- Freeze the one-exec descriptor handoff, controlling-terminal launch sequence,
  private bounded Bash-helper packet protocol, exact submission-discard window,
  and operation-start deadline.
- Freeze delivery of the immutable Bash bootstrap through one manifested trusted
  rcfile, including the initial prompt-state/readiness handshake before private
  Awsh readiness.
- Generate one versioned Bash-build table into host preparation and Envoy from
  the same canonical digest-keyed source; record each build's compiled system-rc
  path and startup-export transform, require any declared path absent, and reject a
  prepared initial exported environment that cannot fit the helper bounds.
- Freeze the complete Bash state matrix, immutable hooks, and one byte-exact
  submission capsule: captured and temporarily disabled `histexpand`, reserved
  bracketed-paste begin and `C-J` acceptance bindings in both supported
  line-entry keymaps, a temporary byte-transparent termios delta, exact
  begin/frame/end/`LF` bytes, prompt-boundary validation before readiness,
  a blocking entry-sentinel handshake that proves Bash actually entered
  Readline, the complete generated real-signal trap inventory at Bash default,
  `ERR`/`DEBUG`/`RETURN` unset, and `xtrace` disabled before every normal helper
  spawn,
  pre-start rejection of the exact paste terminator in authored source, and
  independent fail-closed frame-entry and termios-restoration checks, including
  exact post-`submit`, pre-first-byte rollback with retained prompt state.
- Preserve Bash as the only initial backend while defining a private behavioral
  boundary that a future shell backend can implement without changing the
  controller protocol.

Gate: this documentation-only successor is approved at its current revision,
its required checks are green, and every changed design attestation binds to
the final reviewed bytes before B work resumes.

### B. Local Envoy and Awsh conformance

**B1. Protocol models and fixtures**

Deliver B1 as eight independently reviewable, topologically ordered sub-slices.
Every sub-slice must compile, carry focused tests for all behavior it adds, and
pass the runtime module's tests and static validation. The complete B1 corpus is
accepted only at B1h; an earlier sub-slice may introduce the exact fixture rows
needed for its own byte-level tests but may not claim complete conformance.

**B1a. Protocol foundations, inspection models, and path resolution**

Bootstrap the dependency-free Go module and its CI gate. Add the shared bounds,
stable protocol errors, validation primitives, execution and observation
vocabularies, and canonical JSON string handling required by later codecs. Add
inspection requests with deterministic request-order IDs, resolved plans,
typed results, aggregate bounds, exact optional-field behavior, and lexical
path resolution, including undefined variables and `~user`.

Gate: focused inspection, path, malformed, and boundary tests pass; every added
behavior is exercised; and no public or private session codec or digest corpus
is claimed by this slice.

**B1b. Digests, public vocabulary, and canonical encoding**

Freeze file-digest compatibility with the native runner, including symlinks and
nested special entries. Directory digests are deliberately not
native-compatible: freeze separately tagged native `directory` and protocol
`directory-v2` encodings. Add the bounded Controller-to-Envoy request and
Envoy-to-Controller event models, exact field sets and ordering, closed
failure-code set, terminal-input barrier, sender-stamped output marks, canonical
encoders, matching and mismatching handshake session IDs, and the typed
`operation_gate_interrupted` event.

Gate: focused digest, message-vocabulary, validation, and byte-exact encoding
tests pass; shell-sensitive JSON remains canonical; and no streaming decoder or
session state machine is claimed by this slice.

**B1c. Bounded public decoding and frame conformance**

Add exact JSON Lines decoders and fail-closed bounded stream decoding for every
public request and event. Enforce duplicate, unknown, missing, conditional, and
null field rules, complete-frame limits, sequence-independent frame validity,
terminal-message behavior, and fragmented input and EOF handling.

Gate: malformed, fragmented, oversized, terminal-frame, and EOF tests pass;
every public frame round-trips byte-exactly; and the complete public codec
accepts only messages allowed by the v1 schema.

**B1d. Session lifecycle core**

Add the serialized fail-closed session model for handshake, ordinary operation
phases, sequence and input watermarks, output marks and barriers, normal gate
continue/interruption, resize ownership, and ordinary terminal results. Cover
an earlier writer exiting with bytes still buffered, require the fresh exclusive
pre-start drain before `operation_started`, and freeze zero-byte boundaries for
first PTY and split operations and for first-operation failure or pre-start
cancellation. Model ordinary completion closing and quiescing the operation
input gate before a slave-queue flush and Readline re-entry, including a crossing
PTY write and later idle input. Require the initial `pty` boundary-only stream,
repetition of the current stream when no byte selected a new one, and a
same-offset `stdout` or `stderr` mark before the first split-stream byte.

Gate: all ordinary transitions and boundary rules introduced by this slice pass
focused tests without lifecycle crossing extensions, a private Awsh codec, or
production process supervision.

**B1e. Lifecycle crossings, drains, and inspection outcomes**

Complete the state model for crossed continue, cancel, and finalize behavior,
including pre-start cancellation on both sides of the source commit point,
ordinary completion winning a crossed cancel, gate interruption winners,
completion-input closure crossed by settled cancellation or finalization,
inspection result and failure constraints, resize crossings, shell-ended drain,
discarded late requests, retained cwd, and both winners of every specified race.

Gate: the complete transition matrix, shell-end and drain paths, inspection
outcomes, timeout mappings, and crossing tests pass without a private Awsh codec
or production process supervision.

**B1f. Terminal submission capsule and deadline ownership**

Add the doubled-source capsule maximum and exact private `submit` envelope.
Build both authored branches from byte-identical source, preserve the exact
conditional arguments and FIFO redirections, and accept a received capsule only
when it is byte-identical to the canonical generated form. Add every normative
startup, control-write, helper, operation-start, barrier, cancellation, cleanup,
resize, final-drain, and Readline-entry deadline owner and start epoch. Model
post-`submit` cancellation before byte zero, exact terminal-state rollback,
retention of the prompt snapshot for the next submission, and fatal rollback
failure. Include post-source input-close writer quiescence and slave flush in the
existing non-resetting Readline-entry and operation deadlines.

Gate: exact capsule, maximum-source, malformed-envelope, deadline-table, and
pre-write rollback tests pass; skeleton-only submission validation is rejected.

**B1g. Private Envoy/Awsh wire protocol**

Add exact bounded NUL-framed requests and results for Awsh/Bash identity,
`execute`, `submit`, `started`/`started_ack`, source rejection, gate readiness
and `gate_interrupt`/`gate_interrupt_ack`, every cancel/finalize `disposition`,
post-source `input_close`/`input_closed`, active and idle `shell_exit`, resize
prepare/apply/result, shell-ended shutdown, and both `closed` reasons. Add
fail-closed private stream decoding with exact
arity, frame bounds, terminal-message, fragmentation, and EOF behavior.

Gate: every private frame shape has a byte-exact focused test and no executable,
PTY ownership, helper transport, or process supervision is introduced.

**B1h. Canonical integrated conformance corpus**

Assemble the canonical corpus under `tests/fixtures/envoy-protocol-v1`: one
complete interleaved public session, every private frame as exact hexadecimal
bytes, and the inspection, path-resolution, and digest cases. Add cross-layer
tests that consume the checked-in corpus, regenerate it deterministically only
under the explicit fixture-update mode, and fail on unapproved drift. This
integration slice adds no new production protocol behavior.

Gate: the full runtime tests and static validation pass, fixture regeneration
leaves the worktree clean, every B1 requirement maps to focused or corpus
evidence, and only then is B1 complete. Production Envoy and Awsh commands,
PTY/process supervision, network listeners, and controller integration remain
later reviewed slices.

**B2. Awsh boundary alignment**

Replace the Bash-resident request loop with the external Go Awsh supervisor and
initial Bash backend. Prove direct `Envoy -> Awsh -> Bash` parentage, real shell
wait status, cooperative bracketed-paste submission through Envoy, the
start/ack barrier, source preflight, the complete supported state matrix,
immutable hooks, action gates, cancellation survival, explicit shell exit, and
orderly shutdown. Make idle shutdown send one uncatchable `SIGKILL` to the known
selected-shell process group, reap Bash, close helper/slave resources, and report
status 137 without running workload traps; preserve an actual reap status when
Bash wins the pre-signal race. Launch Bash with the manifested trusted rcfile
and prove its initial prompt-state/readiness sequence completes before private
Awsh readiness.
Validate `/bin/bash` against Envoy's generated build table and the selected
entry's absent compiled system-rc path before launch, and retain only Awsh's close-on-exec control-only PTY slave descriptor
for identity checks, submission-capsule termios operations, and atomic
cancellation signaling.
Implement the private mode-0700 runtime directory and
mode-0600 Unix `SOCK_SEQPACKET` helper endpoint using short-lived modes of the
manifested Awsh binary; freeze every bounded packet, environment limit, state
transition, blocking reply, peer check, timeout, and cleanup rule. Implement and
test exact signal-safe verify/ignore/default-restore behavior around every
helper. Preserve `EXIT` as the sole persistent trap; reserve
`ERR`/`DEBUG`/`RETURN` unset, `xtrace` disabled, and every catchable real-signal
trap in the generated Bash-build inventory at default at every adapter boundary.
Check the shell option flag directly and check every reserved trap through
private-file `trap -p` output, without command substitution or a child process,
before every normal helper spawn. Store and compare the canonical `trap -l`
output from which the catchable inventory was generated; reject any drift as an
unsupported Bash build. Make a gate refuse status 125 and a reached prompt
boundary fail closed when reserved state is invalid; do not claim tracing or a
pending signal trap can be suppressed before its own invocation. Allow trusted source to use
reserved facilities transiently only when it restores the required boundary
state, while foreground applications retain their own signal handlers.
Implement and test the Awsh-owned `setsid`/`TIOCSCTTY` controlling-terminal
session, supervisor-private `SIGTTOU` handling, and barriered Bash-child
process-group/fd-0-1-2/foreground-group launch sequence. In the child-only
async-signal-safe path after foreground release, use raw syscalls to clear the
inherited signal mask and reset every catchable real signal in the generated
Bash-build inventory to default before exec, without changing Awsh's own signal
policy. Prove Bash cannot inherit ignored or blocked `SIGINT`, job-control, or
other reserved signals, and prove Awsh can legally perform `tcgetsid`,
`tcgetpgrp`, and termios operations after Bash becomes foreground. Implement
the private shell-ended close handshake. Create and validate the
frame-entry marker in the immutable `PS0` path before its helper sends the
blocking `start` packet. Validate bracketed-paste
enablement, the exact begin binding, and `C-J` mapped to `accept-line` in both
supported line-entry keymaps at startup and every reached prompt boundary; an
invalid state must fail before readiness. Capture `histexpand`, disable it while
the generated frame is read and parsed, then generate two byte-identical
authored brace branches selected by the immutable input-state function as an
`if` condition. That condition restores history state and returns the exact
prior status without invoking `errexit`; the selected authored branch must keep
normal `errexit` behavior and see that status at its first command. Apply and
verify the entry sentinel while the blocking `prompt_ready` helper still
prevents Bash from entering Readline; acknowledge it only after exact read-back,
then accept no input or `execute` until Readline clears both sentinel bits and
Awsh captures the active state. At operation return, retain the candidate
status, cwd, and resolved inspections from `prompt_state` but withhold private
`completed` until Envoy has closed and quiesced the operation input gate, Awsh
has flushed the slave queue while the matching `prompt_ready` helper remains
blocked, that helper has exited, and the observed active-state transition proves
Bash has re-entered Readline with no prompt helper left for Envoy cleanup. Make
that immutable primary-prompt hook
an exact, manifested output-empty construction, so waiting for the transition
cannot admit a real prompt into the operation range; visible prompts remain
controller presentation. Implement private `input_close`/`input_closed`, require
Envoy's acknowledgement only after its sole PTY writer is quiescent and later
input is in discard mode, and successfully flush the retained slave with
`TCIFLUSH` before applying the post-source entry sentinel; startup skips this
operation-only exchange. Apply the closed byte-transparent termios delta
only from that exact active state. Require normal Readline return to restore the
sentinel, then restore and verify the workload snapshot before Awsh accepts
`start`. Keep the frame-entry marker and termios comparison as
independent framing backstops. Enforce the protocol's closed
simple-alias grammar from `BASH_ALIASES` at startup and every reached prompt
boundary, reserve the exact input-state-condition alias name, preserve common
command-and-argument aliases, and fail unsupported grammar-bearing alias state
without transferring aliases into Awsh preflight.
Parser-state-dependent, whitespace-only, comment-only, and exact
bracketed-paste-terminator-containing source must be rejected before start.
Keep a Ctrl-C-interrupted gate helper blocked while Awsh proposes the interrupt,
make Envoy order that proposal against continue/cancel/finalize, publish the
typed output-barrier event before acknowledgement, and only then let the helper
return 130.
Prove Bash and exec'd descendants inherit no Envoy socket, private
Envoy-to-Awsh descriptor, or helper descriptor. Do not retain a second
Bash-resident production driver or claim non-Bash support.

**B3. Envoy session foundation**

Implement listeners, the controller-generated session-ID handshake, the
independent actor-local connect/hello/ready deadlines, one PTY, persistent
external-Awsh/Bash startup with validated private identities, shared PTY
execution, exact byte relay, sole-writer serialization of controller input and
Awsh-requested source submission, exact pre-submission retention and
submission-redraw discard, post-source input-gate closure that finishes a
crossing PTY write and discards all later operation bytes before acknowledging
Awsh, the non-resetting operation-start deadline, bounded
control writes, the one-exec close-on-exec descriptor handoff, an empty
`HISTFILE` for controlled Bash after application environment delegation, and
orderly shutdown carrying the reaped shell status privately without relying on
PTY closure or a cooperative Bash command.

**B4. Operation boundaries and controls**

Implement Awsh `submit`/`started`/`started_ack`, source-redraw discard, output
barriers, completion, input, resize, cancellation, action gates, planned
finalization, and final drain. Make Envoy the sole gate-decision arbiter: close
the multi-source output frontier and complete
`operation_gate_interrupted` before `gate_interrupt_ack`, suppress a losing
proposal, resolve a crossed continue with the interruption event, and preserve
a crossed cancel or finalize against the resumed operation. Treat the first
submission byte as the start commit point: cancel before it disarms privately
with an empty pre-start result after proving no frame entry, clearing operation
arming, and restoring exact `READLINE_ACTIVE`; retain the captured prompt state
so the next submission restores workload-visible history expansion before
authored source. A rollback failure is fatal. Cancel crossing the bounded
submission transaction is accepted immediately after `operation_started` and
takes the ordinary running path. Freeze result-versus-request serialization and
the private request/disposition handshake: `signal` confirms Awsh selected and
interrupted the foreground group inside the same serialized classification,
`gate-cancelled`, `settled`, and `disarmed` perform no PTY signal, and
`already-interrupted` prevents a second action when
cancel crosses finalization. Retain a crossed just-written Awsh result until the
next execute/shutdown, buffer it at Envoy until the disposition arrives, and
prove signal-safe helper transitions close every source/gate/completion race
without resetting the timer. Freeze
resize placement at the
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
commitment begins; buffer it until the schedule is known and use its
`output_through` as a covered prefix for each logical stream. Place the resize
after the latest authored output event derived from any covered prefix. For
stdout-then-stderr publication, require authored order to win when raw stream
interleaving makes both sides of the raw frontier impossible to preserve: once
stderr is covered, all stdout precedes the resize and only uncovered stderr
remains after it. Cover a long-running operation whose resize arrives before
any authored event is committed, plus stderr-before-resize-before-stdout and
stdout-before-resize-before-stderr split-stream cases. Carry the cumulative
terminal-input watermark on each
`continue` and keep the gate closed until the Envoy has received those bytes;
cover delayed terminal input and cancellation while waiting. A continuation
watermark timeout must take fatal session teardown with no terminal operation
result or private gate-abort mechanism, and must record the explanation and
Reploy termination request and result. Accept ordinary completion from the
controller's cancelling state when the serialized inspection result wins before
cancellation. Before ordinary completion can release Bash into Readline, require
the private input-close exchange to quiesce Envoy's sole writer, discard later
terminal bytes, and let Awsh flush unread slave input. Cover `read -n 1` with a
multi-byte text step, a master write crossing closure, later in-flight bytes,
the next operation, every frame mismatch, flush failure, and timeout; no unread
suffix may execute as an idle command. Begin each accepted resize with private `resize_prepare`. Make Awsh
defer `resize_ready` across every active prompt-state, Readline-entry,
submission-capsule, or cleanup termios transaction and reserve that lane when it
replies. Then linearize the resize in the output pump: close and carry its
preceding `output_through` frontier across the PTY and every active operation's
split stdout/stderr source before sending matching `resize_apply`. Make Awsh
perform Linux `TIOCSWINSZ` on its retained control slave and return one matching
`resized`, with the whole prepare/frontier/apply transaction under a
non-resetting five-second deadline. Require Bash's complete real-signal trap
inventory at default, including `SIGWINCH`, while
relying on the ioctl's one kernel-generated foreground-group signal for
applications, and make
the controller reach that raw-log frontier before publishing the resize. Cover PTY
output immediately preceding a resize and a continuously writing PTY workload
in B4 conformance; add the active split-stream equivalent with B5. Cover both
resize/shell-end race outcomes: `resize_applied` resolves a resize applied
before drain, while `draining` resolves a superseded outstanding resize without
publishing it. Prove that a shell-ended drain crossing both an unstarted
`execute` and its deadline-derived `cancel` resolves both requests with no
terminal operation result and leaves the planned beat to fail as unrunnable.
Make every failed `TIOCSWINSZ` fatal to the session in idle and active-operation
states: emit best-effort `resize-failed`,
no `resize_applied` or terminal operation result, close the channels, exit
nonzero, retain partial artifacts, explain the failure, and log the Reploy
termination request and result. Before
any terminal operation result, observe EOF on the
operation's split streams, drain those and the PTY bytes, and emit their covering
mark.

**B5. Split execution**

Implement Envoy-created per-operation stdout/stderr FIFOs in a private
mode-0700 runtime directory, with stdin left on the PTY, separate stream
supervision, ordered terminal forwarding, sender-stamped output marks, and
split-stream conformance. Envoy must open readers and keepalives before Awsh
frames the redirections, kill remaining operation writers before closing its
keepers, drain both FIFOs through EOF, remove the operation paths, and only then
publish the terminal result. Prove the selected FIFO-backed fd 1/fd 2 are the
only FIFO descriptors reaching a split exec'd child: no reader, keeper, helper,
control descriptor, or pathname contract reaches it, and no descriptor or path
survives into a later operation.

**B6. Process cleanup and exclusive observation**

Implement fail-closed exclusive evidence ranges for checked, suppressed,
replaced, and presentation-timed operations. Reject `output_contains` and
`output_regex` at plan compilation when an interactive operation or any of its
continuations sends bytes through `text`, `key`, or `control`; retain
`wait_for` as visible-terminal synchronization rather than assertion evidence.
Independently of that evidence mode, implement Envoy-owned process-tree policy
after every submitted Bash operation: Envoy directly supervises Awsh and acts
as subreaper, Awsh directly parents and reaps Bash, and Envoy performs pidfd
tracking, repeated `/proc` census, termination, adopted reap, EOF, and drain
before the terminal result. Cover ordinary
background jobs, `disown`, `nohup`, `setsid`, rapid double-fork daemonization,
cancellation, cancellation received after the Awsh result while mandatory
cleanup is in progress, planned finalization, the five-second monotonic cleanup
deadline, a cancellation racing a command that ends the persistent shell, and a
setup-launched service outside the controlled tree remaining unaffected. Prove
that cancellation and finalization grace-period expiry makes Envoy terminate
the selected-shell process group, Awsh explicitly report Bash's reaped status,
and Envoy emit `operation_failed` with the corresponding timeout code and
`shell_ended: true`, then enter the `shell_ended` drain without another prompt
or operation. Prove that a deadline cancel accepted during the original
finalization grace sends no second signal, does not reset the timer, and
switches the result to `operation_cancelled` after a timely Awsh adapter result
and cleanup or to `cancel-timeout` with `shell_ended: true` on expiry. Prove
that post-result cancellation does not signal idle persistent Bash, does not
reset the cleanup deadline, skips inspection only after successful cleanup, and
then emits `operation_cancelled`; cleanup failure still produces no terminal
operation result and hands final environment termination to Reploy. Also prove
the same outcome when cancellation arrives during post-finalize cleanup, and
prove both serialized winners when it arrives during post-finalize inspection.
Also prove that Awsh's explicit shell-ended result wins its cancellation race
without losing its reaped status. Prove that `finalize` received after the Awsh
result likewise never signals idle persistent Bash, never resets cleanup, and never replaces the
returned status with a synthetic status-free result; successful cleanup
continues through inspection, while cleanup failure remains fatal with no
terminal operation result. Do not add a controller process-lifetime option or a
per-operation numeric descendant-admission guarantee. V1 does not preserve
processes across operations; session-lifetime support may be added
later if setup cannot handle a compelling use case. Any future deterministic
process ceiling belongs to a Reploy-owned kernel-enforced workload/session
domain.

**B7. Workload inspection**

Have external Awsh's Bash adapter resolve configured paths from persistent Bash
cwd and exported environment; perform bounded workload
existence/type/hash inspection in Envoy only after the universal operation
cleanup and output drain, and return private typed results without controller
filesystem access or probe commands. Run the resolved plan in a short-lived,
restricted worker mode of the Envoy executable with no inherited session
channels. Serialize worker-result acceptance against `cancel`; cover the normal
result winner, the same two winners when cancellation crosses planned
finalization inspection, cancellation followed by worker stop and reap within
five seconds, and a blocked worker exhausting that deadline. The last case must
emit the fatal `inspection-cancel-timeout` diagnostic, emit no terminal
operation result, prevent a later operation, retain a bounded controller
explanation, and log the Reploy termination request and result. Also cover mutation races,
cleanup and drain failures, and every inspection resource limit.

**B8. Failure and isolation hardening**

Prove the exact process topology, socket and private-descriptor isolation,
stable failure classes, Awsh-first failure, active and idle explicit shell exit,
malformed private traffic, adapter-framing corruption, cleanup, and repeated
shutdown behavior.
Cover helper packet truncation/overflow/state mismatch, environment overflow,
manifested-rcfile absence or mismatch, unknown Bash digest/build-table entry,
present system rc path, oversized prepared startup environment, startup helper
ordering, inherited ignored and blocked signals from each generated-inventory
class,
every submission-capsule termios flag and read-back/restore failure, initial and
later mutation of paste-begin or `C-J` acceptance, top-level history-expansion
suppression with both preserved values, both exact conditional branches,
nonzero prior status with `errexit` enabled, input-state-condition failure,
entry-sentinel ordering and timeout, input-close ordering, slave-flush failure,
input or `execute` before observed Readline entry, initial and later non-default
traps across the complete generated real-signal inventory, generated-inventory
drift, gate refusal under invalid
state, initial and later `ERR`/`DEBUG`/`RETURN` traps, initial and later
`xtrace`, a pending ordinary signal trap that mutates cwd or `histexpand` before
the boundary, pseudo-signal trap interception, side-effecting `PS4`
interception, resize deferral across each termios-handshake phase, private
prepare/ready/apply mismatch and timeout, a transient Bash signal trap restored
before the boundary, an application `SIGWINCH` handler, and absence of duplicate
signaling,
the maximum accepted source producing an in-bound doubled-source `submit` frame,
operation-start timeout, partial Bash launch cleanup, every disposition phase,
late Ctrl-C during each helper mode, gate-interrupt winners and losers against
continue/cancel/finalize, normal completion after `gate; echo after`, immediate
`errexit` after gate status 130, source-to-gate and source-to-prompt helper
transitions during Awsh's atomic classified signal action, crossed private result/disposition
ordering, and private EOF after `shell_exit` but before
the required `closed` frame. Cover idle shutdown's successful process-group
`SIGKILL`, fixed status 137, trap suppression, pre-signal `ESRCH` reap race,
resource-close order, and final-drain timeout.
Cover both orderings of `shutdown` crossing an idle persistent-shell exit:
observed shell exit first resolves `ShutdownSent` through `shell_ended`, while
accepted shutdown first preserves the requested shutdown reason.

Gate: the complete local Envoy/external-Awsh/Bash-adapter conformance suite
passes. No Reploy or
terminal-runner integration is required to review the individual B slices.

### C. Controller and Reploy boundaries

**C1. Public Reploy codecs**

Extract strict public controlled-session event, request, and host-result codecs
with the broad fixture corpus, without controller lifecycle or subprocess code.

**C2. Controller lifecycle state machine**

Implement lifecycle ordering, attachment startup, completion, termination,
acknowledgement, cancellation, stderr retention, and failure handling against a
deterministic fake client.

**C3. Envoy session client**

Implement the Python terminal/telemetry client against the canonical Envoy v1
fixtures, including inspection results and cross-channel output barriers.

**C4. Runtime build artifact**

Add reproducible `CGO_ENABLED=0` platform builds for the Envoy and external Awsh
commands from the runtime Go module. Manifest both binaries, Awsh's built-in
Bash helper modes (without separate helper executables), the trusted Bash
rcfile, the empty Readline file, terminfo, and locale
assets; require the same selected artifacts for host and isolated workloads.

**C5. Runtime staging**

Materialize a manifest-validated read-only `/omegaflow-runtime` tree without
blueprint composition or controller execution.

**C6. Blueprint schema and composition**

Add typed controller/workload blueprint models, Hydra composition, read-only
controller configuration, resolved YAML retention, and fixture conformance.
Reject the complete normative launch-control environment enumeration before
materialization, including application-provided `HISTFILE` and `INPUTRC`.
Compose an empty `HISTFILE` after the application, validate that final
value, and prove neither application `HISTFILE` nor the default under its
`HOME` can block Bash before OmegaFlow types the Envoy bootstrap command. Record
and validate `/bin/bash` against the generated build table, require any selected
system rc path absent, derive the exact first controlled-Bash exported
environment, and reject preparation when its canonical helper representation
would exceed 1,024 entries or 49,152 encoded bytes.

**C7. Controller run input**

Prepare the bounded `omegaflow-controller-run-v1` manifest and declared assets,
stage them as a read-only controller-only `/omegaflow-input` mount, and add the
internal controller command that validates the schema, paths, hashes, bounds,
and recording plan before starting the session client.

**C8. Controlled-session invocation**

Prepare separate deployments, invoke the public controlled-session command,
resolve only trusted opened endpoints, and retain the exact host result and
stderr.

Gate: controller, codec, staging, and blueprint tests pass independently before
the terminal runner consumes them.

### D. Terminal-only integration milestone

**D1. Envoy-backed terminal runner**

Adapt `PersistentTerminalRunner` to the Envoy session while preserving command
status, cwd, input, resize, Ctrl-C, output policies, assertions, action gates,
produced outputs, ranges, and structured diagnostics.

**D2. Direct terminal artifacts**

Write the private raw log, asciicast, and timeline directly from controller
presentation events plus Envoy terminal and telemetry events. Do not introduce
a controller-side PTY or `asciinema record` process.

**D3. Isolated Reploy end-to-end proof**

Run one internal demo or tutorial through a real isolated Reploy workload.
Exercise nominal capture plus startup, channel, workload, Envoy, finalization,
acknowledgement, and cleanup failures, with repeated nominal runs for race
coverage.

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
| A1 | Approved | Approved on the current PR 25 revision after the local deep-design-review corrections and fresh remote review; GitHub carries the `approved` label |
| A2 | Approval pending | Owner-approved external Awsh-supervisor direction with a 126-test feasibility result; exact local review status belongs to hash-bound sidecars, while current-revision approval and required checks remain mandatory before B work resumes |
| B1a–B1h | Pending | Raw material only; each sub-slice requires independent review and acceptance evidence |
| B2–B8 | Pending | Raw material only |
| C1–C8 | Pending | Raw material only |
| D1–D3 | Pending | Raw material only |
| E | Deferred | Requires terminal-only gate |

Update this table only from executed checks and current review state. Historical
PR labels and tests from superseded commits are context, not completion
evidence for the rebuilt stack.
