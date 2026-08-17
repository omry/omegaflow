# Reploy Recording Environments Design

## Status

- Approved environment direction with the current Hydra blueprint and placement
  amendments under re-review. The trusted implementation boundary remains PR 8.
  Runtime, controller, terminal, browser, publication, and packaging changes in
  the former PR 9–13 stack are raw material, not accepted implementation
  evidence.
- Updated: 2026-08-17
- Scope: Reploy-backed OmegaFlow execution environments, application
  blueprints, and project bootstrap

The [OmegaFlow Workload Envoy Design](omegaflow-envoy-design.md) is
authoritative for terminal control, recording-PTY ownership, shell telemetry,
terminal-to-browser coordination, and their implementation sequence. This
document remains authoritative for the broader Reploy environment, blueprint,
bootstrap, lifecycle, artifact, packaging, and migration direction. It does not
define a second terminal implementation path.

This document expands the Reploy environment item in
[the backlog](../BACKLOG.md). It records the intended product and configuration
model. Reploy supplies the public controlled-session v1 boundary that OmegaFlow
will consume. OmegaFlow conformance, the initial distribution image,
configuration and CLI integration, source materialization, and migration
cutover remain delivery work.

## Summary

Reploy becomes OmegaFlow's required recording-toolchain substrate and a Python
dependency of OmegaFlow. A supported container runtime is the only normal
external host dependency. The controller, browser, media, narration, and
publication tools always run in the OmegaFlow-owned Reploy environment; users
do not install those tools directly on the host.

The two placement choices remain explicit:

```yaml
studio:
  recording_backend: reploy  # reploy | host; host is reserved and errors today
  workload_backend: host     # host | reploy
```

`studio.recording_backend` defaults to `reploy`. `host` reserves a future
bare-metal recording toolchain but currently produces a capability error.
`studio.workload_backend` defaults to `host`. The host workload uses a
host-running Envoy and `awsh` while the Reploy controller retains all capture
authority. An explicitly selected Reploy workload requires a complete
application blueprint and inherits no undeclared host dependencies. Missing or
invalid isolated configuration fails; it never falls back after `reploy` is
selected.

The design separates two environments:

1. The **OmegaFlow toolchain environment** contains OmegaFlow and its capture,
   processing, narration, and publishing dependencies. OmegaFlow owns this
   stable internal Reploy blueprint.
2. The **recorded workload** contains the project, tools, packages, services,
   and state that appear in the recording. It runs on the host by default. When
   isolated execution is selected, the project owns its Reploy application
   blueprint as a Hydra config-group entry.

Hydra produces complete typed controller and workload blueprint objects,
OmegaFlow materializes their native YAML, and Reploy remains authoritative for
blueprint semantics, Reploy interpolation, package resolution, environment
construction, and execution.

For an isolated-workload recording, host OmegaFlow prepares distinct
toolchain-controller and recorded-workload deployments, then invokes the public
`reploy controlled-session run` command. Reploy injects its release-matched
`reploy-session-client` only into the controller. Controller OmegaFlow consumes
the public JSON Lines lifecycle stream and uses the separate byte-only terminal
attachment to bootstrap the mounted OmegaFlow Envoy and retain its outer
diagnostics. The controller records the Envoy-owned PTY through the Envoy's
lease-private terminal channel. No OmegaFlow process receives the Docker socket
or uses a Reploy-private transport.

Project bootstrap always runs from the application project root. It creates
`.omegaflow` there and records the location of the recording directory, which
may live outside the project root. Bootstrap inspects declarative project
metadata and creates a conservative initial application blueprint. A separate
refresh operation can repeat discovery and produce a candidate blueprint for
the user to merge manually.

## Goals

1. Make Reploy a Python dependency of OmegaFlow so a supported container
   runtime is the only normal external host dependency. Controlled-session v1
   currently selects Docker.
2. Make recorded application state reproducible and isolated from undeclared
   host state.
3. Use native Reploy blueprints instead of inventing an OmegaFlow dependency
   schema.
4. Use Hydra config groups to select, compose, and override application
   blueprints.
5. Keep the OmegaFlow toolchain blueprint internal and versioned with
   OmegaFlow.
6. Anchor project discovery and source access at one unambiguous project root.
7. Allow the recording directory to live inside or outside that project root.
8. Bootstrap useful environments for unrecognized, single-language, and
   multi-faceted projects.
9. Support explicit regeneration of blueprint suggestions without rerunning
   full project bootstrap or overwriting user configuration.
10. Preserve actionable Reploy and OmegaFlow failure diagnostics.
11. Integrate only through Reploy's public host command, controller client,
    terminal attachment, endpoint, output, cancellation, and diagnostic
    contracts.
12. Run the recording controller in Reploy for both workload backends; do not
    retain a separate host recording toolchain.

## Non-goals

1. A second OmegaFlow-specific package or environment schema.
2. Perfect dependency inference from arbitrary source code.
3. Executing project code during bootstrap to discover dependencies.
4. Silently updating an application blueprint during an ordinary build.
5. Divergent recording-plan, artifact, or diagnostic semantics between host
   and Reploy workloads.
6. Giving the OmegaFlow toolchain environment broad Docker-socket access.
7. Designing remote execution, portable environment transfer, or a general
   multi-service orchestrator in this phase.
8. Adding missing Reploy package providers as part of the initial OmegaFlow
   integration.
9. Depending on Reploy's private framed session protocol or Docker
   implementation details.
10. Solving shared-state multi-terminal-pane recording in the first
    controlled-session integration. The terminal-only milestone supports one
    Envoy-owned persistent terminal pane; browser capture follows in a later,
    separately approved stack.

## Terminology

**Project root**
: The application source root from which `omegaflow bootstrap=project` is run.
  It owns `.omegaflow` and is the source location that the recorded
  application environment reproduces.

**Recording directory**
: The directory containing shared recording configuration, recording scripts,
  application blueprint config groups, and recording data. It defaults to
  `recordings` under the project root but may be elsewhere. It is not a second
  project root.

**Toolchain environment**
: The internal Reploy environment containing OmegaFlow and its recording and
  processing dependencies. During a controlled session it is the trusted
  controller deployment.

**Recorded workload**
: The shell, files, tools, services, and browser application being
  demonstrated. It runs on the host by default or in a Reploy workload
  deployment when isolated execution is explicitly selected.

**Application blueprint**
: A project-owned Reploy blueprint selected through a Hydra config group for
  use as the isolated recorded workload.

**Host OmegaFlow**
: The ordinary OmegaFlow CLI installed on the host. It composes blueprints,
  prepares deployments, invokes host Reploy, consumes the final host result,
  and publishes retained artifacts. It does not execute recording actions.

**Controller OmegaFlow**
: The trusted internal controller command inside the toolchain deployment. It
  drives the Reploy session client, terminal recorder, browser runner, action
  coordination, artifact finalization, and acknowledgement lifecycle.

## Isolated Workload Architecture

```text
Host
├── Docker
├── Python environment
│   ├── OmegaFlow
│   └── Reploy
└── project root
    ├── .omegaflow/config.yaml
    └── application source

Host OmegaFlow
└── reploy controlled-session run
    ├── toolchain controller deployment
    │   ├── controller OmegaFlow
    │   ├── reploy-session-client
    │   ├── Playwright and Chromium
    │   ├── ffmpeg, ffprobe, and codecs
    │   └── private capture and diagnostic output
    └── recorded workload deployment
        ├── project source or working copy
        ├── project toolchains and packages
        ├── read-only /omegaflow-runtime
        ├── Reploy bootstrap shell and PTY
        ├── Envoy-owned recording PTY and persistent Bash
        └── demonstrated services and declared endpoints
```

Host Reploy selects exact prepared generations, owns Docker, creates the
private session channel and bootstrap workload PTY, supervises both containers,
and owns bounded cleanup. The controller receives neither Docker nor host
process authority. The workload receives neither the controller session socket
nor the controller output destination.

Terminal capture occurs at the Envoy-owned PTY boundary inside the recorded
environment. Browser capture runs in the controller against endpoint
coordinates granted by Reploy. Media processing may continue in the controller
after workload termination, within the selected controller-finalization
timeout, before OmegaFlow declares its artifacts complete.

## Host Workload Architecture

The host backend retains the same Reploy controller deployment and replaces the
Reploy workload deployment with the packaged Envoy and `awsh` launched against
the host project environment. Host OmegaFlow owns that process lifecycle;
Reploy owns the controller lifecycle and retained output. Terminal, telemetry,
and declared application services cross only through bounded coordinates
prepared before controller launch. The portable public Reploy contract for
those host coordinates remains an implementation prerequisite.

### Public controlled-session boundary

Host OmegaFlow invokes only this public shape:

```text
reploy controlled-session run \
  --controller-dir DIR --workload-dir DIR \
  [--endpoint ID ...] [--columns N --rows N] \
  --output-dir DIR [TIMEOUT OPTIONS] -- CONTROLLER_COMMAND [ARG ...]
```

The controller and workload directories select distinct, already prepared
deployments. A session is bound to their exact current generations. OmegaFlow
uses `--output-dir`, not `--output-file`, because a recording retains several
artifacts and must preserve partial evidence after failure. The controller
runtime must be non-root while Reploy's initial output contract rejects root
controller output mounts.

Inside the controller, OmegaFlow starts:

```text
reploy-session-client client
reploy-session-client attach --socket PATH
```

`client` is the versioned UTF-8 JSON Lines lifecycle boundary. `attach` is the
separate bootstrap terminal-byte boundary. Controller OmegaFlow uses it to
replace the bootstrap shell with `/omegaflow-runtime/bin/envoy` and continues
draining it for outer Envoy diagnostics; it is not the canonical recording
stream. The socket reported by `broker-ready` is treated as opaque data and
passed safely to `attach`; OmegaFlow never opens it using Reploy's private
protocol.

The controller must attach within 10 seconds of `broker-ready`. It then reads
`opened` for granted operations, endpoint coordinates, terminal dimensions,
and the host-owned output-finalization timeout. `ready` permits actions to
bootstrap the mounted Envoy. Recording actions begin only after the Envoy's
terminal and telemetry channels are ready. Startup failure may instead produce
`terminated` before `ready`.

During termination OmegaFlow continues draining the terminal attachment. After
`terminating` and `workload-outputs-finalized`, it closes and finalizes casts,
screenshots, timelines, diagnostics, rendered media, and other private
artifacts. It then sends `complete`, stores the authoritative `terminated`
result, sends `acknowledge-terminated`, and reads until the client closes
cleanly. Failed terminal-output finalization still follows this sequence so
partial artifacts and failure evidence survive.

Host Reploy writes exactly one
`reploy-controlled-session-run-result-v1` JSON object to stdout after valid
argument parsing. Host OmegaFlow stores and evaluates the complete object;
neither the controller exit code nor the attachment exit code independently
establishes success.

### Ownership boundary

| Owner | Responsibilities |
| --- | --- |
| Reploy | Exact deployment admission, the bootstrap workload PTY, endpoint coordinates, lifecycle truth, controller output retention, cleanup, and private crash receipts. |
| Host OmegaFlow | Blueprint composition, deployment preparation, public host invocation, host-process stderr retention, final host-result interpretation, retained-run inspection, and publication. |
| Controller OmegaFlow | Recording plan execution, Envoy connections, terminal-to-browser handoff, browser automation, redaction, media production and validation, publication-candidate construction, artifact finalization, and structured failure preservation. |
| OmegaFlow Envoy | Recording PTY ownership, exact terminal input and output, resize, Ctrl-C delivery, persistent-Bash supervision, structured operation telemetry, output ordering, draining, and bounded workload diagnostics. |
| Recorded workload | Project shell, tools, files, services, and all untrusted terminal or application content. |

### OmegaFlow terminal control

The current `TerminalControlSession` uses controller-local FIFOs and files to
exchange commands, completion status, cwd, output ranges, and action gates with
a local Bash process. Those channels do not cross into the separate workload
container and are not part of the Reploy-backed design.

The Reploy adapter uses the attachment only to execute the read-only mounted
Envoy. The Envoy then owns one recording PTY and exposes separate lease-private
terminal and telemetry TCP channels. Terminal bytes remain the canonical
recording stream but are never parsed as telemetry. The telemetry channel
carries command completion, status, cwd, action gates, output ordering, and
diagnostics through the versioned contracts defined by the Envoy design.

All OmegaFlow-supplied workload executables and scripts are staged from the
installed OmegaFlow release, validated by manifest, and mounted read-only and
executable at `/omegaflow-runtime`. The workload does not require Python or the
controller filesystem. It must provide Bash for the initial `awsh` adapter. The
existing local FIFO protocol remains the current host implementation and is not
extended for Reploy. Replacing it with a host Envoy requires separate design,
parity evidence, and approval.

### Browser handoff

`opened.endpoints` supplies each granted endpoint's stable ID plus its
lease-local scheme, host, and port. Controller OmegaFlow constructs browser
targets from those coordinates and OmegaFlow-owned path or navigation intent.
It never accepts an arbitrary replacement host from terminal output.

The existing file-based `BrowserHandoffBroker` may coordinate processes that
both live inside the controller, but workload code cannot write its files.
Browser destinations and readiness conditions are compiled controller inputs;
workload output cannot replace them. Ordinary sequencing waits for structured
operation completion. When a planned browser handoff intentionally keeps its
workload operation running, controller OmegaFlow waits for
`operation_started`, and races the plan-selected granted endpoint readiness
probe against typed operation completion, cancellation, or failure. It performs
the planned Playwright work only when readiness wins while the operation is
still active; a terminal result observed first or at handoff fails the capture.
It then applies the operation's compiled lifetime policy through normal Envoy
cancellation and output-finalization rules. The handoff consumes no workload
files, OSC markers, terminal text, or workload-originated navigation telemetry.
Playwright, browser checks, endpoint selection, and navigation intent remain in
the controller. A generic Envoy action gate is used only when a running shell
operation deliberately pauses for a controller action already present in the
recording plan. Envoy protocol v1 carries no browser-specific message or
workload-originated navigation intent. Dynamic workload-selected navigation
remains deferred.

## Reploy Recording Toolchain and Workload Selection

`studio.recording_backend` is a typed `reploy` or `host` enum and defaults to
`reploy`. The planned production backend is Reploy. The reserved `host` value
produces a capability error once this configuration surface is delivered; it
reserves a possible future bare-metal controller without implying support.
Pure authoring and source-inspection operations may remain direct CLI
operations. A supported container runtime is therefore an intentional
recording prerequisite, including in CI.

The workload runs on the host unless `studio.workload_backend=reploy` is
selected with a complete workload blueprint. This choice does not change the
recording controller, capture semantics, browser runner, artifact pipeline, or
publication boundary.

## OmegaFlow Toolchain Blueprint

OmegaFlow owns the toolchain blueprint because the dependency set is an
implementation detail of the OmegaFlow release:

- supported Python runtime
- OmegaFlow itself
- Playwright and the supported Chromium build
- ffmpeg and ffprobe
- required WebP and H.264 codec capabilities
- fonts and graphical runtime libraries
- optional narration clients and timing tools

The blueprint is an OmegaFlow-shipped Hydra configuration and is not selected
or extended through the project's config. It should be stored as readable
packaged data rather than as a large Python string. OmegaFlow versions and
tests it with the release that uses it.

The blueprint declares a non-root runtime and one native internal controller
command. That command is an implementation surface between host and controller
OmegaFlow, not a user-facing recording backend. Controller OmegaFlow writes
the exact Envoy terminal bytes to a private raw log and derives the asciicast
without a second PTY or an `asciinema record` process. Before it requests
execution, it adds provenance-marked prompt and displayed-command events from
the recording plan; those presentation events are ordered ahead of workload
output but are not added to the raw workload-byte log. Reploy separately
injects its release-matched public client at session preparation time;
OmegaFlow must not package its own copy of `reploy-session-client`. OmegaFlow
wheels do not bundle asciinema. Both workload backends are recorded by the
Reploy controller; host workloads reach that controller through the Envoy
terminal and telemetry boundary rather than a host recording toolchain.

The command's timing, execution shape, and output mode are fixed before
execution. `realtime` plus `real` uses the PTY-attached path and publishes
decoded output incrementally. Presentation-timed `real` uses split streams,
retains their observed bytes privately while the command runs, and publishes
logical stdout followed by logical stderr only after completion, output-through,
and the configured logical post-enter pause. It does not expose command
wall-clock duration or raw arrival timestamps in the presentation timeline. Raw
output under `suppress` and `replace` remains only in the retained private
range; replacement text is a controller-presentation event committed at the
corresponding buffered-output publication point after output-through.

The raw range is temporal PTY evidence, not foreground-process provenance.
Output assertions therefore require exclusive observation: no surviving PTY
writer from an earlier operation and a fresh output-through drain barrier before
execution. Authored controller input remains supported, with its echo excluded
by construction rather than by trusting the terminal mode: the Envoy marks the
raw-log span around each authored write as `echo`, which belongs to no logical
stream and is never assertion evidence, and marks `pty` again only after the
line discipline has processed the input and the Envoy, as the master's only
reader, has drained the master to empty. Closing on the drain rather than on
input consumption keeps the span an output-side boundary, so echo cannot land
past its closing offset. The span is an attribution over the raw log rather than
a separate published stream, so it publishes with its operation under that
operation's policy and presentation schedule rather than on a clock of its own.
A termios reading taken before the write would only be a check-then-write race,
since the workload owns its own terminal modes. Bytes an application emits
before the span closes are excluded with the echo, so the exclusion can fail an
assertion but never satisfy one, and an Envoy that cannot close the span fails
the operation. Failure cancellation and user cancellation invalidate the
assertions. Planned recording-end finalization is a distinct typed lifetime
result: it terminates and drains the intentionally open operation, closes the
observed range, and then evaluates authored non-exit assertions over that
complete range. The synthetic termination status is not treated as a natural
exit code. `suppress`, `replace`, and checked `real` operations always use
exclusive observation, as does any presentation-timed operation, whose
compressed schedule a surviving real-time writer would otherwise push back out
to wall-clock. The planned operation tree is expected to be terminated by this
finalization; only a writer that survives the final drain is a writer-cleanup
failure.

Checked output preserves the current `stdout + stderr` assertion view rather
than treating temporal presentation order as equivalent. Non-interactive
split-stream operations retain logical stdout and stderr evidence separately, as
mark-attributed slices of the complete retained output, while the Envoy forwards
both streams to the terminal channel in observed order. Interactive operations
intentionally attach both streams to the single slave PTY, as the current
realtime runner does, so their logical stdout is the exact post-line-discipline
PTY range and logical stderr is empty. Stream identity is never reconstructed
from merged PTY bytes, and pre-line-discipline bytes are never inferred from
polled termios state. PTY assertions therefore match the same CRLF conversion
and other terminal transformations as the current realtime runner. Split-stream
evidence bypasses the terminal line discipline and preserves newline-sensitive
stdout-then-stderr checks. The raw terminal log and published cast retain their
exact bytes and are never normalized.

Bash jobs are insufficient evidence because disowned and daemonized descendants
can still write to the PTY. The Linux Envoy acts as a subreaper, tracks
operation-created processes by pidfd, and uses `/proc` to census descendants
that remain in the PTY session or retain its slave. An exclusive operation
that leaves a potential writer behind terminates and drains it under the same
private output policy before failing; inability to prove the set empty or to
drain it fails the session. This supervision is correctness evidence within
the same-identity threat boundary, not security evidence.

Unchecked `real` operations may preserve supervised background writers. Their
ranges are shared, may contain interleaved late output, and cannot satisfy
operation-level output assertions. A later exclusive operation is rejected
until the supervised writer set is empty and a new output-through drain barrier
prevents its remaining bytes from entering the checked range. Produced-output
metadata, diagnostics, and private failure evidence retain shared ranges only
with that limitation and without publishing suppressed bytes.

`file_exists` and `produces` are evaluated inside the workload rather than
against the controller filesystem. Their bounded specifications travel with the
typed operation request. After command execution, `awsh` resolves configured
paths in the persistent Bash's resulting cwd and exported environment and sends
the resolved plan to the Envoy over the private driver channel. The Envoy first
terminates and reaps every tracked operation-created descendant, proves the
descendant and exclusive writer sets empty, and drains output through the
closing offset. It then checks existence and kind and computes deterministic
file or directory SHA-256 values before returning typed inspection or
produced-output records with the terminal result. Controller OmegaFlow records
those records as private run evidence; it does not run hidden probe commands,
access workload paths, parse PTY output for path or hash fields, or publish
absolute paths and digests without a separate sanitizing publication contract.

Secrets such as narration credentials are not embedded in the blueprint.
OmegaFlow continues to scope them to the operations that declare them and to
exclude them from published artifacts and diagnostics.

The existing local requirements are inventoried in
[Runtime dependencies](../runtime-dependencies.md). That inventory becomes
input to the toolchain blueprint rather than end-user installation
documentation once the Reploy path is ready.

## Reploy Blueprints as Hydra Structured Configs

Hydra produces the complete application configuration that OmegaFlow consumes.
Its final `reploy` node contains two immediately usable native Reploy blueprint
objects:

```text
reploy.controller  # OmegaFlow-owned and read-only after composition
reploy.workload    # Envoy defaults composed with the selected application
```

Both objects use the same plain Python dataclass model of the complete public
Reploy blueprint syntax supported by the OmegaFlow release. The dataclasses
have no Hydra dependency; Hydra's `defaults` list remains composition metadata
outside the Reploy model. OmegaFlow performs structural type checking through
OmegaConf. Reploy remains authoritative for blueprint semantics, platform
support, package locking, backend rendering, preparation, and execution.

The initial dataclasses live in OmegaFlow and are checked against Reploy's
accepted blueprint fixtures. They are intended to move, without OmegaFlow
fields, into a Reploy-owned and lockstep-versioned Python distribution such as
`reploy-blueprint-schema` if the model proves reusable.

### Controller blueprint

OmegaFlow ships the complete controller configuration. It includes the exact
OmegaFlow package and compatible Python, browser, media, identity, command, and
read-only controller-input mount requirements. Application configuration never
composes into this object. The resolved controller remains visible for
inspection but OmegaFlow marks it read-only before application code receives
the final config.

The controller configuration uses ordinary Hydra defaults and OmegaConf
interpolation. Values such as the running OmegaFlow version and run directory
come from resolvers; there is no post-composition injection or mutation step.

### Workload blueprint

Application blueprints use the project-selectable `reploy/app` Hydra config
group. A project can define one blueprint shared by all recordings or several
for distinct applications or environment variants. Each entry supplies a
native Reploy blueprint under `reploy.workload`.

OmegaFlow's built-in Envoy config composes into that same object in two parts.
Its overridable defaults compose before the selected application, so an
application can replace them; its reserved entries — the Envoy command and the
two container ports — compose after it, so an application cannot. Composing the
reserved entries first would let an application win under Hydra's ordinary
behaviour and leave Reploy forwarding to a port the Envoy is not listening on.
The defaults are:

- a Bash package and executable requirement;
- a default non-root `omegaflow` identity and terminal-only command;
- the read-only `/omegaflow-runtime` mount;
- fixed endpoint IDs `omegaflow-terminal` and `omegaflow-telemetry`;
- private TCP bind defaults and dynamic host publication for both endpoints;
  and
- the fixed Envoy runtime paths and command.

The terminal endpoint defaults to scheme `tcp`, container port `47001`, and
Docker bind address `0.0.0.0`; the telemetry endpoint uses the same values with
container port `47002`. Container ports are fixed because they live in the
deployment's own network namespace and cannot collide. Host publication is not
pinned: both endpoints publish on `127.0.0.1` with a dynamically assigned host
port, and the controller connects to the coordinates Reploy reports in
`opened.endpoints` rather than to a constant. A pinned host port would make two
concurrent recordings on one Docker host — the ordinary case in parallel CI —
race for the same loopback port, and the losing deployment would fail to bind
before reaching the Envoy handshake. Host publication stays an ordinary typed
value that an application may override, and every resolved value remains visible
in the final blueprint.

The container ports are not overridable, though, because the Envoy is launched
with the listen coordinates frozen into that same blueprint and its command is
one of the fixed built-in entries. Letting an application move the container
port without rewriting that command would leave the Envoy listening on the
default while Reploy forwarded to the override, and the handshake would never
connect. The two therefore compose after the application, under the same rule
that reserves any entry an override can never legitimately change.

The application config may override normal Reploy fields, including identity,
command, ports, mounts, packages, and endpoints — every one of them except the
Envoy-reserved entries named above, which compose after it. The built-in
defaults are meant to keep ordinary users from needing to know Envoy
coordinates, while the final values remain visible in the retained blueprint.

Validation is layered by ownership. Reploy owns the blueprint schema and is
authoritative for validating it and for its runtime checks; OmegaFlow does not
duplicate that schema to validate earlier, and surfaces Reploy's errors with
project and recording context. The structural dataclass schema OmegaFlow
currently hosts is a temporary home for that generic Reploy structure and
carries no OmegaFlow-specific fields or checks. Hydra owns structural type
checking of the composition.

Envoy requirements are OmegaFlow concepts that Reploy cannot know, so OmegaFlow
validates them itself against the composed blueprint before materialization: a
Bash package and executable, a non-root identity, the read-only executable
`/omegaflow-runtime` mount, and both `omegaflow-terminal` and
`omegaflow-telemetry` endpoints bound privately. A violation fails the recording
with a specific error naming the unmet requirement; OmegaFlow does not repair
the blueprint. Where an override is never legitimate, the Envoy-reserved entry
composes after the application instead, so it cannot be overridden at all.

Hydra's normal mapping and list behavior applies. Envoy additions use reserved
mapping entries and do not modify an application's existing package lists.
Lists are not assumed to concatenate implicitly; an application override owns
the resulting list.

Conceptually, recording configuration selects the application as follows:

```yaml
defaults:
  - reploy/app: demo
```

Hydra composition, explicit overrides, and `${...}` interpolation produce both
final blueprint dataclasses. Reploy's `{{ ... }}` expressions remain ordinary
strings and are preserved for Reploy's later resolution. OmegaFlow does not
merge, inject, repair, or reinterpret either blueprint after Hydra returns the
final configuration.

### Per-run materialization and evidence

Every recording serializes both resolved dataclasses as native Reploy YAML and
retains them in its private Hydra run directory:

```text
<run>/reploy/blueprints/controller.blueprint.yaml
<run>/reploy/blueprints/workload.blueprint.yaml
<run>/reploy/deployments/controller/
<run>/reploy/deployments/workload/
<run>/reploy/controller-output/
```

The recording uses fresh prepared deployment directories. Reploy's normal
provider and image caches supply reuse across runs. Blueprint composition is
not cached separately. The controller output directory is a distinct sibling,
starts empty when `reploy controlled-session run` begins, and never contains
host input.

The runtime and controller-input bind sources use run-directory interpolation,
for example `${omegaflow_run_dir:}/reploy/input/runtime` and
`${omegaflow_run_dir:}/reploy/input/controller`. Their container targets remain
`/omegaflow-runtime` and `/omegaflow-input`. Exact resolved YAML and digests are
private run evidence and are not published by default.

The first conformance workload uses an internal OmegaFlow demo or tutorial with
audio and browser actions disabled, terminal-only ordering, and writable shared
state. It requires no external service, credential, application endpoint, or
general project-discovery design. Browser endpoint and handoff conformance
follows only after the terminal-only gate.

The first implementation follows Reploy v1's controlled-session limitations:
Linux hosts using Docker, Linux `amd64` or `arm64` controller images, one
attachment per session, no reconnect, and no controller or workload deployment
with a configured private environment. Unsupported selections fail before
capture with a targeted capability error. Secret-dependent application,
narration, or publishing operations remain outside this slice until an approved
secret-delegation design exists.

## Project and Recording Directory Layout

Bootstrap has one project root and one recording directory:

```text
/src/arbiter/
├── .omegaflow/
│   └── config.yaml
├── go.mod
├── pyproject.toml
└── project source

/src/arbiter-recordings/
├── config.yaml
├── reploy/
│   └── app/
│       └── arbiter.yaml
├── .omegaflow/
│   └── generated/
└── <recording-id>/
    └── index.md
```

The recording directory may instead be `/src/arbiter/recordings`, which
remains the default.

`omegaflow bootstrap=project` is run from `/src/arbiter`. There is no separate
recording root and no project-root override. The current working directory is
the project root; bootstrap should validate this precondition when repository
metadata makes a mismatch detectable.

The optional recording-directory override is resolved from the project root.
Bootstrap always creates `.omegaflow` under the project root and writes the
recording-directory link there:

```yaml
studio:
  recording_dir: ../arbiter-recordings
  data_dir: ../arbiter-recordings/.omegaflow
```

Bootstrap should prefer a general relative path from the project root,
including `../` segments for sibling directories. It should not fall back to
an absolute path merely because the recording directory is outside the project
root. Absolute paths remain valid when a meaningful relative representation is
not practical.

OmegaFlow commands continue to run in the project context. The external
recording directory is storage and configuration, not an independent project
identity.

## Bootstrap

Full project bootstrap performs the following environment work in addition to
the existing OmegaFlow scaffolding:

1. Treat the current working directory as the project root.
2. Resolve the recording directory, defaulting to `recordings`.
3. Create `.omegaflow` under the project root and record the recording
   directory relative to it.
4. Inspect declarative project metadata without executing project code.
5. Identify every supported project facet.
6. Create a conservative initial application blueprint in the `reploy/app`
   config group.
7. Validate the materialized candidate through Reploy.
8. Report detected facets, added dependencies, and unresolved findings.

The initial blueprint starts from a general-purpose Debian or Ubuntu
environment. The exact distribution, release, and image size are open. The
default should favor a predictable terminal and broad package availability
over minimizing image size before measurements justify a slimmer choice.

Bootstrap is allowed to provide a useful environment for an unrecognized
project: the general base plus access to the project source. Reliable metadata
can enrich that baseline, but failed or incomplete detection must not prevent a
user from editing the native Reploy blueprint manually.

Automatic detection happens during bootstrap or an explicit refresh. Ordinary
builds do not rescan the project and silently change the environment.

## Project Discovery

Project discovery produces an additive set of facets rather than assigning one
exclusive project type. Candidate declarative signals include:

- `pyproject.toml`, supported Python lock files, and requirements files
- `go.mod` and `go.sum`
- `package.json` and supported JavaScript lock files
- `Cargo.toml` and `Cargo.lock`
- `Makefile`, `justfile`, and other supported task metadata
- container and service metadata that has a defined, safe interpretation

Discovery must not:

- import or execute project code;
- run arbitrary build-system hooks;
- infer an OS package solely from a missing command name;
- treat a heuristic as proof of the complete project environment; or
- discard one facet because another was detected first.

For example, Arbiter contains both Go and Python. Bootstrap should retain both
findings and produce one environment capable of supporting the relevant Go and
Python workflows. Language facets do not automatically become separate Reploy
applications. Application boundaries follow logical package and operation
ownership, while a single application may use several package ecosystems.

The initial Reploy integration can only express providers Reploy currently
supports. A Go facet may initially influence the base image or OS packages
without providing a first-class managed Go source build. OmegaFlow must report
that limitation instead of claiming equivalent provider behavior.

Manual dependencies and automatically proposed dependencies use the same
native Reploy fields. OmegaFlow does not retain a parallel dependency list as a
second source of truth.

## Blueprint Refresh

Project discovery must be repeatable without rerunning full bootstrap. A
dedicated blueprint refresh operation will:

1. inspect the current project root;
2. detect all currently supported facets;
3. generate a complete candidate application blueprint;
4. validate the candidate through Reploy;
5. compare it with the selected, version-controlled application blueprint; and
6. report the proposed change.

Refresh does not recreate recording scripts, secrets, ignore files, or other
bootstrap output. It does not build the Reploy environment unless the user
requests a separate build operation.

The version-controlled application blueprint is user-owned. Refresh never
attempts a semantic merge and never overwrites it by default. It writes the
candidate outside the selectable config group, provisionally beneath:

```text
project-root/.omegaflow/generated/reploy/app/<name>.yaml
```

and presents a diff. The user manually merges any desired changes into the
config-group entry. This avoids hidden ownership metadata and ambiguous list
merge behavior.

Refresh must be deterministic and idempotent: unchanged project metadata and
the same OmegaFlow discovery version produce the same candidate bytes.

The final command spelling is open. `blueprint=refresh` is descriptive but not
yet a public CLI decision.

## Source, State, and Artifact Boundaries

The recorded application environment requires explicit boundaries for:

- project source input;
- the disposable writable working copy used during recording;
- persistent or reusable caches;
- declared environment variables and scoped secrets;
- workload endpoints;
- private capture artifacts;
- logs and failure metadata; and
- published output.

The project root identifies the source project, but it does not imply that the
host checkout should be mounted writable. The bounded prototype must choose and
validate the source snapshot or mount contract. A safe default is a read-only
source input plus a disposable writable working copy.

Each controlled session uses a fresh private host run directory as Reploy's
controller `--output-dir`. Reploy exposes it only to the controller, through
`REPLOY_OUTPUT_DIR`, and retains it across successful and failed teardown. The
output directory starts empty and is never used for controller input. Host
OmegaFlow supplies the bounded `omegaflow-controller-run-v1` manifest through a
separate read-only, controller-only prepared-deployment mount at
`/omegaflow-input/run-manifest.json`. `/omegaflow-input` and
`REPLOY_OUTPUT_DIR` are distinct and non-overlapping; the manifest does not use
the workload filesystem or a Reploy-private socket.

The controller writes the private raw terminal-byte log, casts, action
timelines, browser media, narration audio and timestamps, logs, and OmegaFlow
failure metadata into that directory. It closes those capture files and
completes all required media processing before sending `complete`. After
`complete`, it writes the authoritative decoded
`terminated` result as separate session evidence before sending
`acknowledge-terminated`. When pre-completion finalization fails, it still
closes and retains partial artifacts, records the failure, and follows the
Reploy completion and acknowledgement lifecycle when the broker remains
available.

Only declared artifacts cross from the private run into published output. The
controller builds the complete publication candidate and runs media validation,
including the current `ffprobe` checks, before `complete`. The candidate carries
a versioned manifest, file hashes, and validated media metadata. Publishing
remains a host OmegaFlow operation after Reploy returns, but its path performs
only path-containment, allowlist, manifest-schema, hash, and bounded-copy checks;
it does not invoke `ffmpeg`, `ffprobe`, or another capture or media executable.
The implementation must split the current `publish_bundle()` media-validation
work from this host-only verification and copy path. Reploy's complete host
result is retained alongside the private run even when publication is not
attempted.

## Failure Behavior

Failures should name the responsible layer:

- Hydra composition and OmegaConf interpolation failures are OmegaFlow
  configuration errors.
- Missing or malformed typed `reploy.controller` or `reploy.workload` objects
  are OmegaFlow configuration errors.
- Blueprint, provider, platform, package, image, mount, and Reploy runtime
  failures retain Reploy's diagnostics.
- Recording-plan, action, expectation, media, narration, and publishing
  failures retain OmegaFlow's diagnostics.
- Controller-client schema, lifecycle, terminal-attachment, output-drain,
  acknowledgement, and cleanup failures retain the corresponding Reploy event
  or host-result field together with OmegaFlow operation context.

OmegaFlow should add project, recording, and selected-blueprint context without
rewriting a precise Reploy error into a generic environment failure.

Host OmegaFlow treats `reploy-controlled-session-run-result-v1` as the
authoritative top-level outcome after valid invocation. Success requires its
`ok` field, successful result delivery and acknowledgement, successful cleanup,
no recovery action, successful controller output retention, and an acceptable
workload/controller result. A zero controller, attachment, or host process exit
code never overrides a failing structured field.

Controller OmegaFlow stores every well-formed `diagnostic`, `client-error`, and
`terminated` event. Unknown well-formed diagnostic codes remain generic
diagnostics rather than schema errors. It also preserves controller-client
stderr separately from terminal content. Host OmegaFlow captures and retains
the host Reploy process's stderr alongside the private run. Abrupt host death
may leave only retained partial artifacts, captured host stderr, and a
Reploy-private incident receipt; OmegaFlow reports the absent host result
without manufacturing successful completion.

A failed bootstrap or refresh must not leave a partially written canonical
application blueprint. A failed refresh may retain its generated candidate and
diagnostics for inspection.

## Security

1. The host Reploy process, not the toolchain container, owns Docker control.
2. OmegaFlow uses only Reploy's public host command, controller JSONL client,
   byte-only attachment, endpoint, and output contracts.
3. Terminal content is untrusted workload data and cannot be interpreted as a
   Reploy lifecycle or Envoy telemetry event. Terminal and telemetry bytes use
   separate channels.
4. Endpoint IDs are selected before admission. Browser navigation cannot
   replace the granted lease-local host with an arbitrary host from workload
   output.
5. Project discovery reads supported declarative metadata and does not execute
   project code.
6. Generated blueprints and the controller run manifest contain no secret
   values.
7. Secret delegation remains operation-scoped and must not enter published
   artifacts.
8. Source, cache, output, and runtime mounts are explicit and validated.
9. The recorded application environment does not inherit arbitrary host
   environment variables or host paths.
10. Failure artifacts are private until OmegaFlow's publishing allowlist accepts
   them.

Reploy's initial endpoint network grants coarse controller-to-workload network
reachability rather than destination-port isolation. OmegaFlow must not expose
sensitive controller listeners on that network or claim that selecting an
endpoint limits the controller to that port.

## Delivery Plan

The former seven-slice plan overlapped the Envoy plan and grouped public
contracts, lifecycle, terminal semantics, browser work, publication,
orchestration, bootstrap, and migration into oversized review units. It is
superseded by the temporary
[Reploy Integration Implementation Plan](reploy-integration-implementation-plan.md).

The rebuilt delivery order is:

1. amend and re-review the contracts and plan;
2. complete local Envoy/Awsh conformance;
3. deliver controller, Reploy, runtime, blueprint, and terminal-runner
   boundaries as independent slices;
4. prove a terminal-only isolated Reploy recording; and
5. plan browser, publication, host-workload parity, FIFO retirement, bootstrap,
   and refresh as later stacks.

The validation and product decisions below remain authoritative. The temporary
plan owns implementation sequencing and current evidence.

## Validation Plan

Validation proves boundaries and failure behavior before broad language or
multi-pane coverage. Each item is tagged with the delivery phase that owns it;
later items are not gates for the terminal-only milestone:

1. **Terminal-only:** Validate the controller stream and host result decoders
   against every public Reploy v1 golden fixture.
2. **Terminal-only:** Exercise fragmented terminal and telemetry bytes,
   fragmented and invalid UTF-8 at cast boundaries, terminal text that resembles
   protocol content,
   exact private raw-output retention, `real`/`suppress`/`replace` publication,
   incremental realtime output, buffered stdout-then-stderr presentation output
   after the logical post-enter pause, and compressed command wall time,
   exclusive checked ranges supporting interactive input whose kernel echo is
   excluded by `echo`-marked spans, without accepting output from earlier
   supervised PTY writers,
   fail-closed cleanup including disowned descendants, resize, Ctrl-C, command
   completion, cwd, newline-sensitive split-stream assertions, exact
   post-line-discipline PTY assertion semantics across terminal CRLF processing,
   split-stream `stdout + stderr` compatibility under interleaved presentation,
   planned finalization of an asserted open operation, typed workload-side file
   and produced-output inspection, action gates, and ordered output ranges.
3. **Terminal-only:** Prove startup failure, controller cancellation, workload
   exit, terminal output-finalization failure, controller artifact failure,
   result-delivery failure, acknowledgement failure, cleanup failure, and
   recovery-action reporting.
4. **Browser stack:** Run Reploy's OmegaFlow-shaped conformance test and an
   OmegaFlow-owned real terminal-and-browser integration test on Linux `amd64`;
   retain an `arm64` contract and smoke path.
5. **Terminal-only and later end-to-end stacks:** Repeat each nominal end-to-end
   test enough to detect attachment and teardown races rather than accepting a
   single successful run.
6. **Browser stack:** Verify that endpoint IDs resolve only through the trusted
   `opened` event and that undeclared or substituted browser destinations fail
   closed; prove a blocking workload reaches controller-observed endpoint
   readiness without a workload file, OSC marker, or browser-specific telemetry
   message; and prove operation completion or failure beats a stale endpoint
   readiness success.
7. **Publication stack:** Preserve the complete private run, partial failure
   artifacts, controller session result, host result, diagnostics, and published
   bundle.
8. **Terminal-only:** Compose typed `reploy.controller` and `reploy.workload`
   objects, verify that the controller is read-only, OmegaConf interpolation
   resolves, Reploy interpolation remains intact, and exact native YAML is
   retained.
9. **Bootstrap stack:** Bootstrap both an in-project recording directory and a
   sibling recording directory; detect a synthetic Go/Python project without
   executing its code.
10. **Bootstrap stack:** Refresh discovery deterministically, produce a
    candidate and diff, and leave the canonical blueprint unchanged.
11. **Packaging stack:** Verify that installing OmegaFlow installs Reploy and
    that a supported container runtime is the only external host dependency;
    the host does not require individual capture or media tools.

## Decisions

1. Reploy is the required recording-toolchain substrate. A supported container
   runtime is required for recording, including in CI; a bare-metal controller
   is deferred unless a concrete need justifies reintroducing it.
2. Recording-toolchain placement and workload placement are distinct. The
   controller always runs in Reploy; the workload defaults to the host and may
   be explicitly isolated in Reploy.
3. OmegaFlow owns the stable controller structured config, and the resolved
   controller blueprint is visible but read-only.
4. Project-owned application structured configs compose through `reploy/app`
   into the final workload blueprint after OmegaFlow's Envoy defaults.
5. Hydra produces complete typed controller and workload blueprints without
   application-side mutation; Reploy remains authoritative for Reploy semantics
   and environment realization.
6. Full bootstrap runs from the project root.
7. `.omegaflow` always belongs to the project root.
8. The recording directory may be outside the project root and is linked
   preferably with a relative path.
9. Project discovery is additive and supports multi-faceted projects.
10. Automatic discovery occurs only during bootstrap or explicit refresh.
11. Refresh creates a candidate and diff; users merge changes manually.
12. Reploy's public controlled-session v1 host command, controller client,
    terminal attachment, endpoint, output, and diagnostic surfaces are the only
    Reploy integration boundary.
13. Reploy owns the bootstrap PTY and lifecycle truth. The OmegaFlow Envoy owns
    the recording PTY and application-level terminal/telemetry transport;
    controller OmegaFlow owns action coordination, browser behavior, recording
    policy, redaction, media, and publishing.
14. Recordings use a controller output directory, finalize controller-produced
    capture artifacts before sending `complete`, retain `terminated` before
    acknowledgement, and retain the host result after the host command returns.
15. The first terminal-only milestone uses one Reploy bootstrap attachment and
    supports one Envoy-owned persistent terminal pane without browser capture.
    Browser capture and shared-state multi-terminal-pane support are deferred to
    separately approved stacks.
16. OmegaFlow declares a compatible Reploy Python dependency and normal package
    resolution installs it; OmegaFlow does not vendor Reploy. A supported
    container runtime is the only normal external host dependency; Reploy
    controlled-session v1 currently uses Docker.
17. `studio.recording_backend` is a typed `reploy` or `host` enum and defaults
    to `reploy`. `host` is reserved and produces a capability error until a
    bare-metal recording toolchain is deliberately introduced.
18. `studio.workload_backend` is a typed `host` or `reploy` enum. `host` is the
    default. Explicit `reploy` selection requires a complete workload blueprint
    and never falls back after configuration or execution failure.
19. The current FIFO runner is migration scaffolding, not a second recording
    toolchain. Host workloads move to the packaged Envoy and `awsh` and use the
    same Reploy controller as isolated workloads.

## Open Questions

1. Which Debian or Ubuntu image and release should bootstrap select by default?
2. What are the final CLI names for the recording-directory override and
   blueprint refresh operation?
3. How is project source transferred or mounted, and when is a writable copy
   created?
4. Which caches are shared across recordings, and what inputs define safe
   reuse?
5. Which project detectors are included in the first release?
6. How should toolchain selection work for a facet that lacks a first-class
   Reploy provider?
7. What public Reploy endpoint contract should give its controller bounded,
   portable access to a host-running Envoy and declared host application
   services across supported Docker and Podman runtimes?
8. What topology will eventually support multiple terminal panes that share
   one recorded application state without weakening the persistent-shell and
   output-ordering contracts?
9. Which secret-dependent narration, application, and publishing operations
   remain host-side or deferred until controlled sessions support an approved
   secret-delegation boundary?
