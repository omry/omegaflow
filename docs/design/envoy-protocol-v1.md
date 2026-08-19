# OmegaFlow Envoy Protocol v1

## Status and scope

This document defines the first controller/workload contract for the
[OmegaFlow Workload Envoy](../future/omegaflow-envoy-design.md). The current
pre-release inspection amendment becomes frozen when the rebuilt design slice
is approved. It is an internal OmegaFlow release contract. Reploy provides the
private network, endpoint coordinates, bootstrap attachment, and authoritative
lifecycle; it does not transport or interpret these messages.

Version 1 covers:

- a full-duplex binary terminal channel;
- a bounded JSON Lines telemetry channel;
- the private NUL-framed Envoy-to-`awsh` descriptor protocol;
- bounded workload-side `file_exists` and `produces` inspection;
- state, ordering, resize, cancellation, shutdown, and failure rules;
- sender-stamped output marks carrying stream identity and timing;
- direct asciicast synthesis and exact raw-output retention; and
- the controlled Bash launch boundary.

It does not implement the Envoy process, PTY supervision, TCP listeners, runtime
mounting, or Reploy lifecycle integration. Delivery order is tracked in the
temporary [Reploy integration implementation
plan](../future/reploy-integration-implementation-plan.md).

## Implementation and build contract

The workload Envoy is a dependency-free Go executable:

- module: `github.com/omry/omegaflow/runtime/envoy`;
- minimum toolchain: Go 1.25.x, matching Reploy;
- supported targets: `linux/amd64` and `linux/arm64`;
- `CGO_ENABLED=0`;
- no third-party module dependencies;
- `-trimpath -buildvcs=false`; and
- linker flags `-s -w -buildid=`.

The eventual production command is built from `./cmd/omegaflow-envoy`. The
protocol-model slice does not add a placeholder command. Once the command
exists, the release build is equivalent to:

```text
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build \
  -trimpath -buildvcs=false -ldflags='-s -w -buildid=' \
  -o omegaflow-envoy ./cmd/omegaflow-envoy
```

Release materialization records the source revision, Go version, target, file
size, and SHA-256 digest. Rebuilding the same source with the pinned Go patch
release and target must produce the same digest before the binary is added to
the runtime manifest.

## Connection establishment

The workload blueprint declares two lease-private TCP endpoints: terminal and
telemetry. The Envoy binds both listeners before starting Bash. It accepts one
connection on each listener and then closes both listeners, so a later attempt
is refused by the kernel without the Envoy observing it and has no effect on the
capture.

The controller connects the terminal channel first and telemetry second. Its
first telemetry request is `hello`. The Envoy creates the PTY and persistent
Bash only after both connections and a valid `hello` exist. Its first event is
`ready`. Neither side sends another message before this exchange completes.

The channels have no application reconnect. EOF, reset, timeout, a second
connection completed while a listener is still open, or traffic before the
required handshake fails the capture.

## Global limits and timeouts

| Contract | Value |
| --- | ---: |
| Telemetry frame, including LF | 1,048,576 bytes |
| Private `awsh` frame | 1,048,576 bytes |
| Bash operation source | 1–786,432 UTF-8 bytes |
| Output-mark cadence | 10 milliseconds |
| Output marks per session | 1,000,000 |
| Session elapsed microseconds | 0 through `2^63-1` |
| Identifier | 1–64 ASCII identifier characters |
| Diagnostic message | 1–4,096 UTF-8 bytes |
| Reason | 1–256 UTF-8 bytes |
| Cwd | 1–4,096 UTF-8 bytes, absolute Linux path |
| Inspection specifications per operation | 0–64 |
| Inspection configured or resolved path | 1–4,096 UTF-8 bytes |
| Produced-output identifier | 1–64 ASCII identifier characters |
| Directory entries inspected per operation | 100,000 |
| Regular-file bytes hashed per operation | 16 GiB |
| Symlink target | 0–4,096 bytes |
| Sequence | 1 through `2^63-1` |
| Output offset | 0 through `2^63-1` |
| Terminal input watermark | 0 through `2^63-1`, non-decreasing |
| Terminal input barrier wait | 5 seconds |
| Raw output per session | 8 GiB |
| Retained supervised writers per session | 256 |
| Concurrently tracked descendants per operation | 4,096 |
| PID | 1 through `2^31-1` |
| Shell status | 0 through 255 |
| Terminal columns and rows | 1 through 1,000 |
| Connect deadline | 10 seconds |
| `hello`/`ready` deadline | 10 seconds |
| Individual control write | 5 seconds |
| Cancellation grace period | 5 seconds |
| Final drain | 5 seconds |

Operation duration is owned by the recording plan and is not a fixed Envoy
timeout. The controller converts an operation deadline into a typed `cancel`.

Every field limit is additionally constrained by the enclosing telemetry or
private-frame limit. Reaching an individual field maximum does not guarantee
that the field can be combined with every other maximum-sized field. Encoders
must validate the complete encoded frame before writing it, and receivers
reject an otherwise field-valid frame that exceeds its enclosing limit.

Identifiers match `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`. Diagnostic codes match
`[a-z][a-z0-9-]{0,63}`. Strings reject NUL. Cwd values are lexical evidence
from Bash; the controller does not resolve them on its own filesystem.

## Terminal channel

The terminal connection is binary and full duplex:

- controller to Envoy: exact input bytes;
- Envoy to controller: exact post-line-discipline PTY-master bytes for an
  attached operation, or Envoy-ordered stdout/stderr pipe bytes for a
  split-stream operation.

It has no record framing, JSON, lifecycle messages, presentation markers, or
shell-status markers. `^C` is byte `0x03`. The terminal line discipline and
foreground process group give it normal terminal behavior. Resize travels on
telemetry because it is a structured PTY-master operation.

Because the channel carries no framing, it carries no operation identity either,
so the binding between input and operation is a contract rule rather than a
field. Every terminal byte the controller writes is authored against one
operation: there is no person at a keyboard, so there is no type-ahead the
recording must preserve. The Envoy therefore discards terminal input that
arrives while the session is idle, and when an operation ends it drains input
the line discipline has queued but the workload has not consumed. Input a gated
operation is still entitled to receive is unaffected, since such an operation
has not ended, and so is anything the workload already read.

Those two rules govern bytes the Envoy already holds, which is not enough on its
own: terminal and telemetry are separate connections and nothing orders one
against the other, so input written for an operation can still be in flight when
that operation's result is sent, and would arrive after the next one had already
started. `execute` therefore carries `input_through`, the running count of
terminal bytes the controller has written since the session began, and the Envoy
does not start the operation until its own terminal read count reaches that
value. Whatever arrives while it waits belongs to an operation that has ended
and is discarded under the rule above. The count is a barrier rather than
framing, so the channel stays exact bytes with no record structure, and it needs
no acknowledgement round trip because the controller is the only writer and the
Envoy the only reader.

The controller appends every received output byte to a private raw log before
using it anywhere else. A zero-based monotonically increasing offset is the
number of bytes durably appended. The raw log, not the asciicast, is canonical
for output ranges and diagnostics.

Lossless retention needs a volume bound, because the operation timeouts bound
duration rather than output and mark coalescing keeps the mark budget almost
unchanged under a flood. A command such as `yes` would otherwise fill the
controller's disk and take the recording host with it. The raw log is therefore
capped per session, and reaching the cap is a typed session failure: the
controller stops appending, emits a bounded diagnostic, and fails the capture
rather than truncating a log whose whole contract is that it is exact.

## Telemetry framing

Each telemetry message is one UTF-8 JSON object followed by one LF. Outgoing
frames use compact JSON and field order shown by the golden fixtures. Receivers
reject:

- missing LF, CRLF, embedded LF, NUL, or invalid UTF-8;
- invalid JSON, a non-object top level, duplicate fields, or non-finite numbers;
- unknown or missing fields;
- an unsupported `schema` or `type`;
- values outside the declared type or bounds; and
- frames over the byte limit, including an unterminated buffered frame.

Every message has `schema: "omegaflow-envoy-telemetry-v1"`, `type`, and a
positive `seq`. Controller and Envoy sequence spaces are independent, begin at
1, and increase by exactly one. A gap, duplicate, or regression is fatal.
Version 1 has no alternative-version offer: the `hello` schema selects v1 and
the `ready` schema confirms it. Any other schema fails the handshake instead of
being downgraded.

### Controller requests

| Type | Additional required fields |
| --- | --- |
| `hello` | `session_id` |
| `execute` | `operation_id`, `source`, `execution_shape`, `timing`, `publication`, `observation`, `inspections`, `input_through` |
| `continue` | `operation_id`, `gate_id` |
| `cancel` | `operation_id`, `reason` |
| `finalize` | `operation_id`, `reason` |
| `resize` | `columns`, `rows` |
| `shutdown` | `reason` |

Operation source is trusted recording-plan Bash source. It is delivered on the
private control path and is not typed into the PTY.

The compiled execution policy uses these closed enums:

- `execution_shape`: `pty` or `split`;
- `timing`: `realtime` or `presentation`;
- `publication`: `real`, `suppress`, or `replace`; and
- `observation`: `shared` or `exclusive`.

`input_through` is not part of that policy: it is the terminal-input barrier
defined under Terminal channel, and the Envoy holds the operation in its
starting state until its terminal read count reaches it. It is a terminal input
watermark under the global limits, so it is a non-negative 64-bit count that
never decreases across a session. The Envoy rejects a value outside that type or
below the previous watermark, which is the whole of what it can check: it cannot
know what the controller has written, only what it has read. A watermark that is
merely never reached is therefore not a validation failure but a wait, and the
terminal input barrier wait bounds it.

Realtime timing requires PTY execution and real publication. Presentation
timing requires split execution and exclusive observation. Suppressed and
replaced output require exclusive observation.

Presentation timing needs the exclusive requirement because a surviving writer
and a compressed schedule cannot both be honoured. Late bytes from an earlier
unchecked `real` operation publish at their own mark times, an authored schedule
starts at the last committed absolute time, and the writer never rewinds, so a
background byte arriving twenty seconds in would force the buffered presentation
output to start no earlier than twenty seconds and expose exactly the duration
presentation timing exists to discard. Retiming those bytes onto the authored
schedule would falsify output that belongs to no operation, so the design
forbids the combination instead: exclusive observation already requires the
supervised writer set to be empty before the operation starts. Replacement text
and authored presentation delays stay controller-private and are not sent to the
Envoy.

`inspections` is an array, including when empty. Each entry is an exact object
with a unique `inspection_id`, a `kind`, and a configured `path`. `kind` is
`file_exists` or `produces`. A `file_exists` entry has no other fields. A
`produces` entry additionally requires `producer_id` and `output_id`; both are
identifiers. Paths are trusted recording-plan values, not shell output, but
remain bounded and reject NUL. An operation with inspections requires
`exclusive` observation so resolution and hashing run only after every tracked
descendant owned by that operation has been closed and reaped.

Controller OmegaFlow assigns inspection IDs deterministically in request order
as `inspection-1`, `inspection-2`, and so on. It retains that compilation map
with the operation. Results remain in request order and repeat the ID;
`produces` results additionally retain the authored producer and output IDs.

### Envoy events

| Type | Additional required fields |
| --- | --- |
| `ready` | `envoy_pid`, `shell_pid`, `cwd`, `columns`, `rows`, `elapsed_us` |
| `operation_started` | `operation_id`, `output_start` |
| `operation_ready` | `operation_id`, `gate_id`, `output_through` |
| `operation_continued` | `operation_id`, `gate_id`, `output_through` |
| `output_mark` | `offset`, `stream`, `elapsed_us` |
| `operation_completed` | `operation_id`, `status`, `cwd`, `output_start`, `output_through`, `inspection_results`, and `shell_ended`, boolean `true`, present only when the operation's shell did not survive it |
| `operation_cancelled` | `operation_id`, `cwd`, `output_start`, `output_through`, `reason`, and `status` unless the operation was cancelled before it started; no inspection results |
| `operation_finalized` | `operation_id`, `cwd`, `output_start`, `output_through`, `reason`, `inspection_results`; no status |
| `operation_failed` | `operation_id`, `output_start`, `output_through`, `code`, `message`, `cwd`, and `shell_ended`, boolean `true`, present only when the operation's shell did not survive it |
| `resize_applied` | `columns`, `rows`, `elapsed_us` |
| `diagnostic` | `severity`, `code`, `message`; optional `operation_id` |
| `draining` | `reason`, `output_through` |
| `closed` | `reason`, `output_through` |

Diagnostic severity is `info`, `warning`, `error`, or `fatal`. Codes are open
for forward-compatible diagnostics; code shape and message size remain bounded.
An unknown diagnostic code is retained, not reclassified as a protocol error.

`output_mark` attributes raw output to a logical stream and to sender time. It
is session-scoped rather than operation-scoped, because output can arrive
between operations. `offset` is the raw-log offset at which the attribution
begins, `stream` is `pty`, `stdout`, `stderr`, or `echo`, and `elapsed_us` is
the Envoy's monotonic microseconds since the session epoch established by
`ready`; `ready` itself carries `elapsed_us` 0, the instant it is stamped. A mark attributes every byte from its `offset` until the next mark's
`offset`.

`echo` covers the raw-log span produced by the Envoy's own write of authored
controller input. The workload owns its terminal modes, so an echo-disabled
termios reading taken before the write is a check-then-write race rather than
provenance for the bytes that follow. The Envoy therefore marks `echo`
immediately before each authored write and marks `pty` again only once two
things hold: the line discipline has processed the input, which is what places
any echo on the master's read side, and the Envoy has since drained the master
to empty. The second conjunct is what makes the span an output-side boundary. A
mark attributes raw-log offsets, so closing on input consumption alone would put
both marks at the same offset whenever the drain had not yet run, and the echo
arriving afterwards would be attributed `pty`. Draining is an observable the
Envoy owns because it is the master's only reader, and closing at the offset
that drain reached puts kernel echo, including newline echo, inside the span by
construction rather than by timing, whatever the terminal mode was. A span that
closes at the offset it opened at is legal and attributes nothing, which is the
right answer when no echo occurred; it is only trustworthy because the close
required a drain, since without that requirement the same two same-offset marks
could equally have meant that the echo had not been drained yet. An `echo` span
belongs to no logical stream, so it is never assertion evidence, while its bytes
remain in the raw log. It is an attribution over that log rather than a separate
published stream: the span publishes with its operation, under that operation's
policy and presentation schedule and never on a clock of its own, so it neither
exposes command wall time nor flushes decoder state at its boundaries. An
application in a non-canonical mode may begin answering before the span closes,
so those bytes are excluded with the echo; the exclusion can therefore fail an
assertion but never satisfy one. An Envoy that cannot close the span fails the
operation.

The Envoy emits a mark when the stream identity changes, when at least the mark
cadence has elapsed and new bytes exist, and immediately before any event
carrying `output_start` or `output_through`. Marking both range-opening and
range-closing events is what supplies the timeline anchors the asciicast writer
re-anchors on, without a separate timestamp field. It coalesces otherwise. The
mark budget is session-wide rather than per-operation, because a mark carries no
`operation_id` and output surviving from an earlier operation can arrive while
the session is idle or while a later operation runs; neither endpoint could
charge such a mark to an operation. Exhausting the session budget is a session
failure, not a partial success. Marks never regress in `offset` or `elapsed_us`,
and a mark's `offset` never exceeds the bytes already written to the terminal
socket, so a mark is never visible before the bytes it describes. A split-stream
operation therefore carries `stdout` and `stderr` marks over its interleaved
terminal range; a PTY operation carries `pty` marks and, around authored input,
`echo` marks, its `pty`-marked bytes are logical stdout, and logical stderr is
empty.

Logical stdout and logical stderr are slices of the controller's raw log
selected by stream attribution. The Envoy sends no copy of workload output on
telemetry, so assertion evidence is the complete retained output rather than a
bounded excerpt.

A split-stream operation's stdout and stderr pipes are Envoy-owned, and an
unchecked `real` operation may leave a supervised background writer holding
them. The Envoy therefore keeps those pipe readers open past the operation's
completion rather than closing them at the typed boundary, which would either
block on the surviving writer or deliver `SIGPIPE` to a process a later step
expects to use, matching the shell behaviour a recording reproduces. Each
retained writer holds descriptors and supervision state, and a job that never
writes never touches the raw-log or mark budgets, so the retained set has its
own session limit: an operation whose completion would retain a writer beyond it
fails instead of retaining, before the Envoy's descriptor or process budget
breaks a later operation or the final cleanup.

Tracking itself is bounded the same way. The Envoy retains a supervision
identity for every live operation-created descendant, so the concurrently
tracked set has its own budget from the global limits: an operation that
would exceed it fails with `tracked-descendant-limit`, its tree terminated
and reaped exactly as at an exclusive cleanup, before the Envoy's descriptor
table becomes the failure.

Only an unchecked `real` operation can leave such a writer: `suppress`,
`replace`, and checked `real` operations fail closed when a writer survives
their cleanup, so a session that continues past them has none, and exclusive
observation refuses to start while one is outstanding. Late bytes therefore
never carry suppressed or replaced content and need no operation attribution.
They are ordinary real terminal output: they belong to no operation's logical
stdout or stderr even when they fall inside a later unchecked `real` operation's
contiguous range — unrelated provenance inside a shared range, not part of that
operation's output — publish at their own mark times, and are drained at a later
barrier or the final drain.

After natural completion or planned finalization, output assertions consume
logical stdout followed by logical stderr. Each stream is decoded on its own —
UTF-8 with replacement, and the decoder flushed at the end of that stream — and
the decoded texts are then concatenated. Decoding after concatenation would let
a truncated sequence at the end of stdout join a continuation byte at the start
of stderr into a character neither stream contains: stdout ending `0xC3` and
stderr beginning `0xA9` must read as two replacement characters, as the native
runner produces them, not as `é`. This is the assertion decoder, separate from
the asciicast decoder specified later, which serves a different stream. Output
assertions never consume temporal terminal order or infer stream identity from
PTY bytes. Cancellation and failure discard partial assertion evidence instead
of evaluating it.

### Workload inspection

`inspection_results` is an array in request order, including when empty. Each
result repeats `inspection_id` and `kind`, and contains an absolute
`resolved_path` and `path_kind`. `path_kind` is `file`, `directory`, or `other`.
A `file_exists` result has no digest or producer fields. A `produces` result
allows only `file` or `directory`, repeats `producer_id` and `output_id`, and
contains `sha256`, a 64-character lowercase SHA-256 digest. A `directory` result
also carries `digest_algorithm`, the tag that domain-separated the hash input —
`directory-v2` for anything this protocol produces, `directory` for a record the
native runner made. The tag is not recoverable from the digest, so without it a
retained record could not say which algorithm produced it, and the promise that
a recording stays identifiable under its own tag would not survive migration.
Downstream retained records carry it with the digest.

After Bash returns or a planned finalization closes the operation, `awsh`
resolves configured paths using the persistent Bash process's resulting cwd and
exported environment. It sends only the resolved plan to the Envoy over the
private descriptor. The controller never resolves a workload path, starts a
probe operation, or infers filesystem state from terminal output.

Resolution preserves the current native runner's successful path behavior. It
expands `$NAME` and `${NAME}` from the exported environment when the name is
defined and leaves an undefined or malformed reference literal. A leading `~`
uses `HOME` when set and otherwise the workload's user database for the current
effective identity; a leading `~user` uses that database when the named user
exists. An unresolved home expression remains literal and will ordinarily fail
the subsequent existence check. Resolution performs no command substitution,
arithmetic expansion, word splitting, or globbing. The expanded relative path
is anchored at the resulting cwd. After existence is established, the Envoy
reports the absolute canonical path.

Inspection is the final part of an exclusive operation boundary. The Envoy
first receives the Awsh result and resolved plan, terminates and reaps every
tracked descendant created by the operation, proves the descendant and
supervised-writer sets empty, and drains output through the operation's closing
offset. Only then may it inspect or hash workload paths. Cleanup or drain
failure emits no inspection results. The Envoy emits
`operation_completed` or `operation_finalized` only after inspection succeeds;
its `output_through` is therefore already stable when results become visible.
This closes races from cooperative operation-created background processes; it
does not make hashes tamper-proof against another process already running under
the same workload identity.

The Envoy rejects a missing `file_exists` path. A `produces` path must exist and
resolve to a regular file or directory. A regular-file digest covers its exact
bytes. A directory digest traverses entries in sorted relative POSIX-path order
and hashes fixed-size framing rather than delimited text, because delimiters are
not injective over arbitrary file bytes: one file whose contents happen to
contain the delimiter and a following entry's framing would otherwise hash
identically to two files, giving distinct trees the same digest without any
SHA-256 collision. Each entry contributes a one-byte kind tag — ASCII `f` for a
regular file, `d` for a directory, `l` for a symlink, and no other value — the
SHA-256 of its path, and the SHA-256 of its payload — the target for a symlink,
the exact bytes for a regular file, and the empty string for a directory — so
every entry occupies the same 65 bytes whatever it contains. The digest is the
lowercase SHA-256 over the literal `directory-v2` tag followed by those entries
in order. The tag is versioned because this is a deliberate break: the native
runner's encoding, tagged `directory`, is the delimited form whose ambiguity
this replaces, so the two disagree on every directory including the empty one,
and a digest is an identity that must not change silently with the runner that
produced it. Paths and symlink targets must be UTF-8. As in the native runner, a
special entry nested inside a produced directory is omitted from the digest; a
symlink is always recorded as a link and is not followed. A top-level produced
path of a special type still fails with `inspection-type`. The amended canonical
fixtures freeze representative encodings, including nested special entries. File
contents never travel over telemetry.

Resolution, unsupported file type, traversal, read, or hashing failure emits
`operation_failed` with `inspection-resolution`, `inspection-missing`,
`inspection-type`, `inspection-limit`, or `inspection-read`. Cancellation and
ordinary failure produce no inspection results. The Envoy enforces bounded entry
and byte budgets from the global limits; exceeding one is an inspection failure
rather than a partial success. The operation deadline stays controller-owned and
reaches a long-running inspection, when it expires, as a typed `cancel`.

Absolute resolved paths and digests are private run evidence. They are not
presentation or publication data and must not enter a published bundle unless a
separately specified publication contract explicitly selects and sanitizes
them.

## Session state machine

Only one top-level operation is active. Controller and Envoy messages jointly
advance this state machine:

```mermaid
stateDiagram-v2
    [*] --> HelloSent: controller hello
    HelloSent --> Idle: Envoy ready
    Idle --> Starting: controller execute
    Starting --> Running: Envoy operation_started
    Starting --> Idle: Envoy operation_failed
    Running --> Gated: Envoy operation_ready
    Gated --> Continuing: controller continue
    Continuing --> Running: Envoy operation_continued
    Starting --> Cancelling: controller cancel
    Running --> Cancelling: controller cancel
    Gated --> Cancelling: controller cancel
    Continuing --> Cancelling: controller cancel
    Running --> Finalizing: controller finalize
    Gated --> Finalizing: controller finalize
    Continuing --> Finalizing: controller finalize
    Running --> Idle: completed or failed
    Gated --> Idle: Envoy completed or failed after shell end
    Continuing --> Idle: Envoy completed or failed after shell end
    Idle --> Draining: Envoy draining after the shell ends
    Starting --> Draining: Envoy draining supersedes a crossed execute
    Gated --> Idle: failed
    Continuing --> Idle: failed
    Cancelling --> Idle: cancelled or failed
    Finalizing --> Idle: finalized or failed
    Finalizing --> Idle: completed when the observed result wins
    Idle --> ShutdownSent: controller shutdown
    ShutdownSent --> Draining: Envoy draining
    Draining --> Closed: Envoy closed
    Closed --> [*]
```

`resize` is allowed in idle, starting, running, gated, or continuing states; continuing is included because it is running-equivalent for the PTY, so a controller may pipeline `continue` and `resize` without waiting for `operation_continued`. Only one resize
may be outstanding, and it must be matched by `resize_applied` with the same
dimensions before another resize or shutdown. A bounded diagnostic is allowed
after `hello` and before `closed`. Every operation and gate event must match the
active identifiers. The shell-end transitions are entered by the Envoy rather
than by a controller message. An operation whose control descriptor reached EOF
without a `closed` reaches `Idle` through `operation_completed` carrying the
status the Envoy reaped — unless it declared inspections or still holds an
unresolved gate, which the closed descriptor can no longer resolve, in which
case it reaches `Idle` through `operation_failed` instead, because an authored
requirement that cannot be evaluated must not be reported as met; that
`operation_failed` carries `shell_ended` exactly as the completion does, so the
controller still learns the shell is gone. `Idle --> Draining` follows either.
That drain is Envoy-initiated, so no request supplies its reason: `draining` and
`closed` both carry reason `shell_ended` on this path, exactly as they carry the
controller's shutdown reason on the requested one, which is what lets the golden
fixtures freeze one shell-exit sequence. An EOF that arrives while the session
is idle — a workload killed between operations — takes the same drain path with
the same reasons; there is no operation to report. Because these transitions are
Envoy-initiated, a controller request already in flight when the shell ends —
the next `execute`, a `resize` — can arrive after them; it is accepted and
discarded exactly like a request that crossed its own terminal result, and a
recording with a beat left to run still fails from that beat being unrunnable
rather than from the crossing.

`Starting` can last as long as the terminal-input barrier makes it last, so it
accepts `cancel` like the running states do. A cancel there needs no signal,
because the operation has not begun: the Envoy abandons the wait, discards the
input it was waiting through as belonging to an ended operation, and reports
`operation_cancelled` with an empty range and no `status`. No shell ran, so
there is no status to report and none is invented; this is the only case in
which that field is absent, and it parallels `operation_finalized`, which has no
status for the same reason. The wait is bounded anyway: the barrier only waits
for bytes the controller has already written, so exceeding the terminal input
barrier wait means they are never arriving, and the Envoy fails the operation
rather than holding the session.

Two crossing families are accepted in states that have no transition for them,
because TCP ordering is directional and the controller can act on a state the
Envoy has already left. A `cancel` or `finalize` naming an operation whose
terminal result the Envoy has already sent is accepted and discarded while it is
still the most recent operation, and the next `execute` supersedes it; the
controller resolves its own request when the terminal result arrives and does
not additionally wait for `operation_cancelled` or `operation_finalized`. And
any request already in flight when the shell ends — the next `execute`, a
`resize` — is accepted and discarded after the Envoy-initiated drain begins, as
the shell-end rules above describe; the `Starting --> Draining` edge exists
because a controller that sent that `execute` has already left `Idle` when the
`draining` it crossed arrives. Any other transition fails closed.

## Output ordering barrier

`output_start` snapshots the raw-log offset at `operation_started`, and the
snapshot happens before Bash is released rather than after. The driver's
`started` result is one-way, so if the Envoy snapshotted on receiving it, a fast
command could already have written and the pump already appended before the
snapshot, putting the operation's first bytes outside its own range — missed by
assertions, and for `suppress` or `replace` published as session-scoped output
the policy was supposed to withhold. The driver therefore writes `started` and
waits for the Envoy to acknowledge it on the request descriptor; only then does
it evaluate the source. The Envoy takes the offset before sending that
acknowledgement, so no byte of the operation can precede its own start. An
operation that fails before `operation_started` has no such snapshot, so its
`operation_failed` sets both `output_start` and `output_through` to the offset
observed at the failure, not the one observed when the `execute` was accepted.
Either gives an empty range satisfying `output_start <= output_through` without
claiming output the operation never produced, but only the later offset respects
the non-regressing rule: a permitted background writer from an earlier unchecked
`real` operation can advance the raw offset and emit marks while the new
operation waits in `Starting`, so an offset taken at acceptance would sit behind
the mark emitted immediately before this very event, and a strict controller
would turn an ordinary operation failure into a protocol failure. A pre-start
failure is the only case in which an operation reports a range it did not open.
`output_through` is an exclusive raw-output offset. Before emitting an event
that contains `output_through`, the Envoy:

1. observes the corresponding `awsh` result;
2. drains all PTY bytes, or split stdout/stderr pipe bytes, whose writes
   happened before that result write;
3. writes those bytes to the terminal socket in order; and
4. snapshots the resulting output offset for the telemetry event.

An operation whose shell ended has no `awsh` result to observe, so the reap
replaces it: the Envoy waits for the child, then drains every byte whose write
happened before that exit, writes them in order, and snapshots the offset. The
reap is a real happens-before boundary — the kernel does not report the child
until it has gone — so output written immediately before `exit 7` is still
inside the range. Every other step is unchanged.

A pre-start terminal event — the `operation_failed` or `operation_cancelled` of
an operation that never started — has no result to observe either, and none will
ever exist. The Envoy drains and writes the bytes the pump already holds, then
snapshots the offset at the event for both ends of the empty range, which is the
same offset the pre-start failure rule above already requires. Its `cwd` is the
most recent one reported — by the previous operation's result, or by `ready`
when none has completed — since the operation itself exchanged nothing with the
driver.

Because terminal and telemetry use different TCP connections, telemetry can be
received first. The controller does not act on the barrier until its raw log
has reached `output_through`. Offsets never regress. Completion ranges satisfy
`output_start <= output_through` and repeat the operation's original start.

For split execution the Envoy emits the marks covering an operation's range
before the terminal result. Marks carry offsets and stream identity rather than
output bytes, so the terminal connection remains the only path workload output
travels. The completion barrier remains the cross-channel authority: the
controller does not evaluate a mark until its raw log has reached that mark's
offset.

Output from a surviving background job after a completion barrier is outside
that completed operation's range. It remains in the exact terminal stream and
is covered by a later barrier or final drain.

## Planned prompt and command presentation

Prompts and displayed commands are controller presentation, not workload
output. Before sending `execute`, the controller commits these events in order:

1. planned prompt;
2. typing-start timeline event;
3. displayed-command characters with planned timing;
4. displayed newline; and
5. typing-end timeline event.

It then sends `execute`. It synthesizes a following prompt only after the
completion event's output barrier is satisfied, and never after a terminal event
carrying `shell_ended`, since no shell remains to prompt. Synthesized events
carry controller-presentation provenance and never enter the private raw-output
log or its byte offsets.

The direct writer produces asciicast v3 JSON Lines. Its first line is compact
JSON with `version: 3` and `term: {"cols": C, "rows": R}` using the initial
applied dimensions. Existing optional OmegaFlow header metadata such as title
is copied under its existing schema; the Envoy path adds no new public header
field. Later lines are compact three-item arrays:

```text
[DELTA_SECONDS,"o",TEXT]
[DELTA_SECONDS,"r","COLUMNSxROWS"]
```

`DELTA_SECONDS` is non-negative elapsed time since the preceding cast event,
with at most six decimal places. One controller-owned serialized writer assigns
the total order, but for realtime-timed output it does not assign the times.
Realtime workload output takes the `elapsed_us` of the mark covering its offset,
and an accepted resize takes the `elapsed_us` of its `resize_applied`. Each
written delta is the difference between consecutive absolute microsecond values,
so rounding error cannot accumulate across a long recording.

Presentation-timed operations are the deliberate exception. Their whole purpose
is to discard real command duration, so their published output takes the
authored presentation schedule rather than mark time, and marks supply only
stream attribution and relative order within the operation. Mark `elapsed_us`
values inside such an operation are retained as private evidence and are not
published as cast times.

The two clocks meet through one signed running offset, re-anchored at every
return from an authored schedule to sender time. A session has two authored
schedules: the controller's synthesized prompt and typing presentation, and a
presentation-timed operation's compiled schedule. Both commit events at times
they choose, and each is mapped onto the session timeline starting at the last
committed absolute time. The offset is then set from the first Envoy-stamped
operation boundary after the span, not from whatever sender-timed event happens
to arrive next. For a prompt and typing span that boundary is the mark preceding
`operation_started`, or, when the operation fails or is cancelled before it
starts, the mark preceding the `operation_failed` or `operation_cancelled` that
replaces it; for a presentation-timed operation it is the mark preceding its own
terminal event. Every one of those events carries `output_start` or
`output_through`, so the mark rule below guarantees the boundary exists. The
offset becomes that boundary's sender time minus the last absolute time the span
committed, and every later event publishes at its source time minus that offset
until the next re-anchor.

A between-operation mark can also race the authored span itself: a permitted
background writer may emit after its operation completed but while the
controller is committing the next prompt and typing span. The controller
therefore publishes its received frontier before it begins the span, so
everything it already holds appears ahead of the prompt, and a straggler that
arrives once the span is underway is clamped to the span's end like any other
event behind the seam — real bytes displaced by presentation, which the raw log
and marks still record at their true times.

Anchoring on a stamped boundary rather than on the next event is what keeps a
slow command honest. `sleep 5; echo done` produces its first output five seconds
after the operation starts; anchoring on that output would map it to the end of
the authored span and erase the five seconds, while anchoring on the start
leaves the delay after the anchor, where it survives at its real length. The
transport delay between the controller committing the typing schedule and the
Envoy starting the operation falls before the anchor and is absorbed, which is
the intended treatment for controller and transport time.

Re-anchoring after every authored span, rather than only when a presentation
operation ends, is what makes the sender-assigned claim true. Mapping the
authored span alone places it correctly but leaves the session clock running at
real time, so the next sender-timed event would land a full span later: after a
compressed command that reopens exactly the pause the operation exists to
remove, and after an authored prompt it turns however long the controller was
descheduled into a cast pause. The offset is signed for the inverse case: an
authored schedule that advances faster than real time pushes later events
forward instead of letting the monotonicity rule collapse them onto one instant.
Every real gap between sender-timed events keeps its length, and `elapsed_us`
and the private raw log keep the real clock untouched throughout.

Because a session can therefore mix authored-schedule times with mark times, the
writer enforces one monotonicity rule over the merged stream: an event whose
source time, after the running offset is applied, precedes the last committed
absolute time is committed at that last time instead. The only event retimed
rather than clamped is a resize applied while an authored schedule is being
committed, which is placed onto that schedule as described under Resize; nothing
else is retimed. Sender-stamped realtime output never triggers this, since marks
are already non-decreasing. The rule guards the seam where an authored schedule
hands back to sender time, which is the only place the two clocks meet.

Timing is therefore sender-assigned. Transport delay, controller scheduling,
and controller backpressure cannot deform the recorded timeline, and only the
Envoy must stamp promptly. Wall-clock changes cannot affect event timing on
either side. Planned prompt and displayed-command output uses the authored
typing schedule on that same timeline and is committed before `execute`. Events
sharing an absolute time retain writer queue order. The writer emits a resize
event only after accepting the matching `resize_applied`, using `columns`
followed by `x` and `rows`. Terminal input does not create an asciicast input
event; ordinary PTY echo, if any, returns as output.

Terminal bytes are decoded for asciicast output using an incremental UTF-8
decoder with replacement (`U+FFFD`) for invalid input and an EOF flush. Decoder
state spans TCP reads, so splitting a valid multi-byte character does not alter
it. Decoder state is scoped to one published logical stream under one
publication policy, and is flushed at every policy or published-stream boundary,
so bytes never combine across a suppressed, replaced, or differently ordered
range; an `echo` span is not such a boundary, because it publishes inside its
operation's stream. A sequence left incomplete at such a boundary decodes as
replacement rather than joining the next range. Text completed by a later read
uses that later read's timestamp; an EOF replacement uses the final-drain
timestamp. Empty decoded chunks are omitted. The exact undecoded bytes remain in
the private raw log.

For `realtime` plus `real`, the controller publishes decoded PTY reads
incrementally as they arrive, so a live view stays responsive. Arrival time
drives only that live view; the recorded timeline uses mark times, so the
artifact is identical whether or not anyone watched it and whether or not the
controller kept up. Presentation-timed `real` operations retain the operation's
raw range while the command runs; after completion, the output-through barrier,
and the logical post-enter pause, the controller publishes logical stdout
followed by logical stderr.
Command wall time and raw arrival timestamps do not advance that presentation
schedule. `suppress` publishes no observed bytes. `replace` publishes no
observed bytes and commits controller-owned replacement text at the same
buffered publication point. None of these choices changes raw-log retention.

An unchecked `real` operation may leave a permitted supervised background
writer, so `pty` bytes from that earlier operation can land inside a later
operation's raw range. Those bytes are not that operation's output and are not
published by its policy. Because only an unchecked `real` operation can leave
such a writer, they are ordinary real terminal output: they belong to no
operation's logical stream and publish at their own mark times, which is why
marks are session-scoped rather than operation-scoped. No such mark can fall
inside a presentation-timed operation's range, because presentation timing
requires exclusive observation and exclusive observation refuses to start until
the supervised writer set is empty. The same is true of suppressed and replaced
operations for the same reason. Surviving marks therefore only ever appear
between operations or inside an unchecked `real` one.

## Action gates

The trusted operation source may call the `awsh` gate helper. `operation_ready`
is emitted only after its output barrier is established. Browser or controller
actions may then run. A matching `continue` releases only the current gate, and
`operation_continued` confirms release. Gate IDs cannot be reused within an
operation. Terminal input remains available while gated.

## Cancellation

`cancel` names the active operation and a bounded reason. The corresponding
`operation_cancelled.reason` must match it exactly. An operation still held at
the terminal-input barrier has not started, so cancelling it sends no signal and
reports no status, as described under the session state machine; everything
below concerns an operation that is running. The Envoy sends `SIGINT` to the PTY
foreground process group and starts the five-second grace period. If the
persistent driver returns and the operation's observation is `exclusive`, the
Envoy first terminates and reaps every tracked descendant, exactly as at the
ordinary exclusive boundary, before anything is reported — a writer surviving an
operation whose policy withheld its output would otherwise keep writing into
later session-scoped bytes and publish what `suppress` or `replace` was required
to withhold — and a cleanup failure fails the operation instead. An unchecked
`real` operation keeps its shell-faithful survivors at cancellation exactly as
at completion. The Envoy then drains output and emits `operation_cancelled` with
the shell status, normally 130. If the driver does not return, the Envoy
terminates the persistent process group and emits `operation_failed` with
`cancel-timeout`. A cancelled operation never emits `operation_completed`.

A `cancel` can also arrive after the driver has returned, while the Envoy is
still resolving the inspection plan — the one phase that can outlast the command
itself, since hashing runs to the per-operation byte limit. There is nothing to
signal there, so the Envoy abandons the inspection immediately and emits
`operation_cancelled` with the status the driver returned and no inspection
results. That is how the event is already shaped, and it follows the rule that
cancellation invalidates assertions rather than evaluating them: without it, an
operation the controller had bounded could keep hashing up to 16 GiB after its
deadline had passed.

A `cancel` that crosses its operation's own terminal result is not a failure.
The Envoy may send `operation_completed` and return to idle while the
controller, which has not yet seen it, sends `cancel` or `finalize` for that
operation; the request is accepted and discarded, and the terminal result the
controller is already about to receive resolves it.

Connection loss and controller-session cancellation use the same process-group
cleanup but fail the capture even if Bash later returns successfully.

## Planned recording-end finalization

`finalize` is distinct from cancellation. It names an intentionally open
running, gated, or continuing operation and a bounded reason. An operation
still in `Starting` is never finalized: a recording that ends while the
terminal-input barrier holds cancels it instead, taking the pre-start
cancellation path, which sends no signal and reports no status. The compiled
lifetime policy — which operation is intentionally open, and when the recording
ends it — stays on the controller side and reaches the Envoy only as this typed
request, so the termination sequence is fixed rather than configured and
implementations do not invent tree teardown. The Envoy delivers the
interruption exactly as cancellation does — `SIGINT` to the PTY foreground
process group, which interrupts even source running inside the persistent driver
without killing the driver, because the persistent process group is never part
of operation teardown — and waits the cancellation grace period for the driver
to return. It then terminates and reaps every remaining tracked descendant
exactly as at an ordinary exclusive boundary, drains the final output, emits any
remaining split-stream evidence, and emits `operation_finalized` with the
matching reason and closed output range. A driver that does not return within
the grace period fails the operation, exactly as under cancellation.

A `finalize` can reach the same inspection phase a `cancel` can, after the
driver has returned but while the Envoy is still resolving the plan. There the
observed result wins: the operation completes with the status the driver
actually returned — the `Finalizing --> Idle` completion edge exists for exactly
this race — and the finalize is discarded like any other request that crossed
its own terminal result. Synthesizing a status-free finalization instead would
throw away a real exit status and leave an authored exit-code assertion with
nothing to evaluate, when the command it describes had already finished
normally.

`operation_finalized` deliberately has no status. Its synthetic termination
outcome cannot satisfy or fail an authored exit-code assertion. The controller
may evaluate non-exit assertions over the complete range and logical stream
evidence. The tree intentionally terminated by finalization is not a
surviving-writer violation; any writer that remains after cleanup fails the
operation. Failure or user cancellation invalidates assertions instead.

Finalization is always controller-requested. An operation whose shell simply
ends completes instead, with the status the Envoy reaps, as described under the
private protocol.

## Resize

The controller sends the complete target `columns` and `rows`. The Envoy applies
`TIOCSWINSZ` to the PTY master and emits `resize_applied` only after success,
stamped with the `elapsed_us` at which it was applied so the cast orders resizes
against output by sender time. A resize applied while any authored schedule is
being committed is the exception, whether that is a presentation-timed
operation's compiled schedule or the controller's synthesized prompt and typing
span: publishing it at its own `elapsed_us` would advance the cast clock to real
time before the authored events that follow it, exposing a command's discarded
duration in the first case and the controller's own scheduling in the second,
and the events after it would then be pushed forward or collapsed by the
monotonicity rule. Such a resize is retimed onto the authored schedule in
progress and published at its position within it, and the authored events
continue unchanged. The kernel delivers `SIGWINCH` normally. A failed resize
emits a fatal diagnostic and fails the operation or session; it is never
acknowledged with different dimensions.

## Shutdown and drain

`shutdown` remains valid only while idle, after any planned finalization has
returned the session to idle. The following `draining.reason` must match the
shutdown reason exactly. The private `shutdown` request carries no reason, so
the driver's `closed` result answers it with the fixed reason `shutdown`; the
controller-facing telemetry reasons are not derived from that constant. The
Envoy asks `awsh` to close, supervises the persistent process group, drains the
PTY to EOF, and emits `draining` with the current barrier. It then half-closes
terminal output and emits `closed` with the final exclusive offset and the same
reason it drained under. The controller waits for both the raw log to reach that
offset and terminal EOF before finalizing its cast.

An early EOF or reset on either channel is a distinct failure. A telemetry EOF
between complete frames is not success until a valid `closed` was accepted.

## Private Envoy-to-`awsh` protocol

The private descriptor protocol uses UTF-8 fields separated and terminated by
NUL. Every frame starts with `awsh-v1` and a message type. Field arity is fixed
by type; NUL cannot appear in source or other values.

Requests:

```text
awsh-v1, execute, OPERATION_ID, EXECUTION_SHAPE, OBSERVATION, INSPECTIONS_JSON, BASH_SOURCE
awsh-v1, continue, OPERATION_ID, GATE_ID
awsh-v1, cancel, OPERATION_ID, REASON
awsh-v1, finalize, OPERATION_ID, REASON
awsh-v1, started_ack, OPERATION_ID
awsh-v1, shutdown
```

Results:

```text
awsh-v1, ready, SHELL_PID, CWD
awsh-v1, started, OPERATION_ID
awsh-v1, gate_ready, OPERATION_ID, GATE_ID
awsh-v1, gate_continued, OPERATION_ID, GATE_ID
awsh-v1, completed, OPERATION_ID, STATUS, CWD, RESOLVED_INSPECTIONS_JSON
awsh-v1, protocol_error, CODE, MESSAGE
awsh-v1, closed, REASON, CWD
```

The Envoy validates and bounds a complete request before forwarding it. Partial
fields, unsupported types, invalid UTF-8, invalid arity, and EOF in the middle
of a frame are protocol failures.

EOF between frames is not a driver fault, because a recording reproduces a shell
the workload is allowed to end. Operation source may end it with `exit 7`, with
an `errexit` failure, or by replacing the image through `exec`.

The status comes from a boundary that operation source cannot reach. The Envoy
is the shell's parent, so it learns the exit status by reaping the child, and no
`EXIT` trap is involved: source that writes its own trap, as in `trap cleanup
EXIT; exit 7`, keeps that trap and its behaviour, and the status still arrives.
An earlier draft of this contract had the driver install the trap itself, which
source could replace, silently costing the operation its status.

Reaping also removes the need to tell `exec` from a crash. Every candidate
observation for that was racy or undecidable — a short replacement such as `exec
/bin/true` can exit before the EOF is even processed — and a terminal does not
distinguish them either. It no longer matters: `exit 7` reaps 7, an `errexit`
failure reaps its status, `exec /bin/true` reaps the replacement's, and a
termination by signal N reaps `128 + N`, the same value a shell reports for a
signalled child. That conversion is the only one: `status` is bounded 0 through
255, signal numbers stay well below 128, and naming the shell convention leaves
an exit-code assertion one value to compare instead of two implementations
disagreeing about how to spell a signal. Each is what that terminal would have
shown, so all of them are one outcome carrying a real status.

The Envoy emits `operation_completed` carrying that reaped status, so an
authored exit-code assertion sees it and the ordinary completion rules apply
unchanged. It also sets `shell_ended` to boolean `true`. That is the field's
only value, and it is absent from every ordinary completion rather than present
and false, so a strict decoder has one representation to accept instead of a
discriminator whose spelling two implementations could choose differently. The
status alone would not tell the controller that Bash is gone, and a controller
that could not tell would synthesize the configured following prompt for a shell
that no longer exists, or start the next operation before learning otherwise
from `draining`. Its `cwd` is the last one the driver reported, since none can
be observed after the shell is gone. Nothing here is a failure, so nothing
discards the operation's evidence — unless it declared inspections, which cannot
be resolved once the descriptor has closed, and an unevaluated authored gate is
reported as `operation_failed` rather than passed. The Envoy keeps reading the
PTY, since whatever holds the terminal now owns it. For an unchecked `real`
operation those bytes are ordinary real terminal output; for `suppress`,
`replace`, or a checked `real` operation a writer surviving the shell fails the
operation instead, because publishing its bytes would leak content that
operation's policy required withholding. No further operation can be executed
without a control descriptor, so the session moves to its terminal state and
finalizes at the drain. A plan with a later beat therefore still fails, because
that beat cannot run. EOF after the Envoy has requested shutdown remains clean.

`INSPECTIONS_JSON` is the compact JSON encoding of the already validated public
inspection array. `RESOLVED_INSPECTIONS_JSON` retains each inspection's
identifiers and kind, replaces `path` with the absolute `resolved_path`, and
does not contain filesystem results. Both are one NUL-free bounded field. Awsh
uses the persistent shell state only to resolve the plan; the Envoy owns all
filesystem access, type checks, hashing, and public result construction.

`cancel` or `finalize` is recorded on the descriptor when the driver is waiting
at a gate. While Bash is executing, signal delivery is authoritative and the
eventual driver result is translated according to the Envoy's public
cancelling or finalizing phase. The driver does not invent a natural exit
status for planned finalization. The Envoy ignores the driver status while
finalizing, but uses the returned cwd and resolved inspection plan after the
operation tree is closed.

## Controlled Bash launch

The production executable is fixed at `/bin/bash` and begins with
`--noprofile --norc`. It does not honor `AWSH_BASH`. Before launch, OmegaFlow
removes these delegated application variables:

```text
AWSH_BASH BASH_COMPAT BASHOPTS BASH_ENV BASH_XTRACEFD CDPATH ENV
GLOBIGNORE POSIXLY_CORRECT PROMPT_COMMAND PS0 PS1 PS2 PS3 PS4
SHELLOPTS TMOUT
```

It also removes every name beginning with `BASH_FUNC_`, `LD_`, or `AWSH_`; the
`AWSH_` prefix belongs to the launch contract. Loader variables go because the
dynamic loader consumes them before Bash reads a single flag, which would run
application-controlled libraries inside the process that holds the private
descriptors. Blueprint validation already rejects application-declared `ENV`, `BASH_ENV`,
`LD_`, and `AWSH_` names — the enumeration the Reploy environment design owns —
before anything is deployed, so for a validated blueprint this
filter is a backstop rather than the enforcement point. A workload whose
commands need a loader variable sets it inside operation source, where the
persistent shell carries it to operation children as ordinary shell state while
the driver's own image never loads under it. Environment names must be
non-empty, contain neither `=` nor NUL, and values cannot contain NUL. Other
application values, including `PATH`, are delegated unchanged after this
control-plane baseline.

The Envoy owns the TCP sockets with close-on-exec. Bash receives the PTY slave,
dedicated private request/result descriptors, and, for split execution,
operation-scoped stdout/stderr descriptors. The Awsh alignment slice must
prevent ordinary operation children from inheriting the driver descriptors.
Split-stream descriptors are inherited only as required by the evaluated
command tree and are supervised and closed at its typed boundary.

## Failure mapping

Malformed, oversized, out-of-sequence, out-of-state, wrong-operation, and
regressing-offset messages fail closed. When possible, the side detecting a
failure records a bounded diagnostic before closing; diagnostic delivery is
best effort and never converts failure to success.

`operation_failed` carries one of a closed v1 code set: the five inspection
codes above; `input-barrier-timeout` for a terminal-input barrier wait that
exceeds its bound; `cancel-timeout` and `finalize-timeout` for a driver that
does not return within the grace period; `echo-span-unclosed` for an echo
span the Envoy cannot close; `surviving-writer` for a writer surviving a
policy that forbids it; `retained-writer-limit` for a completion that would
exceed the retained-writer budget; `tracked-descendant-limit` for an
operation exceeding the tracked-descendant budget; `shell-ended-unresolved`
for a shell end leaving declared inspections or an authored gate
unevaluable; and `exclusive-cleanup` for a cleanup or census failure at an
exclusive boundary. Codes keep the diagnostic shape, and adding one is a
schema change under the versioning rule.

The controller retains partial raw output, cast, timeline, accepted telemetry,
and the structured local cause, then asks Reploy to terminate. Envoy success
never overrides a failed Reploy lifecycle or cleanup result.

## Conformance fixtures

The canonical corpus is under `tests/fixtures/envoy-protocol-v1`. Delivery
slice B1 updates it for this amendment. Until that slice is accepted, the
checked-in files represent the approved pre-amendment baseline and are not
evidence of compatibility with the amended v1 contract. The amended corpus
contains:

- `controller.jsonl`: exact controller request encodings;
- `envoy.jsonl`: exact Envoy event encodings, including output marks covering
  stream identity and sender timing, workload inspection results, and planned
  finalization; and
- `awsh-frames.json`: exact private frames represented as hexadecimal bytes.

The inspection corpus covers defined and undefined environment references, `~`
and `~user`, relative paths after `cd`, files, directories, symlinks, nested
special entries, missing and unsupported top-level paths, deterministic
request/result correlation, cleanup-and-drain-before-hash ordering, every
inspection budget, and complete public and private frames near and beyond their
aggregate byte limits. File digest cases are also evaluated by a compatibility
test against the native runner algorithm before that runner is retired.
Directory digests deliberately do not match it: the corpus freezes both the
native `directory` encoding and the framed `directory-v2` encoding this protocol
requires, and the gate is that a recording made under either is identifiable
under its own tag, not that the two agree.

The Go protocol implementation consumes these files as its canonical wire
corpus. Future controller implementations, including Python integration, must
consume the same corpus before they are accepted as v1-compatible. Schema
changes after this pre-release inspection amendment is approved require a new
version and fixture directory; accepted v1 fixtures are never silently
rewritten to represent a different contract.
