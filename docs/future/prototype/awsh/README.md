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
awsh-v1, shutdown
```

The result messages are:

```text
awsh-v1, ready, BASH_PID, CWD
awsh-v1, started, OPERATION_ID
awsh-v1, completed, OPERATION_ID, STATUS, CWD
awsh-v1, protocol_error, CODE, MESSAGE
awsh-v1, closed, eof|shutdown, CWD
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

Delivery slice B2 aligns the driver with the frozen grammar and adds the
descriptor isolation; until then, do not read a prototype frame as evidence of
v1 conformance.

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
pytest -q docs/future/prototype/awsh/test_awsh.py
```

## What this can establish

- `cd`, exported variables, functions, aliases, and shell options persist
  because evaluation happens in the driver Bash rather than a child shell.
- The entrypoint can be launched by Reploy's `/bin/sh`; Bash-specific syntax is
  confined to the driver Bash.
- Programs inherit the PTY as standard input, output, and error.
- Visible output can stream while the operation is still running.
- For the tested top-level source shape, terminal Ctrl-C reaches the active
  foreground process, aborts the remaining top-level source, reports status
  130, and leaves the driver available.
- Status and resulting cwd do not have to be inferred from terminal output.
- Marker-like terminal text cannot become a telemetry event because results use
  a different descriptor.
- EOF between request frames closes cleanly, while EOF inside the initial
  schema field is reported as a protocol error rather than a normal shutdown.

## Known limits

- This is a cooperative protocol, not a security boundary. Evaluated source can
  alter traps, shell options, functions, and open descriptors, or terminate or
  replace `awsh`.
- Frames are not bounded. The production Envoy must impose limits before it
  forwards an operation to the driver.
- Operation source is sourced from a reserved descriptor so shell state stays
  in this process and the driver's SIGINT trap can return from the operation.
  It is trusted recording-plan input, not a quoted argument or an authorization
  decision.
- Running `source` as the condition of an `if` lets the driver collect a failing
  status, but Bash suppresses `errexit` behavior in conditional contexts. Exact
  `set -e` semantics are therefore an explicit open problem.
- The request and result descriptors must enter Bash, so they cannot both be
  close-on-exec at Bash startup. Preventing later commands from inheriting them
  requires a stronger descriptor-handoff design than this prototype provides.
- SIGINT is reserved by this cooperative prototype while an operation runs.
  Operation source can replace the trap, so a production driver must protect or
  restore that control-plane behavior more strongly.
- The prototype's SIGINT trap uses Bash function return as its recovery
  boundary. An interrupt inside a workload-defined nested function can unwind
  only that function and let outer source continue. A narrow interrupt after
  source return but before the idle trap is restored can also omit the
  structured completion. Delivery slice B2 replaces these prototype limits
  with the Envoy/controller cancellation boundary.
- Resize, complete job-control semantics, `exec`, and nested interactive shells
  need dedicated experiments before this shape is adopted.
