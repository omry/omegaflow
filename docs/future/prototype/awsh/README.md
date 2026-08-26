# awsh prototype

`awsh` (the "awful shell") is a deliberately small experiment for the
shell-resident half of the proposed OmegaFlow Envoy architecture. Its entrypoint
is POSIX `sh`; it replaces itself with an explicitly selected Bash running the
stateful driver. The driver is not a new shell implementation: one Bash process
reads operations from a private file descriptor, evaluates them in its own
process, leaves terminal I/O on its controlling PTY, and reports structured
results on another file descriptor.

This directory is not part of the OmegaFlow package and is not production code.
The experiment intentionally does not implement the Envoy TCP protocol,
authentication, bounded frames, cancellation, resize, reconnect, or Reploy
integration.

## Running it

The Envoy-shaped parent supplies two inherited descriptors and starts the
POSIX-compatible `awsh` launcher with its standard streams attached to the PTY:

```text
awsh --request-fd 20 --result-fd 21
```

The launcher resolves `bash` through `PATH`, or uses the executable named by
`AWSH_BASH`, and then executes `awsh-driver.bash`. Bash is therefore an explicit
workload requirement even though `/bin/sh` is sufficient to enter `awsh`.
`AWSH_BASH` is prototype-only; the production Envoy uses a fixed Bash
executable and a controlled launch environment.
The prototype launcher must be invoked with an absolute or relative path so it
can locate the adjacent driver without running helper commands that might reuse
its inherited private descriptors. The examples reserve descriptors 20 and 21,
and the driver reserves descriptor 22 while it sources one operation. The
launcher rejects 22 as a request or result descriptor. Low descriptors such as
3 and 4 are unsafe because a script interpreter may use them internally while
opening the launcher or driver.

Both directions use NUL-delimited fields. Every message starts with `awsh-v1`.
The prototype request messages are:

```text
awsh-v1, execute, OPERATION_ID, BASH_SOURCE
awsh-v1, continue, OPERATION_ID, GATE_ID
awsh-v1, cancel, OPERATION_ID, REASON
awsh-v1, shutdown
```

The result messages are:

```text
awsh-v1, ready, BASH_PID, CWD
awsh-v1, started, OPERATION_ID
awsh-v1, gate_ready, OPERATION_ID, GATE_ID
awsh-v1, gate_continued, OPERATION_ID, GATE_ID
awsh-v1, completed, OPERATION_ID, STATUS, CWD
awsh-v1, protocol_error, CODE, MESSAGE
awsh-v1, closed, shutdown, CWD
```

Commas above separate fields for readability; the wire delimiter is a NUL byte.

These are the prototype's own messages, not the frozen contract. They reuse the
`awsh-v1` tag while diverging from the private protocol in
[Envoy Protocol v1](../../../design/envoy-protocol-v1.md) in four ways. Its
`execute` also carries the execution shape, observation, and inspection plan,
and its `completed` also carries resolved inspection results, so both differ in
arity from the messages above. It defines `started_ack`, `continue`, `cancel`,
and `finalize` requests and `gate_ready` and `gate_continued` results that this
prototype does not implement, including the `started_ack` barrier that keeps a
fast command's first bytes inside its own output range. It answers a private
`shutdown` with the fixed reason `shutdown`, where this prototype also uses an
`eof` reason of its own. And it requires that ordinary operation children not
inherit the driver descriptors, which this prototype does not yet arrange —
they do inherit, as the launcher note above reflects.

Delivery slice B2 replaces this Bash-resident request loop with the external Go
Awsh supervisor and controlled Bash-helper boundary, then aligns that
implementation with the frozen grammar and descriptor-isolation rules. It does
not extend this prototype's shared workload/control namespace. Until then, do
not read a prototype frame as evidence of v1 conformance.

What the prototype does already match is the shell-exit path. It installs no
`EXIT` trap, so operation source keeps its own, and `exit 7` exits the driver
and closes the descriptors rather than reporting a status — which is exactly
what the frozen protocol expects, since there the Envoy is the shell's parent
and learns the status by reaping it.
Bash source may contain newlines but not NUL bytes. Operation IDs are limited to
1–64 ASCII letters, digits, dots, underscores, or hyphens and must begin with a
letter or digit.

Run the prototype checks from the repository root:

```text
pytest -q docs/future/prototype/awsh
```

### Split-screen testing console

For exploratory testing, `awsh_demo.py` supplies the missing human-facing
command reader and opens a tmux split with terminal output on the left and
protocol activity on the right:

```text
./awsh_demo.py
```

Enter submits one line of Bash source as an operation. Ctrl-C while an
operation is active is forwarded through its PTY; Ctrl-D at the prompt sends a
structured shutdown request. The prompt reflects the cwd reported by `awsh`.
Python's GNU Readline integration supplies line editing, in-session history,
reverse search, and basic path completion.

When an operation calls `awsh_gate GATE_ID`, the testing console logs the gate
and immediately sends the matching `continue` request. Production coordination
will instead let the OmegaFlow controller perform the planned browser or host
action before continuing the gate.

The activity pane labels submitted source as a wrapper-local `request`, then
shows the `started`, `completed`, and other events decoded from `awsh`'s result
stream. Request source is visible in this testing UI, so do not submit secrets
that should not be displayed.

The console requires Python 3 and tmux. It is testing scaffolding rather than
part of the private protocol or proposed Envoy. It deliberately does not try
to reproduce Bash's parser-driven continuation prompts, startup files,
history expansion, or programmable completion functions. Background-job
output produced while the Readline prompt is idle may not appear until the
next operation starts.

## What this can establish

- For the tested names that do not collide with driver primitives, `cd`,
  ordinary variable values and positive declaration attributes, functions,
  aliases, and shell options persist because evaluation happens in the driver
  Bash rather than a child shell.
- The entrypoint can be launched by Reploy's `/bin/sh`; Bash-specific syntax is
  confined to the driver Bash.
- Programs inherit the PTY as standard input, output, and error.
- Visible output can stream while the operation is still running.
- For the tested top-level source shape, terminal Ctrl-C reaches the active
  foreground process, aborts the remaining top-level source, reports status
  130, and leaves the driver available.
- Action gates stop the operation on the private request channel until a
  matching continue or cancel request arrives. Operation source must use
  `awsh_gate GATE_ID || return $?` so cooperative cancellation unwinds the
  sourced operation.
- PTY resize reaches Bash and interactive children through normal `SIGWINCH`
  delivery; curses and one nested interactive Bash use the same PTY.
- Status and resulting cwd do not have to be inferred from terminal output.
- Marker-like terminal text cannot become a telemetry event because results use
  a different descriptor.
- Only a structured shutdown closes cleanly. EOF before shutdown and EOF inside
  a field are reported as protocol failures.
- A disowned Bash `coproc` broker exclusively owns the external request and
  result descriptors. Its internal pipe descriptors remain usable by driver
  functions but are close-on-exec, so ordinary operation children inherit none
  of the control descriptors.
- Shell `exit` and `exec` produce an intentionally partial result: `started`
  may be retained, but no fabricated `completed` event is emitted.

## Known limits

- This is a cooperative protocol, not a security boundary. Evaluated source can
  alter traps, shell options, functions, and open descriptors, or terminate or
  replace `awsh`.
- Driver control and workload source share Bash's function namespace. Persisted
  workload functions named `.`, `exec`, `unset`, `return`, or `builtin` can
  intercept evaluation, descriptor, cleanup, or status paths and may skip
  source or structured completion. This prototype does not reserve the complete
  driver primitive set; B2 removes this control boundary from workload Bash.
- Declaration replay demonstrates attributes that are added or retained, not
  every attribute transition. Removing an existing global attribute with forms
  such as `declare +x` or `declare +i` is not faithfully persisted, so a later
  operation or child process can still observe the old attribute. B2's
  controlled helper boundary owns the complete Bash-state contract.
- Frames are not bounded. The production Envoy must impose limits before it
  forwards an operation to the driver.
- Operation source is sourced from a reserved descriptor so shell state stays
  in this process and the driver's SIGINT trap can return from the operation.
  It is trusted recording-plan input, not a quoted argument or an authorization
  decision.
- Running `source` as the condition of an `if` lets the driver collect a failing
  status, but Bash suppresses `errexit` behavior in conditional contexts. Exact
  `set -e` semantics are therefore an explicit open problem.
- The broker prevents accidental descriptor inheritance, not deliberate
  same-shell interference. Cooperative source can still close the broker pipes,
  mutate driver globals, terminate Bash, or inspect same-identity processes.
- SIGINT is reserved by this cooperative prototype while an operation runs.
  Operation source can replace the trap, so a production driver must protect or
  restore that control-plane behavior more strongly.
- The prototype's SIGINT trap uses Bash function return as its recovery
  boundary. An interrupt inside a workload-defined nested function can unwind
  only that function and let outer source continue. A narrow interrupt after
  source return but before the idle trap is restored can also omit the
  structured completion. Delivery slice B2 replaces these prototype limits
  with the Envoy/controller cancellation boundary.
- One background-job persistence path is covered, but complete job-control
  semantics and hostile changes to traps, descriptors, or shell control state
  remain outside the supported cooperative boundary.
