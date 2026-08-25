# OmegaFlow Workload Envoy Design

## Status

- Owner-approved direction through the external Awsh-supervisor feasibility
  gate. Exact local-review status belongs only to a hash-bound `.review` sidecar
  when one is present; this amendment and the rebuilt delivery plan require a
  fresh local and remote review cycle. Production Envoy, runtime, controller,
  terminal-runner, and browser changes in the former PR 9–13 stack are raw
  material, not accepted implementation evidence.
- Updated: 2026-08-25
- Initial scope: one persistent Bash backend for terminal execution and
  structured telemetry in Reploy-backed OmegaFlow recordings

This document is authoritative for the terminal-control, PTY-ownership, and
terminal-to-browser coordination portions of
[Reploy Recording Environments Design](reploy-environments-design.md). It
supersedes that document's Reploy-PTY marker design, but retains its broader
environment, blueprint, lifecycle, artifact, packaging, and migration direction.
It does not replace Reploy controlled sessions or their host-owned lifecycle,
isolation, output-retention, and cleanup guarantees.

The first implementation supports Bash only. Additional top-level shell
backends and a generalized backend plugin framework are deferred. An operation
may still start another shell or interactive program, but OmegaFlow treats that
child as opaque until control returns to the persistent Bash.

The controller and isolated-workload blueprint contract is specified in
[Reploy Recording Environments Design](reploy-environments-design.md). General
project discovery, source transfer, cache policy, default-image selection, and
public bootstrap UX remain separate product work; they do not block the bounded
Envoy integration slices.

### Recording toolchain and workload placement

`studio.recording_backend` and `studio.workload_backend` use the same typed
`host | reploy` value domain. Recording defaults to `reploy`, and the recording
controller runs in the OmegaFlow-owned Reploy toolchain environment. The `host`
recording value is schema-valid but fails capability validation before
preparation with a targeted unsupported-controller error. It carries no
maintained bare-metal implementation or fallback. A supported container runtime
is required for recording; OmegaFlow does not retain a separate bare-metal
browser, media, or publication toolchain.

`studio.workload_backend` selects only workload placement. It defaults to
`host`, where the packaged Envoy and `awsh` run against the host project
environment and connect to the Reploy controller. Explicit `reploy` selection
uses the isolated controlled-session workload specified by this design and
requires a complete workload blueprint. Reploy failures never fall back to the
host after explicit selection.

The current FIFO-backed local capture runner is migration scaffolding until the
host-Envoy path proves parity. Portable, bounded controller access to host Envoy
and application endpoints across supported container runtimes is the remaining
host-workload design boundary.

## Problem

OmegaFlow is not only a terminal recorder. It must coordinate planned terminal
operations with browser actions, checks, output ranges, diagnostics, and media
production.

A PTY carries terminal bytes, not shell semantics. From a generic PTY stream a
controller cannot reliably determine that a planned command completed, what its
status was, or what directory the persistent shell subsequently uses. Prompt
parsing is fragile, and an in-band completion marker can be produced
accidentally or intentionally by workload output.

Asciinema avoids this problem by observing only the lifetime of its direct
child, normally one persistent shell. It records that shell faithfully but does
not claim to observe individual commands inside it. OmegaFlow needs an
additional semantic boundary for the top-level operations in its recording
plan.

## Architecture

OmegaFlow supplies a small workload-side process named the **OmegaFlow Envoy**.
The Envoy owns the workload PTY and launches one external **Awsh supervisor**,
which directly launches and reaps one persistent selected shell. It exposes two
lease-private TCP channels to controller OmegaFlow:

1. A **terminal channel** carries interactive input and exact ordered terminal
   output. Ctrl-C is ordinary terminal input. Resize is requested through the
   telemetry protocol and applied to the Envoy-owned PTY.
2. A **telemetry channel** carries versioned structured operation requests,
   completion events, status, cwd, action gates, and diagnostics.

Terminal bytes are never parsed as telemetry. Reploy transports neither
OmegaFlow protocol and does not interpret shell operations. It supplies the
controller/workload network and the declared endpoint coordinates.

```mermaid
flowchart LR
    subgraph Controller["Controller container"]
        OF["Controller OmegaFlow"]
        REC["Direct asciicast and<br/>timeline writer"]
        OF --> REC
    end

    subgraph Reploy["Reploy-controlled session"]
        LIFE["Admission, private network,<br/>lifecycle, retained outputs,<br/>cancellation and cleanup"]
    end

    subgraph Workload["Workload container"]
        ENV["OmegaFlow Envoy"]
        PTY["Envoy-owned PTY"]
        AWSH["External Awsh<br/>supervisor"]
        BASH["Persistent Bash with<br/>initial adapter hooks"]
        CHILD["Commands and nested shells"]

        ENV <--> PTY
        ENV <-->|"private control<br/>and results"| AWSH
        AWSH --> BASH
        PTY <--> BASH
        BASH --> CHILD
    end

    OF <-->|"terminal TCP<br/>input and output bytes"| ENV
    OF <-->|"telemetry TCP<br/>operations and structured events"| ENV
    LIFE --- OF
    LIFE --- ENV
```

## Why the Envoy Owns the PTY

Making the Envoy the PTY owner gives it one place to:

- stream child output without waiting for command completion;
- relay interactive input without prompt interception;
- apply window-size changes and preserve normal `SIGWINCH` behavior;
- deliver terminal control characters such as Ctrl-C;
- observe persistent-shell exit and drain remaining PTY bytes; and
- keep terminal data separate from structured telemetry.

The first implementation must not add another PTY between the Envoy and Bash.
Bash uses the single Envoy-owned slave PTY as its controlling terminal, and
interactive operations attach their standard streams to that slave. To
preserve the existing non-interactive assertion contract, a split-stream
operation instead gives its evaluated command separate stdout and stderr pipes
owned by the Envoy; this is the same non-TTY shape exposed by the current
presentation-timed runner. The Envoy forwards those pipe bytes to the terminal
channel in observed order while retaining their stream identity as private
assertion evidence. The Envoy otherwise occupies the role that a recorder such
as asciinema normally occupies at the PTY master, with the additional telemetry
channel required by OmegaFlow.

Envoy passes the slave through the single Awsh exec handoff but never passes the
master. Awsh calls `setsid` and acquires that slave with `TIOCSCTTY`, making the
supervisor the controlling-terminal session leader. It then forks Bash into a
child that creates a distinct process group and installs the slave as its own fd
0/1/2 behind a launch barrier. Awsh then makes that group foreground and
releases the child to exec only after those steps succeed.
Awsh validates Bash direct parentage, `tcgetsid(slave) == awsh_pid`, and
`tcgetpgrp(slave) == shell_pid` before private readiness. It keeps `SIGTTOU`
ignored for its own terminal-control ioctls; the Bash child resets job-control
signal dispositions to default before exec. Awsh retains one close-on-exec,
control-only slave descriptor until Bash is reaped, never reads or writes
terminal bytes through it, and uses it only for identity checks, submission
capsule termios operations, and the atomic cancellation lookup. A partial
launch is killed and reaped and never reaches public `ready`. Those steps make
Ctrl-C, Bash job control, resize, and finalization rely on a real terminal
relationship rather than an assumed one.

Controller OmegaFlow writes the asciicast and action timeline directly, merging
two ordered sources without another PTY: controller-synthesized presentation
events for the planned prompt and displayed command, and the exact bytes
received on the terminal channel. The prompt and displayed command are not
shell output. Before sending an `execute` request, the controller commits the
planned prompt, typing-start, character-timed display, newline, and typing-end
events to the cast and timeline. Operation source subsequently reaches Bash as
a cooperative
bracketed-paste submission written only by Envoy through the PTY master. Envoy
first drains and retains every preceding PTY byte, then opens a discard interval
for only the injected source echo and Readline redraw. After the blocked `PS0`
helper reports `started`, Envoy drains that interval through `EAGAIN`; none of
its bytes enters the raw log, socket, marks, or offsets. Envoy then commits
`output_start` and releases Bash only after the private start acknowledgement.
Only then may the operation emit retained terminal output.
After completion, the controller waits for the protocol's output-through
barrier and commits any buffered output or replacement at its compiled
publication point before synthesizing a following prompt. This preserves typing
behavior and makes presentation ordering independent of prompt parsing or PTY
echo.

The controller records raw output arrival times at its boundary and records
applied resize events from telemetry. Each accepted resize carries the
output-pump frontier closed across the PTY master and every active operation's
split stdout/stderr pipe immediately before the Envoy applies it. The controller
waits for the private raw log through that frontier before publishing the
resize, so output the pump already ordered ahead of it cannot arrive later on
the independent terminal connection. The controller classifies a resize when
it sends the request. One sent during synthesized prompt or typing retains that
span tag through acknowledgement and publication: an acknowledgement dequeued
before the span closes uses the then-current authored frontier, while one that
arrives after commitment uses the final prompt-and-typing frontier. A resize
accepted after `execute` is sent while the controller remains in `Starting`
belongs to the closing seam of the preceding prompt-and-typing span. The
controller buffers it through
`operation_started` or the pre-start failure, cancellation, or drain that
replaces that boundary, then publishes a matching applied resize at the final
typing frontier so input barrier and transport delay do not enter the cast. A
drain that instead resolves the still-outstanding request publishes no resize
event. From `operation_started` through the terminal event, a resize for a
presentation-timed operation belongs to that operation's authored span even
when the writer sees it before compiled publication starts. The controller
buffers it until the schedule is known. `output_through` defines a covered
prefix for each logical stream, and the writer places the resize after the
latest authored event derived from any covered prefix. A frontier covering no
authored byte places the resize at the pre-span time. In a split stream's
stdout-then-stderr authored order, covered stderr places every stdout event
before the resize, including stdout observed later in raw order, while
uncovered stderr remains after it. Thus authored order wins where it cannot
also preserve both sides of the raw frontier, without ever placing the resize
before covered output. This prevents command wall time from entering the cast
while preserving the Envoy's output ordering. If a shell-ended drain wins a
race with an
outstanding resize, receipt of `draining` resolves the request and the
controller publishes no resize event; a resize applied first is resolved by its
preceding `resize_applied` normally. Failure to apply a resize is instead a
fatal session failure in every accepted state: the Envoy emits best-effort
`resize-failed`, no resize event or terminal operation result, closes its
channels, and exits nonzero; the controller retains partial artifacts, explains
the failure, and asks Reploy to terminate the environment. Arrival times drive
only a live view. The
recorded timeline is sender-assigned: realtime publication takes each range's
time from the Envoy's covering output mark, and presentation-timed publication
uses its compiled schedule instead. Whenever the timeline returns from an
authored schedule to sender time — after synthesized prompt and typing
presentation as well as after a presentation-timed operation — a signed session
offset is re-anchored at the Envoy-stamped boundary that ends the span, the
operation's start in the first case — or the terminal event that replaces it
when the operation fails or is cancelled before starting — and the mark closing
its range in the second. Anchoring there rather than on the next event to arrive keeps controller
scheduling and discarded command duration out of the recording without also
erasing a slow command's own startup delay, which falls after the anchor.
Transport delay and controller backpressure therefore cannot deform the
recording. The controller does not place an `asciinema record` process or
another PTY between the controller and the Envoy. Both workload backends use
this direct Envoy capture path and no host recording toolchain. The controller
also retains a private raw terminal-output byte log as the lossless source for
workload-output byte ranges and diagnostics. It is bounded per session, and
reaching that bound fails the capture rather than truncating the log, since a
truncated log would no longer be the thing the rest of the contract relies on.
Synthesized prompt and command events carry distinct timeline provenance and
never enter that raw log or its byte offsets. Because asciicast events are UTF-8
JSON text, the protocol specifies the decoding and invalid-byte policy
used for the presentation cast; that conversion never weakens the raw transport
or byte-log contract.

The operation's timing, execution shape, and output mode are fixed before
`execute`. The existing streaming path maps `realtime` plus `real` to a
PTY-attached operation; the existing non-streaming paths use split streams.
Observed bytes always continue into the private raw log, but the cast writer
applies the compiled publication schedule:

- `realtime` plus `real` incrementally publishes decoded PTY events as they
  arrive for a responsive live view, timestamped in the recording by the
  Envoy's covering output mark;
- presentation-timed `real` retains split stdout and stderr while the operation
  runs, then, only after completion, the output-through barrier, and the
  configured logical post-enter pause, publishes logical stdout followed by
  logical stderr without admitting command wall-clock duration or raw arrival
  timestamps into the presentation timeline;
- `suppress` publishes none of the observed range; and
- `replace` publishes none of the observed range and, at the corresponding
  buffered-output publication point after the output-through barrier, commits
  the configured replacement as a controller-presentation event.

The raw range is temporal transport evidence, not general process provenance:
PTY echo and output from background work are indistinguishable from the
foreground command's bytes while an operation is running. Output assertions
therefore run only in an exclusive-observation operation selected before
`execute`. Such an operation has an output-through drain barrier. All bytes
read from a PTY master, including terminal echo of controller-authored input,
remain ordinary PTY output. The workload owns its terminal modes outside the
temporary source-submission capsule, whose complete pre-Readline state is
restored before authored source executes. Linux does not expose a reliable
boundary proving that line-discipline processing and
delivery of resulting echo are complete, so the Envoy does not try to infer
echo provenance.

OmegaFlow instead rejects `output_contains` and `output_regex` when an
interactive operation or any continuation sends bytes through `text`, `key`,
or `control`. `wait_for` and `pause` send no bytes and remain compatible with
output assertions. `wait_for` matches visible terminal text, including echo,
and serves only as a sequencing mechanism; authors must not use a just-typed
string as evidence that the application responded. Exit status, `file_exists`,
and produced-output inspections remain available after authored input, and a
later non-interactive operation can verify output or file content. Failure
cancellation and user cancellation invalidate assertions instead of evaluating
partial input and output. Operation source may also end the shell,
and the recording keeps the shell's own behaviour. `exit 7`, an `errexit`
failure, an `exec` that replaces the image, and a crash are one outcome rather
than four: Awsh is the shell's parent, so it reaps the status in every case and
reports it explicitly to Envoy. The operation completes carrying that status,
marked as having ended the shell so the controller does not draw a prompt for a
shell that is gone, exactly as that terminal would have shown. Taking the
status from the reap rather than from a shell trap also keeps it out of reach
of operation source, which can install an `EXIT` trap of its own without
costing the operation its status. Envoy still terminates every remaining
operation-created process, reaps those it owns or adopts, supervises other
direct parents through reap, and drains output before reporting that status,
regardless of presentation or assertion mode. An operation that declared
inspections fails rather than
finishing with them unevaluated. Any later beat then fails,
since no operation can run without a selected-shell backend. If that shell-ended
drain crosses the later beat's unstarted `execute` and a deadline-derived
`cancel`, receipt of the drain resolves both controller requests; the Envoy
accepts and discards any late copies, emits no terminal operation result, and
the beat still fails as unrunnable. Planned recording-end
finalization is a distinct typed lifetime result: the Envoy terminates and
drains the intentionally open operation, closes its output range, and permits
the authored non-exit assertions to run over that complete range. Its synthetic
termination status never satisfies or fails an authored exit-code assertion,
because the operation did not exit naturally. Failure to complete the same
mandatory operation cleanup used after natural return and cancellation fails
closed. Telemetry `continue` messages and resize requests are not terminal data
input. A `continue` does carry the cumulative terminal-input watermark, so the
Envoy waits for independently transported input before releasing its gate. If
that watermark is not reached within five seconds, the blocked gate cannot
produce an ordinary adapter result: the Envoy emits fatal
`input-barrier-timeout`, returns no terminal operation result, closes the
session, and asks Reploy to terminate it rather than adding a gate-abort path.

A controller deadline remains cancellable throughout the finalization adapter
grace period, cleanup, and inspection. Cancellation moves the controller from
finalizing to cancelling. During the original adapter grace period it sends no
second signal and does not reset the timer: an Awsh result takes cleanup and
`operation_cancelled`, while expiry takes `cancel-timeout` with
`shell_ended: true`. During mandatory cleanup it finishes the existing
non-resetting cleanup deadline, skips inspection after successful cleanup, and
returns cancellation without inspection results; cleanup failure remains fatal
with no terminal operation result. Once inspection is running, the existing
supervised-worker race applies: a worker result accepted first commits planned
finalization, while cancellation accepted first stops and reaps the worker and
returns cancellation without inspection results. Failure to reap within five
seconds remains fatal `inspection-cancel-timeout`. A finalization result
committed before cancellation acceptance wins and discards the crossed cancel.

If the active Bash operation does not return through Awsh within the
cancellation grace period, whether cancellation or planned finalization
initiated it, Envoy terminates the shell process group, Awsh reaps and reports
the shell, and Envoy completes mandatory descendant cleanup and output drain,
then emits `operation_failed` with `cancel-timeout` or `finalize-timeout` and
`shell_ended: true`, and enters its `shell_ended` drain. The controller neither
synthesizes another prompt nor dispatches another operation to the dead shell.

Output assertions preserve the current `stdout + stderr` compatibility view;
they do not reinterpret the temporal presentation stream as that view. For a
split-stream operation, the Envoy forwards both streams to the terminal channel
and marks which offsets belong to which stream, and the controller slices its
raw log by that attribution and concatenates logical stdout followed by logical
stderr for `output_contains`/`output_regex`. Assertion evidence is therefore
the complete retained output rather than a bounded excerpt. For a PTY-attached
operation, stdout and stderr intentionally share the slave exactly as in the
current realtime runner, so its exact post-line-discipline PTY range is logical
stdout and logical stderr is empty. Stream identity is never guessed from
merged PTY bytes, and pre-line-discipline bytes are never reconstructed from
polled termios state. A PTY-attached assertion is permitted only when the
operation and its continuations send no authored terminal bytes; it then
matches the same CRLF conversion and other terminal transformations visible to
the current realtime runner. The raw log and published cast always retain the
exact PTY bytes. Split-stream evidence bypasses the terminal line discipline
and preserves newline-sensitive stdout-then-stderr checks.

`suppress` and `replace` always use exclusive observation. A checked `real`
operation uses it as well, and so does any presentation-timed operation. That
choice controls evidence and publication; it does not control process lifetime.
Protocol v1 has one lifetime rule for every operation. When the submitted Bash
source returns to Awsh, Awsh reports its status, cwd, and resolved inspection
plan, and the Linux Envoy terminates every remaining process created by that
operation and supervises its owner or adopted parent through reap before
reporting the terminal result. The Bash job table is
not the authority because `disown`, `nohup`, `setsid`, and daemonization can
hide descendants. The Envoy acts as a subreaper, retains pidfd identities, and
repeats `/proc` census, termination, adopted reap, EOF, and drain until only
external Awsh and persistent Bash remain. Cancellation and planned finalization use the same
cleanup. One five-second monotonic deadline covers the complete cleanup sequence
and does not reset on progress. Inability to prove the operation process set
empty before it expires fails the session without a terminal operation result;
the controller then asks Reploy to terminate the environment.
This census is correctness evidence within the documented same-identity threat
boundary, not security evidence.

Long-lived services belong in environment setup, outside the controlled
Envoy/Awsh process tree. V1 does not preserve a process across operations;
session-lifetime subprocess support may be added later if a compelling use case
cannot be handled by setup. Because no earlier operation process can remain,
every non-shell process adopted by the Envoy at the boundary belongs to the
current operation; a separate per-operation cgroup is not required for this v1
contract.

Filesystem expectations do not use terminal output or controller filesystem
access. The controller includes bounded `file_exists` and `produces` inspection
specifications in the operation request. After the command returns and before
the terminal result is committed, `awsh` resolves their configured paths in the
persistent Bash's resulting cwd and exported environment, then sends the
resolved inspection plan to the Envoy over its private descriptor. The Envoy
first performs the mandatory operation cleanup, proves that only the persistent
Bash remains, and drains output through the closing operation offset. It then
performs bounded workload-side existence and file-type checks. For `produces`,
the selected file or directory must remain one stable source state across the
complete inspection and deterministic SHA-256 calculation even though a
permitted setup service may remain outside operation cleanup. Any observed
change to the selected path or a traversed entry — including its identity,
kind, directory membership, symlink target, metadata, or bytes — or inability
to establish stability on the backing filesystem produces a typed inspection
failure with no digest or inspection result. Protocol v1 owns the concrete
race-detection algorithm and failure-code mapping for this invariant. Accepted
typed results contain the producer and output IDs, resolved path, kind, and
digest without sending file contents over telemetry. Cleanup or drain failure
takes the no-terminal-result session-failure path; resolution, inspection, or
hash failure produces a typed operation failure. None produces inspection
results. Controller OmegaFlow records accepted results as private run evidence
and never launches probe commands, reads workload paths, parses PTY bytes as
filesystem evidence, or publishes absolute workload paths and digests without a
separate sanitizing publication contract.

Envoy, external Awsh, and the selected-shell adapter are separate roles even
though OmegaFlow distributes them together. Envoy owns networking, public
framing, the PTY master, byte ordering, controller input and source
serialization, resize, cancellation, process-tree policy, draining, and
diagnostics. Awsh is Envoy's direct child and the selected shell's direct
parent. It owns the private operation protocol, launches and reaps the shell,
and translates one backend's operation lifecycle and state into shell-neutral
private results. The Bash adapter owns only Bash-specific hooks and source
framing. No shell-specific hook is part of the controller-facing protocol.

## Envoy, Awsh, and the Persistent Shell

Envoy starts one Awsh supervisor and passes the PTY slave plus only the private
runtime descriptors it needs. Awsh starts one selected shell on that slave and
keeps it for the recording. Top-level operations therefore share that shell's
supported persistent state. Awsh reports the selected shell's parent-observed
wait status explicitly; private-channel EOF is never used as a substitute for
an exit result.

```text
OmegaFlow Envoy
└── external Awsh supervisor
    └── persistent selected shell (Bash in v1)
        └── operation commands and nested interactive programs
```

If the selected shell exits first, Awsh reaps it and reports whether an
operation was active plus the real status and last valid cwd. If Awsh exits
first or its private channel fails, Envoy takes a fatal supervisor-failure path,
terminates the remaining selected-shell tree, drains bounded output, and emits
no invented operation result. During orderly shutdown Envoy asks Awsh to close
the shell; timeout escalates through Envoy's final-drain policy. Reploy remains
the authoritative environment-lifecycle owner in every case.

The controller submits planned operations through telemetry. Envoy validates
and forwards each operation to Awsh, and remains the only PTY-master writer.
Awsh arms its backend, asks Envoy to submit the framed source, reports the
backend's start barrier, and waits for Envoy's acknowledgement before releasing
execution. PTY operations keep all three standard streams on the slave.
Split operations keep stdin there while their stdout and stderr reach separate
Envoy-owned streams. Both shapes close through the same result, descendant
cleanup, EOF, drain, and output-through boundary.

The initial contract covers only operations submitted by OmegaFlow. If an
operation starts Fish, Zsh, Python, a TUI, or another interactive program, that
program is one opaque child operation. The terminal remains fully interactive,
but OmegaFlow does not claim command-level telemetry from inside it. The outer
operation completes when control returns to the selected persistent shell.

The design does not parse prompts or workload output for boundaries. Prompts
are presentation output; start, completion, gates, state, and shell exit arrive
through private Awsh results.

### Initial Bash backend

The shipped `/omegaflow-runtime/bin/awsh` executable is a supervisor, not a
shell implementation and not a Bash-resident request loop. Its initial backend
creates its private helper listener, then launches
`/bin/bash --noprofile --rcfile /omegaflow-runtime/etc/awsh-bashrc -i` as a
direct child on the single PTY slave. A versioned table generated into host
OmegaFlow and Envoy from one canonical source maps the resolved regular
`/bin/bash` SHA-256 digest to its compiled system-wide interactive rc path or
`none` and deterministic startup-export transformation. Preparation requires an
exact entry; Envoy re-hashes the binary and requires any declared path absent
before launch. The
manifested rcfile then replaces every user startup file and installs the small
immutable Bash bootstrap:

- `PS0` creates the redirection-only frame-entry marker after Bash parses the
  complete submitted conditional frame; Awsh validates it before accepting the
  helper's `start`, then the helper blocks immediately before that source unit
  begins and lets Awsh report `started` before Envoy releases Bash;
- `PROMPT_COMMAND` and prompt-readiness hooks require default `SIGCHLD`, report
  status, physical and validated logical cwd, exported environment, and
  `histexpand`, temporarily disable top-level history expansion, and return to
  input readiness;
- the readonly input-state condition restores the captured history setting and
  returns the captured status after parsing but before one authored branch
  executes;
- a readonly action-gate helper coordinates `gate_ready`, continuation,
  terminal gate interruption, and lifecycle cancellation without using terminal
  text as telemetry; and
- the marker proves that Bash parsed the complete submitted conditional frame
  before Awsh accepts `start`; its creation precedes the blocking `PS0` helper
  packet.

Those hooks invoke short-lived modes of the same manifested Awsh binary over a
private Unix `SOCK_SEQPACKET` endpoint. Envoy creates a fresh mode-0700 session
directory below `/run/omegaflow`; Awsh owns a mode-0700 Bash subdirectory and
mode-0600 helper socket inside it. Each helper makes one connection and receives
one bounded final reply. Non-gate modes send one bounded packet; a waiting gate
may send one additional `gate_interrupt` packet when terminal `SIGINT` arrives.
Packets report
start, prompt state/readiness, and gates; Awsh maps them to its sole armed or
active operation rather than trusting a Bash-supplied operation ID. The complete
exported environment is sorted and bounded rather than truncated. Invalid,
oversized, out-of-state, or failed helper traffic is fatal `adapter-state`.
No helper descriptor reaches Bash or an ordinary descendant, and the endpoint
is removed before Awsh closes. The pathname remains same-identity orchestration,
not a hostile-workload security boundary.

The first installed prompt hook reports one startup `prompt_state` and then one
startup `prompt_ready` while no operation is armed. Awsh accepts only that
order and records the initial shell state plus the complete workload termios
state. While the blocking `prompt_ready` helper still prevents Bash from
entering Readline, Awsh applies and verifies the entry sentinel, then
acknowledges the helper. It does not emit private `ready` until it observes
Readline clear both sentinel bits, captures that complete active state, and the
Bash parentage, session, and foreground-group checks pass. The existing startup
deadline owns rcfile execution and this handshake; terminal output and helper
closure are never substituted for actual Readline readiness.

Each immutable hook surrounds its external helper with a signal-safe adapter
critical section: Bash saves the exact user `SIGINT`/`SIGQUIT` traps, temporarily
ignores them, requires `DEBUG` and `RETURN` traps unset, and restores the exact
signal-trap state after the helper closes. `DEBUG`/`RETURN` are reserved unset
because a DEBUG trap can run before an adapter function can suppress its own
invocation. `SIGCHLD` is reserved at its default disposition. All three are
checked without spawning a process before every normal helper; an initial
change fails launch, a gate returns 125 without spawning, and a reached prompt
boundary is fatal `adapter-state`. This
avoids both running workload code on an internal helper exit and suppressing a
genuine workload-child event. Non-gate helpers
inherit and verify the ignored dispositions. The gate helper installs a
`SIGINT` handler before reporting readiness and translates terminal Ctrl-C into
its one optional interrupt packet. Awsh reports that packet to Envoy as a
proposal while the helper remains blocked. Envoy orders it against controller
continue, cancel, and finalize requests; when the proposal wins, Envoy emits
`operation_gate_interrupted` and only then acknowledges Awsh, which returns the
helper's status 130. A late cancel, finalize, or terminal Ctrl-C therefore
cannot kill a helper, strand the controller in its gated state, or turn an
ordinary result race into `adapter-state`.

`EXIT`, `ERR`, and ordinary signal traps other than `SIGCHLD` remain persistent
workload state. A trusted operation may use `DEBUG` or `RETURN` only transiently
and must remove it before a gate or source return. If it does not, the transition
is unsupported; a DEBUG trap may observe the first boundary command before the
adapter can fail closed, so no successful completion or preservation guarantee
is made for that path.

Operation source travels cooperatively through the PTY inside one submission
capsule. After fresh-Bash syntax preflight and rejection of the exact Readline
bracketed-paste terminator bytes in any Bash lexical context, Awsh requires the
current terminal state to equal the active state captured from the sentinel
transition, temporarily makes that input path byte-transparent, and verifies the
result. The source bound accounts for the two byte-identical authored branches,
and Awsh verifies the complete generated capsule and private `submit` frame
before arming. Envoy injects exactly bracketed-paste begin, that conditional
frame, bracketed-paste end, and `LF`; the validated `C-J` binding accepts that
line.
Readline must restore the exact entry sentinel; Awsh then restores and verifies
the complete workload snapshot before it accepts `start`. Bash parses the frame
with history expansion temporarily
disabled. Its immutable `if` condition restores the captured `histexpand` and
returns the captured `$?`; this condition is exempt from `errexit`, while the
one selected authored branch is not. The first authored command therefore sees
the exact prior status without losing normal `errexit` behavior. Envoy's
pre-submission drain preserves
legitimate output; its following serialized discard interval suppresses only
the injected bytes and Readline redraw before `output_start`. The start
transaction from satisfied input barrier through matching `started` has one
non-resetting five-second deadline. This is not a private Awsh-to-Bash source
channel, and the documents and implementation must not describe it as one.

The protocol's normative Bash-state matrix classifies cwd, variables/export
attributes, arrays, functions, aliases, positional parameters, traps, status and
transient special parameters, jobs, every supported `set -o` and `shopt` name,
history, and Readline state. Persistent user state is preserved except for
explicit adapter invariants and unsupported modes; the adapter never silently
resets it. `$?` is captured before helper execution and returned by the next
submission's input-state condition, while helper-affected transient values such
as `PIPESTATUS` and `$_` are explicitly unpromised. Operation-created processes
never persist
across the typed result, so `$!` and jobs are not persistent evidence. Source
whose grammar depends on unsupported persistent parser state such as a
previously enabled `extglob` is rejected before start by fresh-Bash preflight.
Aliases remain preserved only when every expansion satisfies the protocol's
closed simple-alias grammar for parser-neutral command and argument words. The
immutable prompt-state path rejects a grammar-bearing alias as unsupported
persistent state before Bash becomes ready for another submission, so Awsh
does not transfer aliases into preflight and ordinary supported aliases retain
their persistent effect. The generated frame's exact readonly input-state
condition name is also a forbidden alias key.

`PS0`, `PS1`, `PS2`, `PROMPT_COMMAND`, and the gate hook are immutable adapter
state. The default `SIGCHLD` disposition is reserved for every short-lived
helper. Readline's `enable-bracketed-paste` value, the exact
bracketed-paste-begin sequence, and `C-J` mapped to `accept-line` in both
supported line-entry keymaps are also reserved. The immutable prompt-state path
validates those bindings at startup and every reached prompt boundary, before
readiness. An initial mismatch fails launch and a later mutation is fatal
`adapter-state`; the adapter does not silently reset it. `C-M` and other
non-conflicting bindings persist. The independent frame-entry marker and the
termios restoration check remain fail-closed `adapter-framing` backstops. Thus
ordinary persistent key macros, history expansion, and workload terminal modes
cannot rewrite or prevent a submitted source unit, while all workload-visible
state is restored before source executes.

Controller terminal Ctrl-C and resize remain Envoy-owned PTY actions;
request-driven cancellation and finalization interruption is the atomic
Awsh-owned action below. A
timely Bash return preserves the shell for the next operation; `exit`, `exec`,
`errexit`, and forced termination instead produce Awsh's explicit `shell_exit`
result with the real reaped status. Envoy still owns descendant census,
termination, adopted-child reaping, output drain, and the public failure/result
mapping.

Envoy serializes an accepted cancellation or finalization against an Awsh result
already accepted by its event loop. When the request wins, it writes the private
request first and waits for one matching Awsh `disposition`: `signal` confirms
that Awsh selected and interrupted the foreground group inside the same
serialized source-execution classification; `gate-cancelled` confirms
one typed helper reply and no signal; `settled` protects prompt/completion helper
work; and `already-interrupted` prevents a crossed request from repeating the
action. Awsh retains the most recently written result until the next execute or
shutdown, so crossing the two private pipes remains valid rather than becoming
out of state. Envoy buffers such a result until the disposition arrives, then
maps the ordinary completion or shell reap according to the winning public
phase. The helper critical section makes a source-to-helper transition during
that atomic signal action harmless. A cancel crossing accepted finalization updates
the disposition without a second interruption or timer.

After an unrequested Bash reap, Awsh reports `shell_exit`, removes its helper
resources, and remains in a private shell-ended state long enough to accept
Envoy's final `shutdown`. It then sends `closed(shell_ended, status, cwd)` and
exits zero. Private EOF before that close handshake remains `awsh-failed`.

An idle Envoy-requested shutdown instead makes Awsh stop accepting private
work, send one `SIGKILL` to the known selected-shell process group, reap Bash,
close the helper endpoint and retained slave, and report
`closed(shutdown, 137, cwd)`. This protocol shutdown intentionally runs no Bash
`EXIT` or signal trap. If Bash wins the narrow race before the signal, Awsh
preserves its actual reaped status under the already-selected `shutdown` reason.
No PTY close or cooperative terminal submission is relied upon to terminate the
shell.

The cooperative bootstrap and same-identity helper resources are orchestration,
not a security boundary. Deliberately hostile same-identity source may attack
them. Such workloads use the controlled application path without OmegaFlow
recording; privilege separation remains deferred.

Awsh's stable extension boundary is behavioral rather than a third-party plugin
API: launch one selected persistent shell, arm one source unit, report start,
gates, completion state, and explicit shell exit, and close that shell on
shutdown. A future Zsh, Fish, or other backend may replace the Bash hooks and
submission details while preserving the private Awsh contract and the unchanged
controller-facing Envoy protocol. Only Bash is implemented initially; dynamic
adapter discovery and non-Bash conformance claims are deferred.

## Trust Boundary

The Envoy design is intended for OmegaFlow recording workloads that cooperate
with OmegaFlow's operation protocol. Its separation of terminal and telemetry
traffic prevents accidental output collisions and prevents terminal text from
being accepted as a protocol message.

The initial Envoy, Awsh, and Bash run under the same non-root Reploy workload
identity. Envoy binds and accepts the controller connections before starting
workload code, closes its listeners after accepting the single controller, and
retains the connected TCP sockets itself. It creates a distinct private
control/result pair for external Awsh. All parent descriptors begin
close-on-exec; only the child control end, result end, and PTY slave have that
flag cleared for the single Awsh exec, and Awsh restores it immediately before
starting Bash. Awsh and Bash inherit no Envoy socket; Bash and ordinary exec'd
descendants inherit neither private Envoy-to-Awsh descriptor. Bash helpers
connect to their bounded pathname socket instead of inheriting it. In split
execution only the chosen FIFO-backed standard fd 1/fd 2 reach the command; no
extra reader, keeper, control, or helper descriptor does. These measures prevent
accidental inheritance; they do not isolate the three same-identity processes
from deliberate interference.

Workload inspection has the same boundary. Closing every tracked
operation-created descendant prevents cooperative background mutation during
hashing, but a different same-identity workload process can still interfere.
Inspection results are reproducibility and correctness evidence, not
tamper-proof security evidence.

The production Envoy starts a fixed Awsh executable with a controlled launch
environment, and Awsh starts the fixed Bash backend from that environment. The
launch path must neutralize shell startup, input-binding, and option
injection such as `BASH_ENV`, `ENV`, `HISTFILE`, `INPUTRC`, `SHELLOPTS`, and
`BASHOPTS`, and it must not honor the prototype-only `AWSH_BASH` override. It
also removes incoming `TERM`, `TERMINFO`, `TERMINFO_DIRS`, `LANG`, `LANGUAGE`,
and `LOCPATH` and every incoming `LC_*` name before installing the
manifest-validated launch values from the environment contract:
`TERM=xterm-256color`, both terminal-database variables naming the read-only
`/omegaflow-runtime/share/terminfo` database,
`INPUTRC=/omegaflow-runtime/etc/inputrc`, the fixed Bash rcfile at
`/omegaflow-runtime/etc/awsh-bashrc`, `LC_ALL=C.UTF-8`, `LANG=C.UTF-8`, and
`LOCPATH=/omegaflow-runtime/lib/locale`. Before starting Awsh, Envoy
verifies the readable trusted terminal entry, the empty regular Readline file,
the regular fixed Bash rcfile, and the complete regular-file-only trusted locale tree against the mounted
runtime manifest and fails startup on a missing, unreadable, non-regular, or
hash-mismatched asset. It also verifies the Bash digest against the generated
build table and that the selected entry's system-wide interactive rc path is
absent. Terminal,
Readline, locale, and Bash startup lookup cannot fall through to
application-controlled data under `HOME`, `/etc`, or another search directory.

Application environment required by planned operations is delegated
explicitly, then the Envoy installs the trusted terminal, Readline, and locale
values and an empty `HISTFILE` as the final launch values so neither an
application path nor a default under `HOME` is opened before Bash accepts the
adapter bootstrap. Operations may deliberately change those values only after the
controlled Bash has started. The protocol specifies the exact filtering
and delegation contract.

Preparation derives the exact first `prompt_state` exported environment after
that filtering, reserved-value installation, selected working directory,
selected table entry's startup-export transformation, and fixed rcfile. It applies the same
canonical compact-JSON rules as the adapter and rejects the workload before
deployment if the complete set would exceed 1,024 entries or 49,152 encoded
bytes, or if the exact set cannot be proven. Runtime source can still overflow a
later report, which is fatal `adapter-state` rather than truncation.

All OmegaFlow-supplied workload executables, scripts, helpers, and their manifest
are mounted read-only and executable at `/omegaflow-runtime`. This prevents the
workload from replacing their on-disk bytes but does not protect the live Bash
state, the Envoy process, or same-identity IPC from deliberate interference.

A future privilege-separated mode may run Envoy as root and Awsh plus the
selected shell as a configured non-root workload identity. Current Reploy
application containers provide one runtime identity with empty capability sets and
`no-new-privileges`, so that mode requires a new Reploy contract and is deferred.
The same missing supervisor/workload identity boundary also affects Flux.

Neither the initial same-identity model nor a future privilege-separated model
makes shell-originated telemetry security evidence. OmegaFlow telemetry controls
recording sequencing; it cannot grant Reploy capabilities, select host
resources, establish successful Reploy termination, or override Reploy's
authoritative lifecycle result.

For generic or more adversarial workloads, Reploy's existing host-owned PTY is
the stronger model: the controller drives the workload without relying on an
in-container terminal proxy. That capability remains useful independently of
OmegaFlow's richer Envoy protocol.

## Reploy Boundary

OmegaFlow continues to use `reploy controlled-session run` for:

- exact controller and workload generation admission;
- private controller/workload networking and declared endpoint coordinates;
- container isolation and fixed capabilities;
- controller output retention;
- cancellation, lifecycle observation, cleanup, and crash recovery; and
- the authoritative final host result.

Runtime integration requires a prepared workload blueprint that adds two
private Envoy endpoints and the read-only `/omegaflow-runtime` mount.
Controller OmegaFlow obtains their lease-local coordinates from
`opened.endpoints` and connects with a bounded startup deadline. The Envoy does
not accept an arbitrary destination or expose its listener outside the
lease-private network. The frozen Hydra blueprint contract below defines the
exact Reploy representation used by this integration.

Reploy controlled-session v1 starts `/bin/sh` as the workload and requires the
controller to attach to its host-owned PTY. After Reploy reports `ready`,
controller OmegaFlow uses that attachment to execute
`/omegaflow-runtime/bin/envoy`, replacing the bootstrap shell without adding a
Reploy command or transport contract.

That bootstrap shell runs before any OmegaFlow code does, so the blueprint never
sees any exact name or prefixed family in the normative launch-control
enumeration owned by the Reploy environment design. That includes `ENV`,
`BASH_ENV`, `HISTFILE`, `INPUTRC`, `TERM`, `TERMINFO`, `TERMINFO_DIRS`, `LANG`,
`LANGUAGE`, and `LOCPATH`, every `BASH_FUNC_`, `LD_`, `LC_`, and `AWSH_` name,
and interactive Bash controls such as `PROMPT_COMMAND` and `TMOUT`. OmegaFlow's
Envoy-requirement validation rejects the complete image-plus-application launch
environment if it contains any forbidden exact name or prefix. Reserved
composition then adds an empty `HISTFILE`, the fixed `xterm-256color` terminal
type and read-only terminfo path, the trusted empty `INPUTRC`, and fixed
`LC_ALL=C.UTF-8`, `LANG=C.UTF-8`, and trusted `LOCPATH`. Final validation
requires those exact values and fails preparation if the manifest-validated
terminal entry, Readline file, or complete locale tree is unavailable.
Those pre-deployment checks are the only mechanism that can reach this shell:
Reploy launches it with the blueprint environment before any OmegaFlow code
runs, and the later controlled-Bash filtering happens after the Envoy is already
running.
Left in place, an application startup file, Readline binding, prompt command,
timeout, or loader variable could consume or rewrite input, execute application
code, alter shell behaviour, or exit before the controller types the Envoy
command, and the Envoy would never start. It keeps draining the Reploy PTY for
bootstrap and Envoy diagnostics while treating the Envoy terminal channel as
the recording stream.

A blueprint command could express Envoy startup in a future design, but the
initial implementation deliberately uses the lower-level controlled-session shell
bootstrap. Making the Reploy terminal capability optional would be a later
simplification, not a prerequisite for implementation.

## Session Sequence

```mermaid
sequenceDiagram
    participant R as Reploy
    participant C as Controller OmegaFlow
    participant W as Workload /bin/sh then Envoy
    participant A as External Awsh
    participant B as Persistent Bash

    R-->>C: broker-ready with attachment socket
    C->>R: attach and drain bootstrap PTY
    R-->>C: opened with Envoy endpoint coordinates
    R->>W: start workload /bin/sh
    R-->>C: ready
    C->>C: generate handshake session_id
    C->>W: exec /omegaflow-runtime/bin/envoy with session_id and listen arguments over attachment
    W->>W: replace shell and bind both listeners
    C->>W: connect terminal then telemetry
    W->>W: close listeners to new connections
    C->>W: hello with exact session_id
    W->>W: create PTY and private Awsh channels
    W->>A: start with PTY slave and fixed Bash backend
    A->>A: listen on private helper endpoint
    A->>B: start Bash with manifested rcfile
    B-->>A: initial prompt state and readiness
    A-->>W: ready with Awsh and shell identity
    W-->>C: envoy-ready

    C->>W: execute operation 17
    W->>A: arm operation 17
    A-->>W: source ready for submission
    W->>B: bracketed source through PTY master
    B-->>A: start barrier reached
    A-->>W: operation 17 started
    W->>A: started acknowledgement
    B-->>W: visible PTY output
    W-->>C: ordered terminal bytes
    B-->>A: operation returns with Bash state
    A-->>W: operation 17 completed
    W->>W: descendants, EOF, drain, inspections
    W-->>C: operation 17 completed

    C->>W: graceful shutdown
    W->>A: close persistent shell
    A->>B: orderly close then bounded termination
    A-->>W: closed with reaped shell status
    W-->>C: remaining terminal bytes
    W-->>C: terminal EOF and final telemetry
    W-->>R: workload process exits
    R-->>C: authoritative lifecycle and finalization events
```

Controller OmegaFlow does not begin recording actions until both Reploy and the
Envoy are ready. On normal shutdown it waits for the Envoy's final telemetry
and terminal EOF before finalizing the cast. Reploy's
`workload-outputs-finalized` event remains authoritative for Reploy-owned output
surfaces; it does not attest delivery of the application-level TCP streams.

If the Envoy crashes or either TCP channel fails, OmegaFlow fails the capture,
retains partial artifacts and diagnostics, and asks Reploy to terminate the
session. A telemetry success can never replace a failed Reploy lifecycle or
cleanup result.

## Protocol Shape

Both protocols are versioned, bounded, and fail closed on malformed or
out-of-state messages.

The terminal channel is binary-safe and preserves byte order. It carries
controller input in one direction and the observed workload output in the
other: PTY bytes for an attached operation, or Envoy-ordered stdout/stderr pipe
bytes for a split-stream operation. It does not carry JSON, lifecycle state,
stream-origin labels, or presentation markers.

The telemetry channel carries typed messages for at least:

- Envoy readiness and negotiated protocol version;
- execute, continue, cancel, and finalize operation requests;
- operation started, ready, continued, completed, cancelled, finalized, and
  failed events;
- operation status and resulting cwd;
- the selected PTY-attached or split-stream execution shape and compiled
  execution policy, with authored schedules and replacement text staying
  controller-private;
- sender-stamped output marks attributing stream identity and timing to the
  retained raw output;
- bounded workload filesystem inspection plans and typed `file_exists` and
  produced-output results;
- generic action gates used to pause an operation for planned controller work;
- resize requests and applied dimensions;
- bounded diagnostics; and
- graceful shutdown and final-drain confirmation.

Exact schemas, limits, ordering rules, timeout values, the Go build contract,
and golden fixtures are owned by the dependent OmegaFlow Envoy Protocol v1
slice and freeze with A2 approval at exact reviewed bytes. Later slices
implement that contract rather than reopening it.

The Envoy-to-`awsh` protocol is private to the mounted runtime and is not a
third network service. The Envoy translates validated telemetry requests into
bounded operations on its private channels to the external supervisor and
translates Awsh results back into typed telemetry. `awsh` does not parse TCP,
authenticate the controller, own or write the PTY master, interpret Reploy
lifecycle messages, perform Envoy's process-tree cleanup, or publish controller
events.

### Browser ownership, readiness, and action gates

Browser automation belongs entirely to controller OmegaFlow. The compiled
recording plan selects each browser action and application endpoint ID.
Browser destinations and readiness conditions are compiled controller inputs.
The controller resolves the selected endpoint ID only through trusted
`opened.endpoints`; workload output cannot replace its scheme, host, port, path,
or readiness policy.

Ordinary terminal-to-browser sequencing waits for structured operation
completion. For a planned browser handoff whose workload operation remains
running, the controller instead:

1. verifies the plan-selected granted endpoint is unready before `execute`, so
   a listener already serving fails as stale;
2. starts the operation and waits for `operation_started` and its output
   barrier;
3. waits for the exact operation-scoped `operation_ready` gate compiled for the
   handoff, while racing it against operation completion, cancellation, and
   failure;
4. while that operation remains gated, probes the endpoint until the configured
   health condition succeeds or its deadline expires, under the same race;
5. runs the already-planned browser actions while the operation remains gated;
   and
6. sends any authored terminal input, then continues with the cumulative input
   watermark so the Envoy does not release the gate before those bytes arrive,
   or ends or retains the operation according to its compiled lifetime policy
   using the normal typed rules.

Terminal Ctrl-C may independently interrupt a waiting gate helper. In that
case Envoy closes the current output frontier and emits
`operation_gate_interrupted` before it lets Awsh return status 130 to Bash. The
controller leaves the gated state on that event; it does not treat the event as
successful completion of the planned browser handoff or as lifecycle
cancellation. A crossed continue is resolved by the interruption event, while a
crossed cancel or finalize remains live against the resumed operation.

The trusted operation source calls the named gate in the intended service's
launch path only after obtaining that operation's application-specific
readiness evidence. The matching `operation_ready`, not the endpoint's temporal
transition, is the causal evidence that the current operation reached the
handoff. The controller's post-gate endpoint probe establishes current health;
the pre-start failed probe only rejects an already-serving stale listener. A
terminal result observed before the gate, during probing, or at handoff fails
closed. If the application cannot expose the operation-scoped gate in its
authored launch path, this running-operation handoff is unsupported and normal
sequencing waits for structured completion.

This handoff does not consume controller-local files, OSC markers, terminal
text, or workload-originated navigation telemetry. The required `awsh` gate is
generic planned-controller-work machinery: when the operation reaches it,
controller OmegaFlow may perform only the action already associated with that
gate and then follow the compiled lifetime policy. Envoy protocol v1 has no
browser-specific message and does not carry workload-originated navigation
intent. Dynamic workload-selected browser navigation is deferred.

## Runtime Mount and Injection

Envoy, external Awsh, and the selected-backend assets are OmegaFlow runtime
components, not application dependencies and not part of the project's source
tree. Host OmegaFlow stages
the artifacts shipped with its installed release, validates them against a
versioned manifest, and asks Reploy to mount that directory read-only and
executable at `/omegaflow-runtime` in the workload:

```text
/omegaflow-runtime/
├── manifest.json
├── bin/
│   ├── envoy
│   └── awsh
├── etc/
│   ├── inputrc
│   └── awsh-bashrc
├── share/terminfo/
│   └── .../xterm-256color
└── lib/locale/
    └── ...  # complete selected C.UTF-8 locale tree
```

The mount contains every OmegaFlow-supplied workload executable, script, and
launch-time data asset.
The persistent `awsh` process is the selected-shell supervisor. Its initial
Bash bootstrap and short-lived hook-helper modes are supplied by the same
manifested executable; no Python runtime, project script, or Bash-resident
request loop is a production dependency.
Writable ephemeral state belongs under `/run/omegaflow`: every Envoy session
uses a fresh mode-0700 directory for Awsh's helper endpoint, frame-entry marker,
and any split-operation subdirectories. All are removed at their typed boundary.
Project source, the application working copy, caches, and controller capture
outputs use separate declared locations. Blueprint validation rejects an application mount
whose target equals, contains, or is contained by `/omegaflow-runtime`,
`/run/omegaflow`, or another reserved OmegaFlow path.

Runtime integration requires the selected application blueprint to provide
Bash and the configured non-root workload identity. Envoy and Awsh are shipped
executables, so the workload does not need Python or an OmegaFlow installation.
The protocol contract selects dependency-free Go Envoy and Awsh
executables, Linux `amd64` and `arm64`, and reproducible runtime build settings.
The workload may not supply or rebuild either executable.
Distribution and identity materialization remain environment-construction
concerns, not additions to Reploy's controlled-session protocol.

### Runtime artifact manifest

The installed OmegaFlow distribution carries one manifest for each supported
workload platform. Its schema is `omegaflow-runtime-manifest-v1` and it records:

- the OmegaFlow version and source revision;
- the Envoy telemetry and private `awsh` protocol schemas;
- the target operating system and architecture;
- the pinned Go toolchain version used for the Envoy and Awsh binaries; and
- every runtime-relative regular file with its byte size, executable mode, and
  lowercase SHA-256 digest.

Paths are unique, normalized relative POSIX paths and may name only the fixed
`bin`, `etc`, `share/terminfo`, and `lib/locale` roots. The trusted
launch data consists of the empty `etc/inputrc`, the exact
`xterm-256color` entry below `share/terminfo`, and every regular file in the
complete selected `C.UTF-8` tree below `lib/locale`. The manifest itself is not
listed as a payload file. Staging rejects missing or additional payload files,
symlinks, special files, escaping paths, duplicate paths, invalid modes, and any
size or digest mismatch. Host OmegaFlow copies verified installed artifacts
into a fresh private directory, writes the manifest last, makes the staged tree
non-writable, and uses that exact directory as the read-only bind source. A
staged runtime is never assembled from a project checkout.

### Workload-blueprint requirements

The frozen Hydra blueprint contract provides all of the following:

- a Linux workload for the selected supported architecture;
- `/bin/sh` for Reploy's bootstrap and a `/bin/bash` build present in
  OmegaFlow's generated digest-keyed build table for the fixed `awsh` backend,
  with any declared system rc path absent;
- one configured non-root workload identity;
- the verified runtime directory mounted read-only and executable at
  `/omegaflow-runtime`;
- the manifest-validated empty Readline file, exact trusted terminal entry, and
  complete trusted locale tree in that non-shadowable runtime mount;
- a writable ephemeral `/run/omegaflow` root from which Envoy can create each
  private mode-0700 session directory;
- two private TCP endpoint declarations reserved for the Envoy terminal and
  telemetry listeners;
- ordinary application and browser-service endpoints declared independently of
  those Envoy endpoints; and
- no application mount that equals, contains, or is contained by
  `/omegaflow-runtime`, `/run/omegaflow`, or another reserved OmegaFlow path.

Hydra produces the final typed controller and workload blueprint objects.
OmegaFlow's built-in Envoy defaults compose before the selected `reploy/app`
configuration, and application configuration may override normal Reploy fields;
the Envoy-reserved entries compose after it instead and are not overridable.
OmegaFlow serializes the final objects without a post-composition merge or
injection step. The complete composition, materialization, and retained-
evidence contract lives in the Reploy environment design linked above.

### Envoy invocation contract

The production command is `/omegaflow-runtime/bin/envoy`. Its local integration
surface accepts the controller-generated handshake `session_id`, explicit
terminal and telemetry listen coordinates, and initial columns and rows. The
listen hosts and ports must be values frozen into the prepared workload
blueprint; they are not accepted from terminal content or an untrusted runtime
destination. The runtime root, external `awsh` path, selected backend, and
`/bin/bash` executable are fixed and have no workload-controlled override. The
exact flags
are required `--session-id`, `--terminal-listen`, `--telemetry-listen`,
`--columns`, and `--rows`. Controller OmegaFlow always supplies all five;
`session_id` has no default. Local developer invocations may use the built-in
blueprint coordinates and an 80-by-24 terminal only by supplying them
explicitly.

The Envoy exits zero only after structured shutdown, final PTY drain, terminal
EOF, final telemetry, and process-group cleanup succeed. Startup, channel,
protocol, PTY, shell, cancellation, resize, drain, and cleanup failures exit
nonzero and retain a stable failure class. When possible, the Envoy emits a
bounded diagnostic before closing and writes an outer diagnostic to the
Reploy-owned bootstrap PTY; neither delivery path converts failure to success.

## Controller Run Input

Per-run recording input is not placed in Reploy's controller output directory,
which must start empty. Host OmegaFlow prepares a versioned regular JSON file
and mounts it read-only and only into the trusted controller deployment at:

```text
/omegaflow-input/run-manifest.json
```

The schema is `omegaflow-controller-run-v1`. It contains the recording identity,
the compiled recording plan, initial terminal dimensions, the two reserved
Envoy endpoint IDs, selected application endpoint IDs, artifact policy, and the
relative names of any separately declared controller input assets. It contains
no secrets, host output path, Reploy session socket, endpoint coordinates, or
Docker information. Referenced controller assets are mounted read-only below
`/omegaflow-input/assets`, carry expected size and SHA-256 evidence in the
manifest, and cannot escape that directory.

The complete manifest is bounded to 8 MiB. Controller OmegaFlow validates the
schema, paths, bounds, hashes, and recording-plan model before starting the
Reploy session client. `/omegaflow-input` and `REPLOY_OUTPUT_DIR` are distinct,
non-overlapping mounts. The input mount is a public prepared-blueprint grant,
not a Reploy-private channel or a workload mount.

## Delivery Plan

The former eight-slice plan combined independent protocol, process supervision,
runtime, controller, terminal, browser, artifact, and migration work into
review units that were too large. The dependent [Reploy integration
implementation plan](reploy-integration-implementation-plan.md) supersedes it.

Delivery now proceeds through five gated phases:

1. amend and re-review the protocol and plan;
2. complete local Envoy/external-Awsh/Bash-adapter conformance in bounded slices;
3. integrate the controller, Reploy boundary, runtime, blueprint, and terminal
   runner without browser scope;
4. prove a terminal-only isolated Reploy recording; and
5. plan browser, publication, host-workload parity, and FIFO retirement as
   later, separately approved stacks.

The cross-slice acceptance requirements below remain product requirements. The
temporary plan owns their implementation order and evidence status.

## Cross-slice Acceptance Validation

The terminal-only isolated Reploy milestone must prove:

1. the complete Bash-state matrix across PTY and split operations, including
   cwd, bounded complete exported environment, variables, arrays, functions,
   aliases, positional parameters, traps, `$?`, explicitly unpromised transient
   parameters, jobs, every supported `set -o`/`shopt` entry, history, and
   Readline state, with the exact simple-alias allowlist preserved,
   grammar-bearing aliases rejected at launch or the reached prompt boundary,
   other parser-state-dependent source rejected before start, and
   the closed submission capsule preserving exact bytes across terminal modes,
   Readline bindings, history state, actual Readline-entry synchronization, and
   default `SIGCHLD`, with reserved-state corruption failing before another
   submission and independent marker/termios backstops,
   with the exact paste terminator rejected before operation start;
2. continuous byte-for-byte terminal transport and private raw-output
   retention while a command runs, with synthesized prompt and command events
   ordered separately in the presentation timeline;
3. `real`, `suppress`, and `replace` presentation over retained private output,
   including incremental realtime publication, buffered stdout-then-stderr
   presentation publication after the logical post-enter pause, compressed
   command wall time, and output-through ordering for replacement events;
4. compile-time rejection of output assertions on an interactive operation
   chain that sends authored terminal bytes, plus fail-closed cleanup after
   every operation, including ordinary background jobs, `disown`, `nohup`,
   `setsid`, and rapid double-fork daemonization, while a setup-launched service
   outside the controlled process tree remains unaffected;
5. typed workload-side `file_exists` and produced-output path, kind, and digest
   results with no controller filesystem access or PTY-parsed probes, including
   typed failure with no accepted digest when a setup service concurrently
   mutates a selected file or directory;
6. newline-sensitive `stdout + stderr` assertions for split-stream operations,
   plus PTY-attached assertions without authored input over the exact
   post-line-discipline CRLF and other terminal bytes treated as logical stdout;
7. controlling-terminal/session/foreground-group setup, interactive input,
   atomic Awsh-classified cancellation interruption, gate Ctrl-C ordered through
   `operation_gate_interrupted`, and Linux `TIOCSWINSZ`/`SIGWINCH` behavior
   without duplicate signaling;
8. curses applications and one nested interactive shell, with hostile
   application `TERMINFO`, `TERMINFO_DIRS`, `$HOME/.terminfo`, `INPUTRC`,
   `$HOME/.inputrc`, `LANG`, `LANGUAGE`, `LOCPATH`, and `LC_*` inputs unable to
   affect either shell launch, and with a missing, non-regular, unreadable, or
   mismatched trusted terminal, Readline, or locale asset failing before
   controlled Bash starts;
9. separation of terminal output from telemetry messages;
10. documentation that same-identity workload processes can deliberately
   interfere and that telemetry is not security evidence, plus a mechanical
   conformance test proving the one-exec close-on-exec handoff, that ordinary
   exec'd descendants inherit neither Envoy TCP sockets, private Envoy-to-Awsh
   descriptors, nor helper sockets, that split children receive only selected
   FIFO-backed fd 1/fd 2, that Envoy directly supervises Awsh while Awsh directly
   parents and reaps Bash, and that real shell wait status survives `exit`,
   `exec`, `errexit`, and signals;
11. exact pre-submission output retention, source/redraw discard, operation-start
   timeout, ordered terminal drain, explicit shell-ended close handshake, and EOF
   during graceful shutdown;
12. useful partial diagnostics after Envoy, Awsh, shell, channel, and controller
   failures; and
13. planned recording-end finalization that drains an intentionally open
    operation and validates its non-exit assertions, while failure/user
    cancellation invalidates assertions, including cancellation and
    finalization timeouts that report `shell_ended` and drain the session; and
14. end-to-end Reploy termination, acknowledgement, retained output, and
    cleanup.

Before retiring the native FIFO runner, a separately approved host-Envoy stack
must repeat every applicable terminal and inspection check above through the
host-workload connectivity boundary and prove recording-plan, artifact,
diagnostic, cancellation, and failure parity with the isolated workload path.

The implementation must also record the deferred Reploy capability needed for a
future privileged Envoy/non-root Awsh-and-workload split. These checks and the
frozen Hydra blueprint conformance do not by themselves authorize replacement of the
local FIFO-backed `PersistentTerminalRunner`. A host-Envoy prototype must first
prove applicable parity, and replacement remains a separately approved change.

## Decisions and Deferrals

1. The Envoy owns the recording PTY and the two application-level TCP channels;
   Reploy continues to own admission, isolation, lifecycle truth, retained
   controller outputs, cancellation, and cleanup.
2. `awsh` is an external selected-shell supervisor and direct shell parent. It
   is not the network, PTY-master, process-tree-policy, or Reploy lifecycle
   owner; Bash-specific hooks remain an internal backend detail.
3. The first implementation supports Bash only. Other top-level shells,
   dynamic adapter discovery, and a generalized backend framework are deferred,
   but a future backend must not require a controller-facing protocol change.
4. Both workload backends use the same version-matched, manifest-validated
   runtime. Reploy workloads receive it through the read-only executable
   `/omegaflow-runtime` mount; host workloads stage it directly on the host.
5. The initial Envoy, Awsh, and Bash use the same non-root workload identity. A
   privileged Envoy/non-root Awsh-and-shell split waits for a Reploy identity
   contract.
6. Terminal bytes never carry telemetry, and shell-originated telemetry never
   overrides Reploy lifecycle or security decisions.
7. The exact protocol schemas, limits, timeout values, output-ordering barrier,
   direct-cast rules, controlled Bash launch environment, and Envoy
   implementation language are explicit protocol contracts rather than details
   left implicit in the terminal adapter.
8. Controller OmegaFlow writes Envoy captures directly in asciicast format; the
   Envoy path merges provenance-marked controller presentation events with
   terminal output, retains the exact private raw workload output separately,
   and does not add an `asciinema record` process or controller-side PTY.
9. Browser actions, navigation intent, checks, and endpoint selection are
   controller-owned. Envoy action gates provide only generic planned
   synchronization; protocol v1 carries no browser-specific messages.
10. Per-run controller input is a bounded, versioned, read-only prepared
    deployment mount at `/omegaflow-input`; Reploy's initially empty controller
    output directory is never repurposed as input.
11. Hydra produces complete typed `reploy.controller` and `reploy.workload`
    blueprint objects. The controller is OmegaFlow-owned and read-only after
    composition; Envoy defaults compose before the user-owned `reploy/app`
    workload config. No post-composition injection or repair step exists.
12. OmegaFlow packages only its own runtime. Reploy remains a separately
    released Python dependency.
13. The recording toolchain always runs in Reploy. Workloads default to the
    host and use Reploy only when explicitly selected with a complete blueprint.
    A bare-metal recording controller is deferred until justified by a concrete
    requirement.
