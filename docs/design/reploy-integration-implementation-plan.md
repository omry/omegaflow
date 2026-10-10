# Reploy Integration Implementation Plan

## Status

- Temporary delivery plan.
- Trusted implementation boundary: the rebuilt stack's base, the tip of
  `main`, which carries the formerly approved implementation stack. That
  stack's final pre-rebuild PR number, 8, has since been reused by an open PR
  in the rebuilt stack, so PR numbers are not boundary evidence; within the
  rebuilt stack, the `approved` label on a PR is. Node identities are not
  recorded here because every restack rewrites them.
- Updated: 2026-10-11.
- A2.7 is approved and merged as PR 38 at
  `f37cd3cdaddf6e04b80011e5b47ee21cb78aca27`. A2.5 and A2.6 remain approved
  predecessors. A2.8 is merged as PR 39 at
  `31e9a497bca99139e00ef64c2a4c4042a4b89e78`; A2.9 is merged as PR 40 at
  `e10e61614f25c06da12fba2610803126390be1e7`. B1 and B2.1–B2.7 have approved
  partial implementation, including A2.10, the B2.3.1/B2.3.2 split and B2.3.3
  correction. B1.2.1 and B1.3.1 are approved owning codec/corpus successors.
  This A2.11 successor separates qualified table data from runtime binaries
  before the B2.1.1 generator correction and B2.8 qualification. The complete
  production execution path and supported Bash-build qualification remain pending.
- A `.review` sidecar attests only the document bytes matching its recorded
  content hash. Changed documents require fresh review and byte-current
  attestations before approval; a sidecar does not establish PR approval.
  The A2.7 merge retains the historical document and sidecar evidence for that
  approved boundary.
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
- Each numbered implementation delivery slice below produces exactly one PR
  with one contract or subsystem responsibility. B1–D3 are requirement packages,
  not executable delivery selectors; use a leaf ID such as B1.1. A package closes
  only after all its leaves and its cumulative gate pass.
- Target at most 750 changed production lines per PR; 1,000 is the hard ceiling.
  Count production additions plus deletions against the PR's exact base, including
  Go/Python/Bash code, executable helpers, build/staging tools, runtime
  configuration, and generated production text. Do not use net growth or omit
  production code because it is copied from a prototype or placed beside tests.
  Tests, test-only fixtures, documentation, and review metadata do not count
  toward this production limit; report their sizes and the total diff separately.
  Report binary production assets separately with their provenance and review
  method; a binary cannot conceal handwritten production code.
- The estimates below are production-line planning allowances, not measured
  prototype diffs or promises. Prepare each leaf against its actual base before
  coding: name owned paths, record production/test/fixture estimates, acceptance
  command, prerequisites, and remaining cross-slice proof cases. Above 750 and
  through 1,000, record why the responsibility cannot usefully be split and obtain
  an agent-owned preparation/review disposition within the authorized scope.
  Above 1,000, split and review the revised leaf boundaries before implementation
  continues; neither generated files nor golden fixtures exempt production text.
  Never move required behavior into an untracked follow-up merely to meet size.
- Recheck the actual production diff before commit and after review corrections.
  If scope or growth invalidates a boundary, replan before publication. Replace
  an unstarted leaf with explicitly numbered successors and update dependencies,
  proof mapping, and ledger; do not deliver multiple PRs under one leaf ID. An
  approved PR stays immutable; any correction is a separately named successor
  slice and PR, with its own acceptance and size checks.
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
  fatal with no terminal operation result. Selection of the ordinary
  started-operation lifecycle path starts the one existing grace deadline
  before foreground sampling; successful ioctl
  return does not reset it. Envoy makes that target safe through its
  controlling-terminal session and pidfd-backed census invariant: before the
  ioctl it samples the foreground group with
  `ioctl(PTY_MASTER, TIOCGPGRP, &foreground_pgid)`. It validates every live
  `/proc` member of a positive group against the controlled session ID and
  pidfd-backed controlled-tree census, while every group
  eligible to become foreground is persistent Bash, an adapter helper, or a
  current-operation descendant. A positive group with no live member is a
  permitted job-return/Bash-reclaim crossing: Envoy resamples it under the same
  deadline while racing `input_close` and `shell_exit`. A Bash-local fact wins
  without a signal; a later live eligible group is validated and signalled once;
  deadline expiry selects the lifecycle timeout. The kernel atomically targets
  the then-current group within that session; an invalid sample, a live
  wrong-session or unclassifiable member, or another inability to prove the
  invariant is fatal before a signal or terminal result. A cancel accepted during ordinary-return cleanup
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
  Envoy's serialized acceptance is the public winner: if `gate_ready` is
  accepted before a lifecycle request, publish `operation_ready`, then apply a
  later lifecycle request from `Gated`; if the later gate outcome is accepted
  before a lifecycle request, publish `operation_continued` or
  `operation_gate_interrupted`, then apply a later lifecycle request from
  `Running`. A controller already locally in
  `Cancelling` or `Finalizing` consumes any such committed event as a crossed
  predecessor, stays on its lifecycle path, and schedules no handoff work. If a
  lifecycle request is accepted first, do not send an unsent `continue`, but
  consume Awsh's exactly-one outcome
  whether or not `continue` was sent, without public gate telemetry, while
  continuing from `Cancelling` or `Finalizing`. The selected persistent Bash
  reserves `INT`; recorded top-level source cannot install or change its trap.
  POSIX mode is also adapter-reserved: the fixed rcfile keeps
  `POSIXLY_CORRECT` readonly while unset, which prevents the selected Bash
  build's `set`, `shopt`, and explicit-`builtin` forms from enabling
  special-builtin precedence and bypassing that trap reservation.
  Completion may temporarily ignore
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
- A2.7 closes all private schemas and establishes the exact B1 base as one
  design-only PR. Freeze every Envoy/Awsh and Bash-helper message's direction,
  ordered fields, arity, scalar and nested-JSON encoding, aggregate byte
  accounting, state/identifier validation, and terminal EOF/reap rules.
  Close the remaining `shutdown`, `shell_exit`, `closed`, and `protocol_error`
  forms without replacing the approved start, gate, completion, or lifecycle
  architecture. Map each recoverable rejection, terminal operation failure,
  and fatal session failure to its existing deadline and teardown path. Inventory
  the exact nominal, malformed, maximum-bound, and crossed-outcome cases B1 must
  freeze and assign runtime proofs to the existing B2–B8 owners. Align all four
  documents and their byte-current review sidecars. This slice adds no production
  code, executable fixtures, public fields, actors, or deadlines.
- A2.8 repairs the implementation dependency order on the approved A2.7 merge.
  Separate B1's static wire corpus and expected traces from executable proofs
  owned by B2–B8 and the later controller/runtime slices. Assign initial
  Bash-build qualification, the canonical table, and its consumer generation to
  B2 alongside the adapter it measures; C4 packages those artifacts. Record the
  external Reploy `tool:bash` inputs and the still-required `/bin/bash`
  placement decision without inventing supported releases or claiming delivery.
  Preserve every A2.7 case, actor, frame, bound, timer, and terminal-only gate.
  Review the aligned plan and protocol evidence descriptions with current
  document attestations before B1 starts.
- A2.9 replaces broad implementation selectors with the bounded leaf catalogue
  below, targeting 750 and never exceeding 1,000 changed production lines per
  PR. Preserve the complete A2.7/A2.8 requirements and final real-actor gates;
  add explicit predecessor, isolated acceptance, and last-prerequisite proof
  ownership for each leaf. Review and approve this plan before preparing B1.1.
- A2.10 corrects the terminal handoff after real B2.5 candidate tests exposed
  canonical-frame redisplay. Save the complete fresh workload termios state,
  prepare `ICANON` on and `ECHO` off before every adapter-owned Readline entry,
  and prove entry by `ICANON` clearing while `ECHO` stays clear. After matching
  `started_ack`, restore and verify the exact saved state through the existing
  terminal-control lease before the start helper's successful reply. Preserve
  intentional raw/no-echo state and the post-cleanup recapture for the next
  operation. Keep the original Envoy-owned start epoch, queued-cancel ordering,
  all wire forms, actors, helper markers and failure mappings. Align all four
  governing documents and current attestations. This is one design-only PR;
  refresh the static inventory's document-byte and affected prerequisite
  bindings after review,
  retaining every frozen wire byte, case ID and pending runtime claim;
  B2.3.3 owns the readiness implementation correction, B2.5 submission proof,
  B2.6 start integration, B2.7 completion integration and B2.8 qualification.
  Envoy holds controller-terminal reads after `execute.input_through` until
  accepted `start_released`, so input authored immediately after public start
  reaches restored workload termios after the start helper has finished,
  including Ctrl-C. Socket backpressure adds no unbounded application buffer
  or new timer. B3.3 owns this forwarding boundary and its deterministic peer
  cases; B3.4 repeats them with real Awsh/Bash, including delayed restoration,
  immediate keys/Ctrl-C, preserved raw/no-echo state, input closure, recoverable
  rejection/pre-submit cancellation and fatal start timeout without replay to
  another operation.

- A2.11 separates qualification output from the executable it qualifies after
  B2.8 preparation demonstrated that embedding the exact Awsh digest in Awsh
  creates a circular identity requirement. Keep the complete actual Awsh hash
  and every behavioral asset bound by qualification. Generate table data into
  the fixed manifest-bound regular `etc/bash-builds.json` payload; generate
  data-independent schema consumers, including their source comments and
  build identity inputs. Review all four governing documents and byte-current
  attestations in this design-only successor. B2.1.1 owns the generator and
  runtime lookup correction; B2.8 retains genuine qualification and every B2
  case. Later adapter changes require affected entries to be requalified.

Gate: each A2 slice is a design-only successor of the preceding approved slice
and must complete deep design review, current-document attestation, required
checks, and exact-head PR approval before its successor is published. No B
implementation starts until A2.7, A2.8, and this A2.9 successor are approved and
merged.

A2.10 is a successor of the approved B2.4 stack tip. Its exact-head approval
and current document attestations precede B2.3.3 publication; B2.3.3 approval
precedes B2.5 completion. These successors require approval, not predecessor
merge. Approved PR49 and PR50 stay immutable. No correction enables a complete
production runtime or admits a supported Bash entry before its existing gate.

A2.11 follows the approved B2.7 and B1.3.1 stack tip. Its exact-head approval
and current four-document attestations precede B2.1.1 publication. The owning
B2.1.1 successor follows A2.11 and precedes B2.8; B1.3.1's B1-C022 corpus
correction is also a required B2.8 predecessor. Preserve approved PR45, PR46
and all approved predecessor commits. These successors require approval, not
merge, and do not admit a supported build without the B2.8 qualification gate.
B2.1.1 proves qualified launch through an isolated test-only assembly linking
the existing selected-shell launch module, using a genuinely measured candidate
and its exact actual assembled Awsh binary and behavioral assets. Regenerating
table data must leave that binary unchanged and permit the same qualified
launch. This proof adds no production entrypoint and depends on no B3
implementation. B2.8 still owns genuine per-target admission and every required
B2 case; B3.2 owns production PTY/Awsh startup.

The exact B1 base is the merge commit of the A2.7 PR approved at its exact final
head, with current document attestations and all required checks passing. Record
that merge SHA in the delivery closeout evidence when delivery completes; before
B1 work, verify it contains the approved document and sidecar bytes and is an
ancestor of the chosen checkout.
No draft A2.7 head, older A2.6 run, prototype commit, or PR number alone satisfies
this base. Later unrelated main commits may follow it, but cannot substitute
their identities for the recorded approved A2.7 boundary.
The implementation checkout must also contain the approved A2.8 merge and its
byte-current documents and attestations. Record both boundaries; A2.8 corrects
delivery order without replacing A2.7's approved protocol contract. Preparation
and review of this successor do not complete or silently resume the blocked B1
delivery campaign; its next preparation step must resolve the newly approved
slice scope before opening implementation PRs. Also verify and record the
approved A2.9 merge and its current attestations before preparing B1.1; the
replan does not resume the old B1 campaign.

### B. Local Envoy and Awsh conformance

**Requirement package B1. Protocol models and fixtures**

Add Go protocol models, strict encoding/decoding and validation, and canonical
wire fixtures under `tests/fixtures/envoy-protocol-v1`, from the approved A2.7
contract and A2.8 delivery order. Cover every public, private, and helper form,
direction, field order, arity, scalar/nested-JSON encoding, aggregate bound,
fragmentation, concatenation, and malformed case in the shared inventory below.
Freeze contract-defined expected traces for handshake IDs, stream marks, absent
fields and empty ranges, failure/result eligibility, ordering, deadline epochs, and crossed
outcomes. These are declarative inputs and expectations, not recorded evidence
from an Awsh, Envoy, or controller implementation. Include deterministic path
and digest examples, with the native `directory` and protocol `directory-v2`
encodings tagged separately; live Bash expansion and inspection belong to B7.

Startup fixtures use explicitly synthetic zero-to-4,096-byte strings to test
bounds and `ready.output_through` encodings, including mismatch, overflow, EOF,
and incomplete-barrier expectations. They assert no supported Bash digest or
observed startup bytes. Source/frame examples and helper messages likewise
freeze the approved bytes without claiming a selected Bash has parsed or
executed them. B2 adds real build entries and selected-shell evidence later.
For build-dependent values, such as checker/trap diagnostics, signal aliases,
or startup-export results, B1 records the scenario and proof owner rather than
inventing an expected value. B2 supplies the concrete expectations from the
selected build's qualification evidence.

Gate: the Go models and validators pass the complete static corpus, and each
runtime case is traceable to its owner below. B1 requires neither a Bash support
matrix nor an adapter, Envoy, generated build-table consumer, runtime package,
or live Reploy session. No executable proof is marked passed by a static fixture.

**Shared conformance inventory and proof ownership**

The following inventory is cumulative across delivery slices. B1 freezes its
wire inputs and expected outcomes. Each executable claim must also acquire
test evidence in the owning implementation slice; "prove" below is not a B1
acceptance requirement. Record the mapping from each case to its fixture and
owning test as the corpus is implemented. A deterministic peer can isolate a
slice, but does not prove the absent peer's production behavior. Cross-actor
cases run again with the real actors when all owners are available.
An earlier slice's closeout requires its implemented behavior and isolated
boundary tests, not a later slice's implementation. Record the remaining
cross-slice cases explicitly as pending, and close them in the slice that adds
their last prerequisite. For example, B4's split/cleanup/inspection crossings
close with B5/B6/B7 respectively; they cannot require those successors before
B4 is reviewed. This permits bounded delivery, not a partial-conformance claim.

| Evidence | Owner and prerequisites |
| --- | --- |
| Static wire models, bounds, malformed bytes, and expected ordering/deadline traces | B1; approved A2.7 contract and A2.8 order only |
| Real Bash-build entries, source parsing/execution, suffix isolation, Readline, reserved-state mediation, persistent state, and Awsh-side helper/completion behavior | B2; B1 plus verified candidate Bash inputs; an isolated PTY and deterministic Envoy peer exercise Awsh without a production Envoy or C4 package |
| Envoy/Awsh startup, startup-byte comparison, readiness, output drain/relay, and private/public start barriers | B3; B1 and the corresponding B2 adapter and qualified build |
| Output/input barriers, completion coordination, resize, gates, cancellation, finalization, and lifecycle crossings | B4; B2/B3 foundations; split, cleanup, and inspection crossings also require B5/B6/B7 respectively |
| Split setup/rollback, stream marks, writer keepalives, independent EOF, and FIFO removal | B5; B2/B3 foundations and B4 boundaries |
| Real descendant census/termination/reap, completion-helper survival, job-table cleanup, final drain, and exclusive observation | B6; B2–B5; rerun ordinary-return and lifecycle crossings with actual cleanup |
| Live path resolution, native file-digest compatibility, tagged directory digests, instability/resource limits, worker isolation, and inspection cancellation | B7; B2–B6 |
| Isolation, malformed traffic, channel loss, terminal EOF/reap, shutdown, and captured-deadline/fatal teardown crossings | B8; B2–B7; include startup, split, cleanup, and inspection variants |
| Controller-side buffering and first-prompt barriers, crossed controller lifecycle events, and termination requests | C2/C3; B1 corpus and corresponding local runtime proofs; C2 uses a fake client, C3 tests the actual session client, and C8 proves real Reploy termination |
| Manifested assets and launch-environment/blueprint rejection before materialization or Bash launch | C5/C6; qualified table and C4 artifacts |

The cumulative suite covers inspection
requests, deterministic IDs, resolved plans, typed results, aggregate frame
bounds, malformed cases, failure codes, matching and mismatching handshake session IDs, and every
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
Freeze readonly `trap` and `enable` mediation for the
adapter-sensitive `CHLD` (`SIGCHLD`), `INT` (`SIGINT`), `DEBUG`, `ERR`, and
`RETURN` traps and the exact adapter-required builtin set. Keep POSIX mode
disabled and adapter-reserved,
and keep `POSIXLY_CORRECT` unset and readonly, because POSIX special-builtin
precedence would otherwise bypass the `trap` mediator. Before deciding whether
a target is reserved,
the readonly `trap` mediator must canonicalize every selected-Bash `signal_spec`
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
ordinary selected-Bash behavior. Prove direct, combined, and expanded attempts
to enable POSIX mode through `set`, `shopt`, or their explicit-`builtin` forms
leave the mode disabled because assignment or unset attempts cannot change
readonly-unset `POSIXLY_CORRECT`; an immediately following
`trap 'exit 42' INT` must still be
mediated. Retain the selected Bash build's exact status and diagnostic for each
attempt rather than adding adapter fail-stop behavior. POSIX queries and
requests that leave it disabled remain ordinary selected-Bash behavior, and a
positive top-level `set -- ...` case must preserve positional-parameter state.
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
Add shell-exit crossings before the first private `execute` byte, after a
complete `execute` but before `submit`, after `submit`, after `started`, after
accepted `start_prepared` but before Envoy writes `start_release`, after the
complete `start_release` write but before Awsh emits `started`, after public
`operation_started` but before `started_ack`, after `started_ack` but before
`start_released`, and immediately after accepted `start_released`. The first
crossing uses Awsh's empty operation-ID encoding and the existing shell-ended
drain without an operation result. Capture the terminal-input-barrier epoch
when the watermark is outstanding, or operation-start after it is satisfied;
retain that epoch for private EOF, zero-status Awsh reap, and any pre-start
split rollback. Final-drain starts at the existing drain-entry boundary and
does not replace or extend the captured bound. Freeze captured-epoch expiry
before final-drain, both deadlines due in one dispatch turn (captured expiry
first), successful closure/rollback followed by final-drain expiry, and both
successful. Captured expiry is fatal with the existing phase diagnostic and
no operation result or successful public `closed`; successful closure/rollback
ends that epoch and final-drain continues with its remaining budget, without
reset. Late crossed input cannot start a discarded operation. Every later pre-`start_released` crossing
is fatal under the unchanged operation-start deadline, while the final
crossing uses ordinary shell-ended result evidence. At both added boundaries
Awsh has registered the active operation, so `shell_exit` carries the active
operation ID and takes the fatal no-public-result mapping under the same
non-resetting epoch. Add execute and continue
crossings while their `input_through` watermarks are outstanding, including
`execute` before the operation-start epoch begins: an accepted
`protocol_error` retains the active five-second Envoy terminal-input-barrier
epoch without reset, supersedes the ordinary `input-barrier-timeout` mapping,
and emits no operation result or synthetic status. The sender's individual
control-write deadline never controls that crossing. Require bounded private
closure and Awsh reap under that epoch. EOF without an accepted `shell_exit`
or `closed` remains fatal, and zero, nonzero, or signalled Awsh reap is fatal
teardown evidence only. The `shell-launch` case requires its existing nonzero
exit; a zero status never makes protocol-error EOF orderly. Missing EOF, an
unreaped Awsh, or expiry is an additional bounded teardown failure with the
original cause retained. Retain bounded code/message evidence
and report Reploy termination. Repeat both barrier crossings with a queued
cancel and an outstanding resize; crossed requests resolve through fatal
channel failure without a new state or timer. Cover partial private-frame
crossings where Envoy has attempted its first byte but Awsh has not accepted a
complete `execute`: the wire ID remains empty, while the Envoy start phase is
already fatal. Repeat with queued cancel and both PTY and split setup active.
Cover valid, malformed, unknown-code, stalled-EOF, reset, and nonzero/signalled
Awsh `protocol_error` cases during launch, Ready/Idle, every start phase,
Running, Gated, Continuing, grace, cleanup, live inspection, inspection
cancellation, and drain. Preserve one active deadline epoch without reset;
when none is active, enter the existing Envoy final drain and start its existing
five-second epoch. The active epoch must be one of the listed Envoy phase or
terminal-input-barrier epochs; an individual control-write timer alone cannot
suppress that fallback. Retain bounded code/message evidence, emit no
synthetic shell status or operation result, and report Reploy termination.
For every variant, private EOF without an accepted `shell_exit` or `closed`
remains fatal, and every zero, nonzero, or signalled Awsh reap is fatal
teardown evidence only. Preserve the existing nonzero `shell-launch` exit;
missing EOF, an unreaped Awsh, or expiry is an additional bounded teardown
failure with the original cause retained.
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
lifecycle runtime proofs to B4–B8. Use the complete A2.7 private-frame inventory
for all request/result/helper directions, including exact `shutdown`,
`shell_exit`, `closed`, and `protocol_error` bytes, empty operation-ID encoding,
status and PID scalars, nested JSON, concatenation, fragmentation, maximum
aggregate sizes, unknown diagnostic codes, and terminal EOF/reap crossings.
Freeze failure-code/result eligibility and public absent-field/empty-range cases
from that same approved contract. B1 creates the static corpus and each owner
adds its executable evidence; A2.7's case list claims neither.

**Requirement package B2. Awsh boundary alignment**

Own the initial Bash qualification harness and canonical digest-keyed table,
including the generator for host-preparation, Envoy, and Awsh consumers. Deliver
this work and the adapter implementation in the numbered B2 leaves below;
the table is produced alongside the adapter, not required as a finished C4
artifact before the first B2 code can be reviewed. Prepare table validation and
the harness first, then measure the implemented launch/submission/return path
against each exact candidate build. Use the fixed rcfile/helper/inputrc and
trusted terminal/locale inputs from the approved contract in a local test tree;
C4 later builds and manifests the same artifacts rather than supplying the
first qualification harness. Deterministic Envoy replies exercise Awsh's
boundary only; B3–B8 must prove actual Envoy coordination and cleanup.

Reploy's `tool:bash` delivery supplies immutable acquisition/provenance and
advertised target tuples. OmegaFlow owns qualification of the exact resolved
regular `/bin/bash` under its adapter. Record the candidate's release, target
tuple, artifact/executable digests and Reploy definition/lock identity, the
resolved executable and system-rc condition, and the adapter and trusted-input
identities used for the measurements. Reploy support alone does not qualify an
OmegaFlow build. Resolve placement/export collisions against `/bin/bash` before
qualifying a tuple; do not replace that contract with a new path override.
Linux amd64 and arm64 need separate genuine target evidence before either is
advertised; versions, tuples, and digests are inputs still to be supplied, not
placeholders to mark supported. No live Reploy session is needed for local
qualification once its verified build inputs are available.

Populate each entry's system rc path or `none`, startup-export transformation,
catchable-signal inventory, Readline readiness/keymap/no-redisplay/UTF-8/
maximum-line behavior, and exact startup PTY bytes from repeatable selected-
build tests. Keep harness candidates distinct from shipped supported entries;
candidate data may bootstrap isolated tests, but production consumers accept
only qualified entries. Freeze observed bytes in the canonical table and
generate its runtime payload separately from schema consumer code, with
stale-generation and unknown/mismatched-digest rejection checks. B2 closeout
requires real evidence for every advertised entry and all B2-owned cases in the shared inventory. A missing external build input blocks
the affected qualification work, not B1; adapter or trusted-input changes
require rerunning the affected qualification before support is retained.

The qualified Bash-build table is generated as a separate readable regular
runtime payload at `/omegaflow-runtime/etc/bash-builds.json`, covered by the
runtime manifest and the read-only runtime mount. Generated Envoy and Awsh
consumer code reads only that fixed path, validates its manifest-bound bytes
and strict qualified-entry schema, and independently selects the exact resolved
Bash entry. Host preparation consumes the same canonical table. Neither table
contents nor their digest are compiled into Envoy or Awsh, including generated
source comments or build identity inputs. Runtime consumer code is generated
from the table schema; stale schema consumers and stale generated table data
are rejected separately.

Qualification binds the complete actual Awsh executable, fixed Bash rcfile,
empty inputrc, terminal entry, and complete selected locale tree. The generated
qualified table is an output of qualification, not an adapter input to its own
receipt. Its manifest digest is checked independently; this does not exempt
any executable or behavioral asset from exact-byte qualification. Any change
to a bound adapter input requires rerunning the affected qualification before
support is retained, including changes made by later runtime leaves. No new
wire message, actor, timer, configurable table path, or Reploy capability is
introduced by this artifact separation.

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
partial-launch cleanup. A2.10 requires echo off before Readline entry, a separate
complete fresh workload termios reference, and exact restoration/readback under
the existing terminal lease before the post-`started_ack` helper success reply.
B2.3.3 corrects the existing readiness primitive; B2.6 integrates restoration
into the real start owner, and B2.7 recaptures the post-cleanup reference before
the next Readline entry. All consume existing phase budgets; Envoy remains the
start-deadline and lifecycle owner. Implement the A2.4 source checker and private active
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

**Requirement package B3. Envoy session foundation**

Implement launch, readiness, relay, and start coordination against B2's adapter
and qualified builds. The ordinary-return sequence below fixes the coordination
boundary; B3 may isolate later process-cleanup and split responsibilities with
deterministic peers. B5/B6 close its real split/descendant-cleanup cases, B7
closes inspection cases, and B8 closes terminal failure variants. B3's closeout
does not require those later implementations or claim their conformance.

Implement listeners, the controller-generated session-ID handshake, the
independent actor-local connect/hello/ready deadlines, one PTY, persistent
Awsh/Bash startup, shared PTY execution, exact byte relay, bounded control
writes, an empty `HISTFILE` for controlled Bash after application
environment delegation, and idle-session shutdown. Active-operation shutdown integrates B4–B6 later.
Own the exact Awsh exec handoff,
startup-output pump and 4,096-byte cap, build-entry comparison, complete public
`ready` write before terminal release, `ready.output_through`, and takeover of
incomplete launch cleanup. Own `execute.input_through` before private submit,
start the one operation-start timer before invoking the execution-mode setup
boundary, including B5's later split directory/FIFO setup; do not reset it when
that boundary returns. B3 uses a shared-PTY no-op setup boundary and an isolated
deterministic peer for setup delay/failure and rollback completion; real split
setup and rollback belong to B5. Own the serialized
internal `0x18 0x02` PTY write, the fresh pre-start drain and mark,
`start_release`/`started` ordering, complete public `operation_started` before
`started_ack`, acceptance of private `start_released`, and the operation-start
deadline and teardown coordination with the same setup/cleanup boundaries.
In B3's isolated foundation harness, use shared-PTY operations without authored
background descendants. Exercise the real Awsh/Bash `input_close` ->
`input_closed` -> state-bearing `prompt_ready` -> `completed` coordination with
an internal deterministic cleanup peer: it reports bounded cleanup completion
or failure before Envoy may send `input_closed`; it never replaces or adds a
wire frame, shell hook, or lifecycle owner. Inject peer delays and failures to
prove that the helper remains blocked and no reusable completion is published
before the cleanup boundary succeeds. This proves coordination only. B6 owns
the actual descendant census/termination/reap and cleanup deadline, B5 owns
split keepalive/EOF/removal, B4 owns lifecycle controls and terminal output
barriers, and B7 owns inspection. Re-run the same coordination with those real
implementations at their cumulative closeouts and the final B gate. The B3
harness is isolated evidence and must not be exposed as a complete production
execution path; unsupported later-slice behavior has no B3 conformance claim.

**Requirement package B4. Operation boundaries and controls**

Implement the real control decisions, timers, PTY ioctls, and public/private
ordering on B2/B3. Until B6 supplies real descendant tracking and cleanup,
isolated B4 cases use a deterministic census/cleanup boundary and a controlled
persistent Bash/helper foreground group; this does not prove the census or
cleanup implementation. Keep cases requiring actual descendants, split
streams, or inspection pending for B6, B5, or B7 respectively, then rerun them
with those implementations. The final acceptance clauses below retain their
full production invariants; a peer is only slice-isolation evidence.

Serialize cancel with the first attempted private
`execute` byte: retain later cancellation, allow `rejected` to commit pre-start
failure, and after `submit` finish public start and wait for `start_released`
before Envoy applies the ordinary started-operation cancellation path. The path
issues one `ioctl(PTY_MASTER, TIOCSIG, SIGINT)` directly on the retained PTY
master; when the queued request becomes actionable after `start_released`, the
existing grace deadline starts before foreground sampling. A successful ioctl
does not reset it, and a failed
ioctl is fatal with no terminal result. It uses the exact B4
controlling-terminal session and pidfd-backed foreground-group invariant; an
unprovable boundary is fatal before the signal. Queue a cancel first accepted between
public start and `start_released` under the same rule; it is never forwarded to
an Awsh lifecycle transaction.

Implement output barriers, completion, input, resize, cancellation, action
gates, planned finalization, and final drain. Linearize every accepted resize in
the Envoy output pump, close and carry its preceding `output_through` frontier
across the PTY before Envoy performs
`ioctl(PTY_MASTER, TIOCSWINSZ, winsize{ws_row: rows, ws_col: columns})`, and
acknowledge it with `resize_applied` only after the resize is applied. Cover
queue-order ties, PTY output
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
targets its current slave foreground group. Selection of the ordinary
started-operation lifecycle path starts the existing grace deadline before
foreground sampling; a successful ioctl does not reset it, and a
failed ioctl is fatal with no terminal result.
Before the ioctl, require Envoy's controlling-terminal session and
pidfd-backed census invariant to prove that every group eligible to become
foreground consists only of persistent Bash, an adapter helper, or a
current-operation descendant. Envoy must sample the group with
`ioctl(PTY_MASTER, TIOCGPGRP, &foreground_pgid)` and validate every live member
of a positive group against the controlled session ID and pidfd-backed
controlled-tree census. A positive group with no live member is a permitted
job-return/Bash-reclaim crossing: resample it under the already-running grace
deadline while racing `input_close` and `shell_exit`; accept a Bash-local fact
without signalling, or validate and signal the first later live eligible group.
Deadline expiry selects the lifecycle timeout. The kernel atomically targets the
then-current group in that session. An invalid sample, a live wrong-session or
unclassifiable member, or another unprovable boundary is fatal before any signal
or terminal result. Cover a clean pre-operation census, nested foreground
programs, a
foreground switch among controlled groups between `TIOCGPGRP` and `TIOCSIG`,
the permitted empty-group return/reclaim crossing and each of its three winners,
and fatal no-signal outcomes for an unexpected live member, wrong session,
invalid sample, unclassifiable member, and unprovable clean boundary. Do not add
an Awsh lifecycle signal or resize transaction. Prove that
`input_close` and `completed` remain the A2.5 shell-local return facts, that a
cancel during ordinary-return cleanup wins without a second signal or timer
reset and skips inspection, that a finalize after `input_close` is
observed-return-wins, and that `shell_exit` before timeout selection wins while
the later shell exit after timeout selection is only reap evidence. Cover a
complete valid `input_close` queued before timeout selection but read afterward:
Envoy must consume and discard it in pipe order, send no `input_closed`, enter
neither ordinary completion nor inspection, and still require `shell_exit` as
the selected timeout's reap evidence. Malformed or out-of-state frames remain
fatal.
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
expiry prevents the complete reply. If `gate_ready` is accepted before a
lifecycle request, Envoy publishes `operation_ready` and applies a later
lifecycle request from `Gated`; if the later private gate outcome is accepted
before a lifecycle request, Envoy publishes the corresponding
`operation_continued` or `operation_gate_interrupted` event and applies a later
lifecycle request from `Running`. If the controller locally enters `Cancelling`
or `Finalizing` before receiving any such committed public event, it must
consume the event as a crossed predecessor, remain on the lifecycle path,
schedule no handoff work, and await its terminal result. If a lifecycle request
is accepted first, Envoy does not send an unsent private `continue`, but consumes
Awsh's exactly-one outcome whether or not `continue` was sent, without public
gate telemetry, while continuing from `Cancelling` or `Finalizing`. Cover all
of those crossings and prove no acknowledgement repair loop or private
state-machine regression.

**Requirement package B5. Split execution**

Implement real split resources and barriers on B2–B4. Until B6 is available,
isolated cases use shared controlled commands and a deterministic descendant
cleanup-completion boundary before closing writer keepalives. Real descendants
retaining split writers and cleanup/EOF races remain pending for B6; B5 must
not claim those cases passed from its peer.

Own bounded rollback of every partial split setup, mode-0600 split FIFO
creation, and reader/keepalive ownership under B3's already-running
operation-start timer. Exercise setup success, every partial failure and
rollback, deadline expiry during setup, and cancellation queued across setup;
B3's setup peer is replaced by the real implementation for these cases.
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

**Requirement package B6. Process cleanup and exclusive observation**

Own the ordinary-return cleanup sequence below, integrating B5's split
barriers and B4's lifecycle/output boundaries; its full real conformance closes
at B6 rather than B3. B7 supplies the later inspection implementation.
For ordinary return, sequence the `input_close` proposal/timer boundary with
only the active operation ID and exact completion-helper PID; permanently close
operation input, terminate live authored descendants and reap adopted children
while preserving only the exact completion helper and descriptor-free Bash wait
records, close both split writer keepalives, drain both split readers to
independent EOF, remove the FIFOs, and send `input_closed`. Only then does Awsh
release the blocked `prompt_state` helper; Bash restores and verifies canonical
unset `INT`, clears its own wait records with reserved `wait`, proves the empty
job table and adapter state, recaptures the
final state in state-bearing `prompt_ready`, and Awsh validates it and resolves
paths before the terminal-control handoff and Readline re-entry. Awsh then sends
`completed` with the saved source status, physical cwd, and resolved inspection
plan. The one non-resetting five-second operation-cleanup deadline ends only after Envoy has
proved the final census and performed the fresh PTY drain/output-through
barrier after `completed`; workload inspection runs afterward under the
controller-owned operation deadline and its existing inspection-cancellation
timeout. A failure at any cleanup, helper, readiness, EOF, drain, or removal
step is fatal and emits no terminal operation result.

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
one `ioctl(PTY_MASTER, TIOCSIG, SIGINT)` on the retained PTY master. Selection
of the ordinary started-operation lifecycle path starts the existing grace
deadline before foreground sampling, and successful ioctl
return does not reset it; no Awsh lifecycle signal
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

**Requirement package B7. Workload inspection**

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

**Requirement package B8. Failure and isolation hardening**

Prove socket and private-descriptor isolation, stable failure classes, channel
loss, shell exit, malformed traffic, cleanup, and repeated shutdown behavior.
For ordinary selected-shell exit after accepted `start_released`, require one
complete terminal `shell_exit`,
no later `closed`, private EOF, and a zero-status Awsh reap. Prove that premature
EOF, a trailing result frame, reset, signal, or nonzero Awsh exit remains fatal
after either terminal result. A processed controller-requested shutdown instead
requires one terminal `closed`, private EOF, and a zero-status Awsh reap.
For that terminal reap, prove the existing timer mapping: before Envoy's first
attempted private `execute` byte, an empty-ID `shell_exit` follows the existing
`shell_ended` drain with no operation result; if `execute.input_through` is
complete, the already-active operation-start deadline governs terminal EOF/reap
and any pre-start split rollback; while that watermark is outstanding, capture
and retain the terminal-input-barrier epoch instead. Late input cannot start
operation-start for the discarded request. Final-drain starts when Envoy enters
the existing public drain and runs alongside the captured bound without
replacing or extending it. Until private closure and rollback complete, captured
expiry wins, including when both deadlines are due in one dispatch turn: take
fatal teardown with the corresponding best-effort `input-barrier-timeout` or
`operation-start-timeout` diagnostic, no operation result, and no successful
public `closed`. After successful closure and rollback, that epoch ends and
remaining supervision/output drain uses the already-running final-drain budget
without reset. Prove these near-deadline crossings and final-drain expiry after
successful private closure. At or after that first byte and before accepted
`start_released`, `shell_exit` is a fatal start-phase crossing under the
unchanged operation-start deadline with no public operation result; its wire ID
remains empty until Awsh validates and registers the complete private
`execute`, then carries the active operation ID. Both branches retain bounded cleanup and the existing
private EOF and zero-status Awsh reap evidence; premature EOF, reset, or a
nonzero/signalled Awsh exit remains fatal. For an active-operation `shell_exit`
after accepted `start_released`, terminal EOF/reap retains the captured
terminal-input-barrier epoch if `continue.input_through` is outstanding;
otherwise it uses the Envoy operation-cleanup deadline. A later cleanup epoch
cannot extend that captured private-closure bound or win a same-turn expiry.
An idle `shell_exit` or `closed` remains under the already
running Envoy final-drain deadline. Terminal evidence replaces or resets no
governing epoch and leaves all existing five-second budgets and failure mappings
unchanged. Also inject valid `protocol_error` in launch, Ready/Idle, every
start phase, Running, Gated, Continuing, grace, cleanup, live inspection,
inspection cancellation, and drain: retain the active epoch without reset, or
enter Envoy-initiated final drain to start the existing five-second epoch when
none is active. EOF without an accepted `shell_exit` or `closed` remains
fatal, and every zero, nonzero, or signalled Awsh reap is fatal teardown
evidence only; preserve the existing nonzero `shell-launch` exit. Missing EOF,
an unreaped Awsh, or expiry is an additional bounded teardown failure with
the original cause retained. Retain the bounded
code/message and emit no synthetic shell status or operation result. Add the
same explicit `execute.input_through` and
`continue.input_through` crossings: before operation-start, retain the active
terminal-input-barrier epoch without reset and suppress the ordinary
`input-barrier-timeout` result; the sender's individual control-write deadline
does not control it, and an individual control-write timer alone cannot
suppress the existing immediate fatal drain in timerless phases. Include valid,
malformed, and unknown-code variants, queued cancel, and an outstanding resize;
require bounded private closure and Awsh reap under the unchanged epoch.
EOF without an accepted `shell_exit` or `closed` remains fatal, and zero,
nonzero, or signalled Awsh reap is fatal teardown evidence only. Preserve
the existing nonzero `shell-launch` exit; a zero status cannot make that EOF
orderly. Missing EOF, an unreaped Awsh, or expiry is an additional bounded
teardown failure with the original cause retained. Emit no operation result or synthetic status and report Reploy
termination.
Cover both orderings of `shutdown` crossing an idle persistent-shell exit:
observed shell exit first sends terminal `shell_exit` and resolves
`ShutdownSent` through `shell_ended`, while accepted shutdown first preserves
the requested shutdown reason whether Awsh sends `closed` or a crossed terminal
`shell_exit`.

Gate: the complete local Envoy/Awsh conformance suite passes, including every
B2–B8-owned executable case above with the real local actors and actual qualified
Bash builds. Static expectations and deterministic peers do not satisfy this
gate. C2/C3 controller and C5/C6 staging/blueprint proofs remain gates of their
own later slices, and all apply at the terminal-only milestone. No live Reploy
or terminal-runner integration is required to review the individual B slices;
verified Reploy Bash-build inputs are required for the corresponding real-build
qualification and local conformance evidence.

### C. Controller and Reploy boundaries

**Requirement package C1. Public Reploy codecs**

Extract strict public controlled-session event, request, and host-result codecs
with the broad fixture corpus, without controller lifecycle or subprocess code.

**Requirement package C2. Controller lifecycle state machine**

Implement lifecycle ordering, attachment startup, completion, termination,
acknowledgement, cancellation, stderr retention, and failure handling against a
deterministic fake client. Accept ordinary completion from the controller's
`Cancelling` state when the serialized inspection result wins before
cancellation.

**Requirement package C3. Envoy session client**

Implement the Python terminal/telemetry client against the canonical Envoy v1
fixtures, including inspection results and cross-channel output barriers. Its
readiness path buffers bounded terminal bytes received before public `ready`,
appends them only after validating `ready.output_through`, and permits the
first planned prompt or request only after the raw log reaches that barrier.
Validate source UTF-8, size and reserved namespace plus authored terminal input
against both reserved Readline sequences before `execute`; keep
`execute.input_through` controller-local and require the exact public
`operation_started` barrier before operation-authored terminal input.

**Requirement package C4. Runtime build artifact**

Add reproducible platform builds and the manifest for Envoy, Awsh, and their
required runtime files. Package B2's qualified canonical digest-keyed Bash-build
table as the manifest-bound `etc/bash-builds.json` payload and regenerate
its host preparation, Envoy, and Awsh schema consumers using the same
generator; reject stale consumers and unsupported or unqualified entries.
C4 does not originate the table or defer its first executable qualification.
If packaging changes the adapter or trusted-input bytes used for qualification,
rerun B2's harness against the packaged assets before accepting the affected
entries and artifact; earlier qualification of different bytes is insufficient.
The table contains the system rc path,
startup-export transform, catchable-signal inventory, startup Readline behavior,
source-loader/submit-macro keymap, no-redisplay, UTF-8 cursor, and maximum-line
behavior, and exact bounded startup PTY bytes. Build and manifest the fixed
`etc/awsh-bashrc` with
its output-empty primary-prompt and `PS0` hooks, readonly adapter namespace,
canonical parser state with POSIX mode disabled and `POSIXLY_CORRECT` unset and
readonly, whole-request readonly `trap` and `enable` mediation,
private bindings, positive helper markers, split-entry sentinel, post-`PS0`
release signal, and readonly fail-stop wrapper. Build the
same manifested `bin/awsh` with its fixed socket-helper requests, bounded
stream-framing validation, and
argument-free non-returning `bash-fail-stop` mode, plus the fixed empty
`etc/inputrc`.

**Requirement package C5. Runtime staging**

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

**Requirement package C6. Blueprint schema and composition**

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

**Requirement package C7. Controller run input**

Prepare the bounded `omegaflow-controller-run-v1` manifest and declared assets,
stage them as a read-only controller-only `/omegaflow-input` mount, and add the
internal controller command that validates the schema, paths, hashes, bounds,
and recording plan before starting the session client.

**Requirement package C8. Controlled-session invocation**

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

**Requirement package D1. Envoy-backed terminal runner**

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

**Requirement package D2. Direct terminal artifacts**

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

**Requirement package D3. Isolated Reploy end-to-end proof**

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

## One-PR implementation delivery catalogue

This catalogue partitions the requirement packages above; it does not replace
or weaken any clause or case in them. Every leaf inherits its package's exact
actors, wire forms, failure/result rules, deadlines, bounds, and acceptance
cases for the responsibility named in its row. The last leaf of each package
runs its cumulative package gate. The final B8.4 and D3.3 gates remain mandatory.
The original catalogue has 64 requirement anchors. Its B2.3 implementation was
split into B2.3.1/B2.3.2, and A2.10 adds the B2.3.3 owning correction below.
The retained B2.3 row is a historical requirement anchor, not an executable
selector. The progress ledger records approved partial delivery; estimates
are not implementation or qualification evidence.

### Ordering and proof accounting

Execute B, then C, then D. Within each phase use table order; each leaf requires
its preceding leaf's approved implementation in addition to the named semantic
prerequisites. The first B leaf requires the approved merged A2.7–A2.9 boundary;
C1.1 follows B8.4 and D1.1 follows C8.2. This conservative order is intentional:
no future implementation is a prerequisite for an earlier leaf's isolated
acceptance. `B2` or another package in the prerequisites column means all its
leaves and cumulative gate. The table names dependencies that explain the
acceptance boundary, not permission to bypass earlier approvals.

For this correction, A2.10 follows approved B2.4, then B2.3.3 follows A2.10
and B2.4, then B2.5 follows B2.3.3. This preserves the approved predecessor
commits and adds no merge prerequisite. The existing static B2.3 case identities
remain requirement anchors: B2.3.1 owns launch, B2.3.2 startup coordination,
and B2.3.3 the corrected terminal handoff. Existing `B1-C031-no-redisplay` and
`B1-C064-fresh-termios` proofs retain their submission and completion owners;
their readiness primitive additionally needs B2.3.3. Preparation records each
exact owning test and pending cross-actor closure, without claiming that a
historical static case proves the amended runtime behavior.

Verified Reploy candidate acquisition/provenance and the resolved regular
`/bin/bash` placement are prerequisites for the first real-shell test in B2.3,
not merely for B2.8 support admission. B2.1/B2.2 can use synthetic inputs for
schema/transport tests without that external evidence. B2.3–B2.7 use those
verified candidates in an isolated test tree without advertising support. B2.8
admits only measured entries. Every later leaf that changes the adapter or
trusted-input bytes must rerun B2's harness for affected entries and regenerate
consumers before retaining support or running dependent real-actor acceptance;
this includes B4.3's gate rcfile/helper change and C4.2's packaged assets. A
prior clean qualification of different bytes never satisfies that leaf.

A leaf may test its real implementation with deterministic peers only at the
already approved internal boundaries. Test-only peers cannot be enabled through
production configuration or claimed as actual missing actors. Earlier leaves
may implement modules without an executable entrypoint until safe assembly is
available; do not ship a partially safe runtime path. Incomplete integration
cases stay explicitly pending, with their last prerequisite and closure leaf:

- B1 static cases acquire concrete Bash-dependent expectations in B2.8, real
  startup/start evidence in B3.4, controls/gates in B4.4, split resource evidence
  in B5.3, actual descendants/EOF/lifecycle evidence in B6.5, inspection races
  in B7.3, and full failure/isolation evidence in B8.4.
- B4 gates and lifecycle use the approved isolated census/cleanup peer until
  B6.1–B6.5. B5 uses the cleanup peer until B6.3. These are acceptance dependencies
  only for the crossing proofs, not a circular prerequisite for B4 or B5 code.
- Real ordinary-return cleanup and split EOF close in B6.3; exclusive ranges and
  finalization close in B6.4; actual foreground/cancel/helper crossings close in
  B6.5; Python compiler rejection closes separately in B6.6; inspection/lifecycle
  crossings close in B7.3. B8.4 reruns all B cases.
- Controller crossings close in C2.3 with the fake client, then C3.3 with the real
  client and real local actors. C4.2 requalifies changed packaged bytes. Staged
  asset behavior closes in C5.2; final composed launch validation in C6.3;
  actual Reploy termination in C8.2 and real bootstrap/launch isolation in D3.2.
- Runner assertion integration closes in D1.3, reusing B6.6 compiler rejection. Artifact frontier and resize schedules close
  in D2.4. The full real isolated workload and presentation/routing/failure
  matrix closes in D3.3; it cannot be replaced by any isolated predecessor.

During B1.3, assign a stable case ID to every shared-inventory case and map it to
its static fixture, requirement package, implementation leaf, exact prerequisites,
and closure leaf. Later leaf preparation must refine this mapping for every
clause it implements, including cases found in package bodies outside the shared
inventory. Record actual test commands/results and pending crossings in each PR.
The package's last leaf checks that no case is lost or ambiguously owned; a missing
case prevents closeout. A discovered production fix belongs to the leaf owning
that behavior, or a newly reviewed correction leaf if its PR is already approved;
proof-only leaves cannot absorb unrelated subsystem rewrites within spare budget.

### B delivery leaves

| Slice | Production estimate | Semantic prerequisites | Owned implementation | Acceptance and retained pending proof |
| --- | ---: | --- | --- | --- |
| B1.1 | 400–650 | A2.7–A2.9 merged | Go public wire models, encoders/decoders, public bounds and validation | Public nominal/malformed/maximum corpus; no runtime actors |
| B1.2 | 450–700 | B1.1 | Private and helper wire models, length framing, strict scalar/nested JSON validation | Every private/helper form, direction, fragmentation and concatenation; no Bash execution |
| B1.3 | 100–300 | B1.1–B1.2 | Static trace/case indexing and corpus completeness tooling | Complete B1 inventory, synthetic startup bounds, ordering/deadline traces and leaf/closure mapping; concrete build values pending B2.8 |
| B2.1 | 350–600 | B1 | Canonical candidate/qualified table admission validator, consumer generator and executable qualification runner | Reject unknown/stale consumers; candidate metadata cannot authorize production support; actual measurements pending B2.8 |
| B2.2 | 350–650 | B1.2, B2.1 | Awsh helper socket transport and non-returning fail-stop helper mode | Exact framing, bounded stream loops, half-close/EOF, ancillary/trailing-byte rejection and fail-stop behavior |
| B2.3 | 450–700 | B2.1–B2.2; verified candidate inputs and /bin/bash placement | Selected-shell launch, fixed startup assets, topology, descriptor intake/lease and readiness | Real candidate Bash launch, prompt-empty startup, termios, shell reap and partial-launch cleanup against deterministic Envoy; not yet a supported entry |
| B2.3.1 | 450–750 | B2.1–B2.2; verified candidate inputs and /bin/bash placement | Descriptor handoff, controlling-terminal topology and raw selected-shell launch | Real candidate launch, parent/session/foreground and signal reset, descriptor isolation, partial-launch failure and reap; approved PR48 |
| B2.3.2 | 450–750 | B2.3.1 | Ordered startup helpers, fixed assets, terminal leases and initial readiness | Real candidate empty prompt, complete termios/readiness transition, terminal drain/slave closure and bounded cleanup; approved PR49; A2.10 handoff correction belongs to B2.3.3 |
| B2.4 | 450–700 | B2.3 | Readonly namespace, canonical parser state and whole-request trap/builtin mediation | Direct/expanded/mixed mutations, selected-build aliases/numbers, POSIX prevention and nested-shell normal behavior |
| B2.3.3 | 80–220 | A2.10 approved; B2.3.2/B2.4 | Echo-off Readline preparation, retained complete workload termios reference and exact existing-lease restoration/readback primitive | Real candidate readiness, no retained or inherited lease, exact state restoration and fatal identity/write/readback/context failure; start integration pending B2.6, fresh completion reference pending B2.7, support admission pending B2.8 |
| B2.5 | 450–700 | B2.3.3, B2.4 | Source checker, canonical source frame, loader/submit Readline bindings and status restoration | Syntax/output-empty checker, suffix isolation, bounds, markers, no redisplay and reserved-input rejection |
| B2.6 | 350–650 | B2.5 | Awsh active start record, PS0/post-PS0 handshake and split-entry sentinel | Every start/helper phase with deterministic Envoy, fail-closed markers and redirection failures; actual split setup pending B5.3 |
| B2.7 | 450–700 | B2.6 | Completion hook, helper identity, Bash inspection-path resolution and state/Readline handoff | Real Bash persistent state and resolved inspection plans, SIGINT-ignore window, empty jobs and final state validation against cleanup peer; real descendants pending B6.3/B6.5 |
| A2.11 | 0 | B2.7, B1.3.1 approved | Four-document manifest-bound qualification-output amendment and current attestations | Deep design review, required checks and exact-head approval; no runtime or support claim |
| B2.1.1 | 150–300 | A2.11 approved; B2.1/B2.7 | Fixed manifest-bound table payload, data-independent schema consumers and exact qualified runtime lookup | Strict fixed-path/manifest/schema/input rejection, stale generation checks, qualified launch in isolated test-only assembly linking the existing launch module, and stable exact Awsh bytes when table data is regenerated; no production entrypoint or B3 implementation prerequisite; B2.8 retains genuine target/all-case qualification |
| B2.8 | 100–350 | B2.1–B2.7, B1.3.1, B2.1.1; verified Reploy build inputs and resolved /bin/bash placement | Qualification measurements, supported-entry admission and generated consumer integration | Genuine per-target evidence for each advertised amd64/arm64 entry; complete B2 suite against deterministic Envoy; missing external inputs block this leaf |
| B3.1 | 350–600 | B1, B2 | Envoy listeners, session handshake, actor-local deadlines and bounded channel writes | Actual transport/IDs; deterministic startup peer; real startup pending B3.2 |
| B3.2 | 450–700 | B3.1, B2.8 | PTY/Awsh launch, startup pump/table comparison, ready barrier and launch cleanup | Real qualified Bash/Awsh; exact 0–4096 startup bytes, complete ready before terminal release and idle shutdown |
| B3.3 | 400–650 | B3.2 | Byte relay, stream marks, input watermark and operation-start sequencing | Fresh drain, setup timer, serialized submit and public/private start barriers; isolated setup/cleanup peers |
| B3.4 | 250–500 | B3.3, B2.7 | Ordinary-return coordination and foundation assembly | Real Bash/Awsh return frames with bounded cleanup peer; full B3 coordination suite; actual split/descendants/inspection pending B5/B6/B7 |
| B4.1 | 350–600 | B3 | PTY output frontiers, resize serialization and direct TIOCSWINSZ | Idle/active/Starting resize, continuous output, drain winners and fatal ioctl failure; split equivalent pending B5.3 |
| B4.2 | 450–700 | B3, B4.1 | Cancel/finalize decisions, grace timer, foreground validation and direct TIOCSIG | Start crossings, empty foreground resampling, no-signal failure and exactly one signal with census/cleanup peer; actual descendants pending B6.5 |
| B4.3 | 350–650 | B4.2, B2.2 | Fixed awsh gate function/helper, continuation watermark and committed outcomes | Hostile PATH, short writes, one reply deadline, gate-ready/continue/interrupted and input-barrier failure; requalify affected adapter/trusted bytes before retaining support |
| B4.4 | 150–400 | B4.1–B4.3 | Integrated control dispatch and crossed outcome handling | B4 isolated suite including completion-helper window and queued frames/timer selection; real split/cleanup/inspection crossings pending B5.3/B6.5/B7.3 |
| B5.1 | 350–600 | B3, B4 | Split setup, private FIFO modes, readers/keepalives and bounded rollback | Every partial failure, original start deadline, queued cancel and rollback; replaces B3 setup peer |
| B5.2 | 400–650 | B5.1 | Separate stdout/stderr pump, exact logical byte ranges and sender marks | Interleaving and zero-byte boundaries without normalizing logical streams |
| B5.3 | 250–500 | B5.2, B4.1 | Split resize frontiers and cleanup-completion/dual-EOF/removal integration | Complete B5 isolated suite; cleanup peer before keepalive close; actual writer-retaining descendants pending B6.3 |
| B6.1 | 450–700 | B2–B5 | Envoy subreaper, pidfd lifetime identity and repeated /proc census | Real controlled-tree identity, rapid fork/adoption and foreground classification; no independent Awsh lifecycle |
| B6.2 | 400–650 | B6.1 | Descendant termination, adopted-child reap and bounded cleanup engine | Background/disown/nohup/setsid/double-fork cases, helper exclusion and outside-tree preservation |
| B6.3 | 400–650 | B6.2, B3.4, B5.3 | Ordinary-return cleanup, input closure, split EOF/removal and final drain integration | Replace cleanup peers; real helper blocking, job-table clearance, final census and one cleanup deadline |
| B6.4 | 300–550 | B6.3, B4 | Envoy exclusive evidence ranges, assertion eligibility and planned-end outcomes | Closed runtime ranges, real status versus finalization outcome and invalidation; Python compile-time rejection belongs only to B6.6 |
| B6.5 | 200–450 | B6.1–B6.4, B4.4 | Actual census/cleanup wiring into lifecycle controls and race handling | Complete B6 runtime suite; real foreground switches, helper survival, timeout reap, post-result cancel/finalize and split crossings; inspection pending B7.3 |
| B6.6 | 100–300 | B6.4–B6.5; existing Python plan compiler | Python compiler validation of interactive output assertions and continuation input | Reject output_contains/output_regex when any operation or continuation sends text/key/control; preserve wait_for synchronization; complete B6 cumulative gate; later runner wiring belongs to D1.3 |
| B7.1 | 350–600 | B2–B6 | Envoy bounded file/directory inspection algorithms and live resolution conformance | Exercise B2.7 Awsh path resolution with live state; native file compatibility, separately tagged directory digests, special entries, mutation races and limits |
| B7.2 | 300–550 | B7.1 | Restricted short-lived Envoy worker, channel isolation and typed results | No inherited session channels or controller filesystem probes; bounded result and reap |
| B7.3 | 250–500 | B7.2, B6.5 | Worker acceptance/cancellation serialization and lifecycle integration | Both inspection winners, finalize status preservation, blocked-worker fatal timeout; complete B7 suite |
| B8.1 | 250–500 | B2–B7 | Descriptor/socket isolation and malformed/channel-loss teardown hardening | Isolation, malformed/unknown-code traffic and channel-loss injection with actual local actors |
| B8.2 | 300–550 | B8.1 | Terminal shell_exit/closed/private EOF/Awsh reap and idle shutdown crossings | Exact terminal ordering, zero/nonzero/signalled reap distinctions and both idle shutdown winners |
| B8.3 | 300–550 | B8.2 | Captured deadline and protocol_error teardown crossings | Every start/barrier/grace/cleanup/inspection/drain epoch, same-turn expiry priority, queued cancel/resize; no new timer |
| B8.4 | 0–200 | B8.1–B8.3 | Local conformance runner/report assembly | Entire B1–B8 case map green with actual Envoy/Awsh and qualified Bash; no peers satisfy final gate; subsystem fixes require owning correction leaves |

### C delivery leaves

| Slice | Production estimate | Semantic prerequisites | Owned implementation | Acceptance and retained pending proof |
| --- | ---: | --- | --- | --- |
| C1.1 | 350–600 | B8.4 | Public Reploy event/request codecs | Strict controlled-session event/request corpus without lifecycle or subprocess code |
| C1.2 | 250–450 | C1.1 | Host-result codec and codec integration | Complete C1 nominal/malformed/maximum corpus |
| C2.1 | 350–600 | C1 | Controller startup/attachment/lifecycle core | Deterministic fake-client ordering, readiness and ordinary completion |
| C2.2 | 350–650 | C2.1 | Cancellation, termination, acknowledgement, stderr and failure retention | Fake-client cancellation/ack/failure paths and bounded retained diagnostics |
| C2.3 | 150–350 | C2.2, B4/B7 contracts | Crossed controller events and lifecycle assembly | Complete C2 fake-client race matrix including ordinary completion winning inspection cancel; actual client pending C3.3 |
| C3.1 | 350–600 | C1, C2, B1 | Python Envoy transport, strict decoding and readiness buffering | Fragmentation, bounded pre-ready terminal bytes and raw-log first-prompt barrier |
| C3.2 | 350–650 | C3.1 | Source/input validation, operation submission, telemetry/output frontier handling | Reserved source/input rejection, controller-local input_through and exact operation_started gating |
| C3.3 | 200–450 | C3.2, B8.4 | Inspection/control results and controller-client assembly | Complete C3 suite with real local Envoy/Awsh; crossed lifecycle and channel barriers; real Reploy termination pending C8.2 |
| C4.1 | 350–600 | B8.4, B2.8 | Reproducible runtime builds and manifest generation | Exact Envoy/Awsh/fixed rcfile/inputrc/terminal/locale asset identities and platform outputs |
| C4.2 | 200–450 | C4.1, B2.1/B2.8 | Qualified table packaging, regenerated consumers and package qualification enforcement | Reject stale/unqualified entries; rerun exact packaged changed bytes; complete C4 artifact gate |
| C5.1 | 350–600 | C4 | Manifest validation and private read-only staging | Paths/modes/types/digests/extra payload rejection, verified installed source and manifest-last assembly |
| C5.2 | 200–450 | C5.1, B2 | Staged asset conformance and trusted-asset validation | Exact staged rcfile/helper/inputrc/terminal/locale behavior and framing/fail-stop; complete C5 suite |
| C6.1 | 350–600 | C5 | Typed controller/workload blueprint models and Hydra composition | Complete resolved models, read-only controller config and retained YAML; no post-composition repair |
| C6.2 | 300–550 | C6.1 | Launch environment reserved-value composition and validation | Full forbidden-name/prefix matrix and exact final HISTFILE/INPUTRC/TERM/locale values |
| C6.3 | 350–650 | C6.2, C4/C5/B2.8 | Trusted mounts, resolved /bin/bash qualification and final launch preflight | Shadow/missing/mismatched asset rejection, exact build/system-rc selection and /run/omegaflow tmpfs plan; complete C6 gate |
| C7.1 | 300–500 | C2/C3, C5/C6 | Bounded controller input manifest and declared asset validation | Schema/path/hash/bounds/recording-plan rejection before client start |
| C7.2 | 200–400 | C7.1 | Read-only controller-only input mount and internal command assembly | Exact /omegaflow-input isolation and validated invocation; complete C7 gate |
| C8.1 | 350–600 | C1–C7 | Controlled-session capability preflight and separate deployment invocation | Linux/Docker/target/attachment/no-reconnect/private-environment matrix, recording-backend routing and trusted endpoints |
| C8.2 | 300–550 | C8.1 | Host result/stderr retention, partial artifacts and Reploy termination reporting | Real public invocation, fatal outcome termination request/result, complete C8 and independent C gates |

### D delivery leaves

| Slice | Production estimate | Semantic prerequisites | Owned implementation | Acceptance and retained pending proof |
| --- | ---: | --- | --- | --- |
| D1.1 | 350–600 | C1–C8 | Envoy-backed runner/session adapter and pre-cutover routing | Status/cwd/session behavior; omitted FIFO host versus explicit reploy/host matrix and no default cutover |
| D1.2 | 350–650 | D1.1, B4/B7 | Runner input/resize/Ctrl-C/gates/inspection and output policy adaptation | Existing runner behavior, produced outputs and structured diagnostics with local actors |
| D1.3 | 300–550 | D1.2, B6 | Runner assertion evidence and recording-end finalization integration | Reuse B6.6 compiler rejection; exact logical stream and PTY CRLF bytes, completed ranges/status, cancellation/failure invalidation; complete D1 gate |
| D2.1 | 300–500 | D1, C3 | Exact private raw log and distinct presentation/timeline event writer | Raw-byte retention, protocol-like terminal text, synthesized prompt/command ordering; no recorder PTY/process |
| D2.2 | 350–600 | D2.1 | Direct asciicast encoding and incremental presentation modes | Fragmented/invalid UTF-8, real/suppress/replace and authored output schedule; no asciinema record |
| D2.3 | 350–650 | D2.2, B4/B5 | Resize tagging and publication-frontier scheduler | Prompt/typing/Starting seams, delayed acknowledgements/watermarks and covered split prefixes |
| D2.4 | 100–350 | D2.1–D2.3 | Artifact writer integration and ordering conformance tooling | Complete D2 queue-tie/zero-duration/continuous-output/authored-order matrix and raw writer frontier gate |
| D3.1 | 100–300 | D1/D2, C8 | Real isolated Reploy demo harness and retained evidence tooling | Repeated nominal demo, curses/nested shell, routing and all presentation modes; not yet final milestone |
| D3.2 | 0–200 | D3.1, C6/C8 | Hostile-launch and startup/exit/cancel/cleanup failure proof scenarios | Real bootstrap and controlled-shell terminal/locale isolation, resource accounting and partial artifacts; fixes go to owning correction leaves |
| D3.3 | 0–200 | D3.2 and all preceding B/C/D leaves | Controller/artifact/result/ack failure proof and milestone closeout | Complete real terminal-only matrix, no pending mandatory case, retained artifacts/diagnostics and accounted resources; authorizes E planning only |

Proof-focused leaves B1.3, B8.4, D2.4, and D3.1–D3.3 must land concrete
tracked fixture, scenario, test, or existing-runner/report-tool changes in their
own PR, plus retained execution evidence. A closeout-only or empty administrative
PR does not satisfy a leaf. Use the existing test runners where possible; do not
invent a new framework to manufacture a production diff. Preparation must name
the exact deliverable and acceptance command before execution; code fixes found
by these leaves follow the owning-correction rule above.

The later E stacks stay deferred and need their own reviewed leaf plans under
the same one-PR and production-line rules; this catalogue does not authorize
browser/publication/host-parity/FIFO-cutover implementation.

## Progress ledger

| Slice | State | Evidence |
| --- | --- | --- |
| A1 | Approved prefix | PRs 23 through 25 are approved at their exact current heads |
| A2.1–A2.2 | Approved prefix | PRs 30 and 31 are approved at their exact current heads |
| A2.3 | Approved prefix | PR 33 is approved at its exact current head with current A2.3 attestations |
| A2.4–A2.5 | Approved prefix | Approved design predecessors; A2.5 PR 35 is merged |
| A2.6 | Approved and merged | PR 36 has the approved label and is merged at `ff71c4ba6c9bebc3a9193ee9f0c59a98fa0c6551` |
| A2.7 | Approved and merged | PR 38 approved at head `750209eec2946309c2abc93a47a91078099edb52` and merged at `f37cd3cdaddf6e04b80011e5b47ee21cb78aca27`; approved document/sidecar bytes and required checks verified |
| A2.8 | Approved and merged | PR39 at `31e9a497bca99139e00ef64c2a4c4042a4b89e78`; exact approved head `f0cc6f5031576e845958933976bcf8c08669efd7` |
| A2.9 | Approved and merged | PR40 at `e10e61614f25c06da12fba2610803126390be1e7`; exact approved head `0286b0255e321f26cdf84e833dd5b1b3a13ffcb3` |
| B1.1–B1.3 | Approved partial implementation | PR42 `36b59a73e346f908175fbee9fe9847284ea9aaa1`, PR43 `08bd24b3280584a3a9aeb970f11596e0122750a4`, PR45 `35632529e0770feab62f1279b1fbc64b3254569e`; static wire/case evidence only |
| B2.1–B2.2 | Approved partial implementation | PR46 `2d27b1e8c738e0287d3acf9e811d97cf11dd5296`, PR47 `47ef02f071d314522f4029647ea78a36935edff3`; no supported Bash admission |
| B2.3.1–B2.3.2 | Approved partial implementation | PR48 `9e25e3a0516ec67941e4c02c833c39ab4d3cf9f7`, PR49 `d843ed43536c60fd4548a7cc1cd0e5fbef8fc59f`; candidate launch/startup evidence; changed handoff requires B2.3.3 |
| B2.4 | Approved partial implementation | PR50 `9929a451ceb81918102f2e581f1b4ddcdd68200e`; real candidate reserved-state proofs; immutable predecessor |
| A2.10 | Approved amendment | PR51 `25a53b7bb4972717ab3754edea71614c3e707100`; echo-off entry and exact workload-state restoration |
| B2.3.3 | Approved owning correction | PR52 `b83b9085c301887153c7e8980ae27016d82151e4`; readiness primitive; approved PR49 unchanged |
| B2.5 | Approved partial implementation | PR53 `141ae5825a12df02746ccc9ef989f8f31c000fe6`; isolated source checker/frame/Readline proofs |
| B2.6 | Approved partial implementation | PR54 `f1c897051f01ba65d10e3aa78330d4d3154795f9`; start integration against deterministic Envoy; real split setup pending B5.3 |
| B1.2.1 | Approved owning correction | PR55 `04b542830225a2e081fed0757379b72435f39009`; private-codec successor; approved PR43 unchanged |
| B2.7 | Approved partial implementation | PR56 `72d90ec0ea6b426bb508dad48e1e98c1a52cb9af`; completion proofs against cleanup peer; actual descendants/inspection pending later owners |
| B1.3.1 | Approved owning correction | PR57 `7247feaefbc8ffb351f2655f4034bd8ec5be3b4d`; B1-C022 permits the native `trap -p CHLD INT` query; approved PR45 unchanged |
| A2.11 | Pending amendment | Separate manifest-bound qualification output; requires deep design review, current attestations, required checks and exact-head approval |
| B2.1.1 | Pending owning correction | Implements A2.11 schema consumer/table separation; approved PR46 unchanged |
| B2.8–B8.4 | Pending | Genuine supported-target qualification and real-actor conformance remain delivery requirements |
| C1.1–C8.2 | Pending | 19 one-PR leaves; raw material only |
| D1.1–D3.3 | Pending | 10 one-PR leaves; raw material only |
| E | Deferred | Requires terminal-only gate |

Update this table only from executed checks and current review state. Historical
PR labels and tests from superseded commits are context, not completion
evidence for the rebuilt stack.
