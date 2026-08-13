# Reploy Recording Environments Design

## Status

- Approved environment direction; terminal implementation delegated to the
  Envoy design
- Updated: 2026-08-16
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
model. Reploy's public controlled-session v1 boundary is now fixed and has
OmegaFlow-shaped conformance coverage. The initial distribution image,
OmegaFlow-facing configuration and CLI spelling, source materialization, and
migration cutover remain open.

## Summary

Reploy becomes OmegaFlow's standard execution substrate and a Python dependency
of OmegaFlow. Docker is Reploy's initial runtime and the only normal external
host dependency. A user should not need to install Reploy separately or install
asciinema, Playwright Chromium, ffmpeg, ffprobe, codecs, or the other recording
and media-processing tools directly on the host.

OmegaFlow nevertheless retains host recording for now. Recordings that exercise
Reploy itself, including Reploy-backed Arbiter flows, require it until a
complete Reploy-nesting model is available. Backend selection UX remains to be
designed. A future host backend may reuse the Envoy and `awsh` contracts, and a
future Reploy-managed controller may connect to a thin host Envoy so capture
dependencies remain off the host; neither topology is specified here.

The design separates two environments:

1. The **OmegaFlow toolchain environment** contains OmegaFlow and its capture,
   processing, narration, and publishing dependencies. OmegaFlow owns this
   stable internal Reploy blueprint.
2. The **recorded application environment** contains the project, tools,
   packages, services, and state that appear in the recording. The project owns
   this Reploy blueprint as a Hydra config-group entry.

OmegaFlow composes an application blueprint with Hydra, resolves OmegaConf
interpolations, materializes the resulting YAML, and hands it to Reploy.
Reploy remains authoritative for blueprint validation, Reploy interpolation,
package resolution, environment construction, and execution.

For a recording, host OmegaFlow prepares distinct toolchain-controller and
recorded-workload deployments, then invokes the public
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

1. Make Reploy a Python dependency of OmegaFlow so Docker is the only normal
   external host dependency.
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

## Non-goals

1. A second OmegaFlow-specific package or environment schema.
2. Perfect dependency inference from arbitrary source code.
3. Executing project code during bootstrap to discover dependencies.
4. Silently updating an application blueprint during an ordinary build.
5. Maintaining permanently divergent recording-plan, artifact, or diagnostic
   semantics between the retained host and Reploy execution paths.
6. Giving the OmegaFlow toolchain environment broad Docker-socket access.
7. Designing remote execution, portable environment transfer, or a general
   multi-service orchestrator in this phase.
8. Adding missing Reploy package providers as part of the initial OmegaFlow
   integration.
9. Depending on Reploy's private framed session protocol or Docker
   implementation details.
10. Solving shared-state multi-terminal-pane recording in the first
    controlled-session integration. The bounded first implementation supports
    one Envoy-owned persistent terminal pane plus browser capture.

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

**Recorded application environment**
: The Reploy environment whose shell, files, tools, services, and browser
  application are demonstrated. During a controlled session it is the workload
  deployment.

**Application blueprint**
: A project-owned Reploy blueprint selected through a Hydra config group for
  use as the recorded application environment.

**Host OmegaFlow**
: The ordinary OmegaFlow CLI installed on the host. It composes blueprints,
  prepares deployments, invokes host Reploy, consumes the final host result,
  and publishes retained artifacts. It does not execute recording actions.

**Controller OmegaFlow**
: The trusted internal controller command inside the toolchain deployment. It
  drives the Reploy session client, terminal recorder, browser runner, action
  coordination, artifact finalization, and acknowledgement lifecycle.

## Architecture

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
Dynamic workload-selected navigation remains deferred.

## Reploy as the Standard Execution Substrate

Reploy is required for operations that execute a recording or require the
managed media toolchain in the standard isolated path. Pure authoring and
source-inspection operations may remain direct CLI operations.

The current native workflow remains available for host recordings, especially
recordings that exercise Reploy or Reploy-backed tools. Reploy is still the
target standard for ordinary isolated recordings. Backend naming, defaults,
and the eventual host implementation require separate design; availability of
the standard Reploy path alone is not a host-removal criterion.

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

The blueprint is logically hard-coded and not selected through the project's
Hydra config. It should be stored as readable packaged data rather than as a
large Python string. OmegaFlow versions and tests it with the release that
uses it.

The blueprint declares a non-root runtime and one native internal controller
command. That command is an implementation surface between host and controller
OmegaFlow, not a user-facing recording backend. Controller OmegaFlow writes
the exact Envoy terminal bytes to a private raw log and derives the asciicast
without a second PTY or an `asciinema record` process. Before it requests
execution, it adds provenance-marked prompt and displayed-command events from
the recording plan; those presentation events are ordered ahead of workload
output but are not added to the raw workload-byte log. Reploy separately
injects its release-matched public client at session preparation time;
OmegaFlow must not package its own copy of `reploy-session-client`. The bundled
asciinema executable remains available to the retained host recording path
until an approved host-Envoy migration replaces it.

The command's output mode is fixed before execution. `real` output is decoded
into the cast as it arrives. Raw output under `suppress` and `replace` remains
only in the private bounded range; replacement text is a
controller-presentation event committed after the operation's output-through
barrier.

The raw range is temporal PTY evidence, not foreground-process provenance.
Output assertions therefore require exclusive observation: no surviving PTY
writer from an earlier operation and a fresh output-through drain barrier
before execution. Authored controller input remains supported only while the
Envoy proves from slave termios that kernel echo, including newline echo, is
disabled; unknown or enabled echo fails closed before the input write.
Failure cancellation and user cancellation invalidate the assertions. Planned
recording-end finalization is a distinct typed lifetime result: it terminates
and drains the intentionally open operation, closes the observed range, and
then evaluates authored non-exit assertions over that complete range. The
synthetic termination status is not treated as a natural exit code. `suppress`,
`replace`, and checked `real` operations always use exclusive observation. The
planned operation tree is expected to be terminated by this lifetime policy;
only a writer that survives the final drain is a writer-cleanup failure.

Checked output preserves the current `stdout + stderr` assertion view rather
than treating temporal presentation order as equivalent. Non-interactive
split-stream operations retain bounded logical stdout and stderr evidence
separately while the Envoy forwards both streams to the terminal channel in
observed order. Interactive operations intentionally attach both streams to the
single slave PTY, as the current realtime runner does, so their logical stdout
is the inverse-transformed PTY range and logical stderr is empty. Stream identity
is never reconstructed from merged PTY bytes.

For PTY-attached checked output, the Envoy records bounded slave output-termios
segments and the controller reverses supported lossless mappings such as
standard `ONLCR` before evaluating `output_contains` and `output_regex`.
Split-stream evidence does not traverse the terminal line discipline.
Unsupported, lossy, or untracked transformations fail the checked PTY
operation. The raw terminal log and published cast retain their exact bytes and
are never normalized.

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
the resolved plan to the Envoy over the private driver channel. The Envoy checks
existence and kind and computes deterministic file or directory SHA-256 values,
then returns typed inspection or produced-output records before the terminal
result. Controller OmegaFlow records those records; it does not run hidden probe
commands, access workload paths, or parse PTY output for path or hash fields.

Secrets such as narration credentials are not embedded in the blueprint.
OmegaFlow continues to scope them to the operations that declare them and to
exclude them from published artifacts and diagnostics.

The existing local requirements are inventoried in
[Runtime dependencies](../runtime-dependencies.md). That inventory becomes
input to the toolchain blueprint rather than end-user installation
documentation once the Reploy path is ready.

## Application Blueprints as Hydra Configs

Application blueprints use a Hydra config group, provisionally
`reploy/app`. A project can define one blueprint shared by all recordings or
several blueprints for distinct recorded applications or environment variants.

An entry is a native Reploy document packaged under a dedicated OmegaFlow
configuration node:

```yaml
# recording-dir/reploy/app/arbiter.yaml
# @package reploy_blueprint

blueprint:
  schema: 1
  version: 0.1.0
  requires_reploy: ">=<supported-version>"
  compatibility:
    platforms: [linux/amd64, linux/arm64]

environment:
  id: arbiter-recording
  base:
    image: ubuntu:24.04
  applications:
    arbiter:
      packages:
        os:
          - git
          - make
        python:
          requirements:
            - pytest

docker: {}
```

The placeholder version and base image above are illustrative, not decisions.
The blueprint contents must follow the Reploy schema supported by the OmegaFlow
release. The selected environment must contain Bash for OmegaFlow's terminal
adapter. During preparation, OmegaFlow adds the read-only
`/omegaflow-runtime` mount and the two private Envoy endpoints without making
those implementation details part of the project dependency schema. Browser
recordings declare named workload endpoints; OmegaFlow passes only the endpoint
IDs required by the recording to `reploy controlled-session run`.

The first implementation also follows Reploy v1's controlled-session
limitations: Linux hosts using Docker, Linux `amd64` or `arm64` controller
images, one attachment per session, no reconnect, and no controller or workload
deployment with a configured private environment. Unsupported selections fail
before capture with a targeted capability error.

Because the initial controlled-session contract rejects private environments,
the first implementation cannot silently move secret-dependent application,
narration, or publishing operations into either deployment. Those operations
remain outside the controlled-session slice or fail with a targeted capability
error until an approved secret-delegation design exists.

A project or recording selects the config-group entry through Hydra. The exact
selection surface remains to be settled; conceptually it is:

```yaml
defaults:
  - reploy/app: arbiter
```

OmegaFlow then:

1. composes the selected Hydra configuration;
2. resolves OmegaConf `${...}` interpolation;
3. extracts the `reploy_blueprint` document;
4. writes a generated YAML document for the run; and
5. passes that document to Reploy.

OmegaFlow must preserve Reploy's `{{ ... }}` expressions. Reploy owns their
late resolution together with schema validation, base-image and platform
resolution, package locking, backend rendering, and execution.

OmegaFlow should not duplicate the complete Reploy schema merely to provide
earlier validation. It may check that a blueprint was selected and materialized
as a mapping, then surface Reploy's authoritative validation errors with
project and recording context.

Hydra list behavior must remain visible. Lists such as OS packages and Python
requirements are not assumed to concatenate implicitly across configuration
layers. A user who overrides such a list owns the composed value.

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
host-to-controller run-manifest mechanism must use a documented controller
input owned by OmegaFlow; it must not depend on the workload filesystem or a
Reploy-private socket. Selecting whether that manifest is staged with the
controller deployment, passed through bounded command arguments, or placed in
the fresh controller directory remains an implementation decision that the
first adapter slice must settle and test.

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
- Missing or malformed `reploy_blueprint` selection is an OmegaFlow
  configuration error.
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

## Implementation Plan

Implementation is divided into focused slices. Each slice keeps host recording
working while Reploy nesting and backend UX remain unresolved. The two paths
must share recording-plan, artifact, and diagnostic semantics rather than
become divergent capture models.

### 1. Public contract models

- Add typed decoders and encoders for
  `reploy-controlled-session-client-v1` events and requests.
- Add a typed decoder for `reploy-controlled-session-run-result-v1`.
- Test against Reploy's public golden fixtures, including unknown diagnostic
  codes, malformed messages, nullable host results, and unsuccessful result
  fields.
- Add Reploy as an OmegaFlow Python dependency and define the supported version
  floor once the containing Reploy release is published.

Completion gate: OmegaFlow can validate every public fixture without importing
Reploy internals or opening a private session socket.

### 2. Controller lifecycle driver

- Add an internal controller command to start `reploy-session-client client`,
  enforce lifecycle ordering, and retain structured events and stderr.
- Start exactly one attachment immediately after `broker-ready` and before its
  fixed deadline.
- Implement cancellation, startup-failure handling, output-finalization
  handling, `complete`, `terminated`, and `acknowledge-terminated`.
- Settle and test the bounded host-to-controller run-manifest mechanism.

Completion gate: deterministic fake-client tests cover success, startup
failure, cancellation, malformed input, output-finalization failure, and
acknowledgement failure.

### 3. Reploy-backed Envoy terminal adapter

- Follow slices 1 through 5 of the Envoy design for its protocols, Bash adapter,
  PTY/network supervisor, runtime mount, controller lifecycle, and
  `PersistentTerminalRunner` boundary.
- Preserve expectation checks, replacement output, produced-output metadata,
  resize, Ctrl-C, typing timing, continuation, checks, setup, cleanup, action
  gates, output ranges, and separate failure diagnostics without terminal
  markers, controller-local FIFOs, or workload-visible controller files.
- Carry bounded `file_exists` and `produces` specifications with the operation,
  resolve paths in persistent Bash state, inspect and hash them in the Envoy,
  and consume only typed workload-side results in the controller.
- Retain the exact private terminal-output byte log and write the asciicast and
  terminal timeline by merging provenance-marked controller prompt/display
  events with Envoy terminal and telemetry events, without introducing another
  PTY. Commit the display events before requesting execution and wait for the
  output-through barrier before displaying a following prompt.
- Cover `real`, `suppress`, and `replace` explicitly: suppressed raw bytes never
  enter published presentation, and replacement output is emitted only after
  the matching output-through barrier.
- Evaluate output assertions against the inverse-transformed logical view from
  the Envoy's bounded output-termios ledger, while retaining exact raw and cast
  bytes and rejecting lossy or untracked transformations.
- Preserve the existing assertion input contract by retaining split-stream
  logical stdout/stderr evidence for non-interactive operations, treating
  merged interactive PTY output as logical stdout with empty stderr, and
  evaluating against logical `stdout + stderr` rather than temporal
  presentation order.
- Carry exclusive-versus-shared observation through the execution boundary.
  Output assertions require exclusive observation, accept controller input only
  with Envoy-observed terminal echo disabled, and start only after an empty
  supervised-PTY-writer/output-drain barrier.
- Terminate, drain, and fail surviving writers left by `suppress`, `replace`, or
  checked `real` operations, including disowned and daemonized descendants.
  Supervised writers are allowed only for unchecked `real` operations, whose
  shared ranges cannot satisfy later assertions.
- Treat planned recording-end finalization as distinct from failure/user
  cancellation: drain the closed operation range and evaluate its non-exit
  assertions without applying an exit-code assertion to the synthetic
  termination status.

Completion gate: the Envoy design's protocol, Bash, local integration, runtime
mount, and single-pane terminal gates all pass.

### 4. Browser endpoint adapter

- Resolve selected endpoint IDs from the trusted `opened` event.
- Keep readiness conditions and path/navigation intent in the compiled
  controller plan; workload output cannot replace them.
- For a long-running terminal operation, wait for `operation_started`, probe
  the plan-selected granted endpoint from the controller while racing the typed
  operation result, run the planned browser actions only when readiness wins
  while the operation remains active, and apply the operation's compiled
  lifetime policy through normal Envoy cancellation and output-finalization.
- Drive the existing persistent browser runner inside the controller without a
  cross-container file handoff, OSC marker, terminal parsing, or
  browser-specific Envoy telemetry.
- Preserve action joins, progress gates, screenshots, pointer state, and
  terminal/browser ordering.

Completion gate: one persistent terminal pane and one browser pane complete a
real ordered handoff through a granted endpoint; undeclared or substituted
hosts fail closed.

### 5. Artifact and diagnostic integration

- Place the complete private run under `REPLOY_OUTPUT_DIR`.
- Finalize casts, extracted beats, browser media, narration, diagnostics, and
  rendered output before `complete`.
- Build and media-validate a versioned publication candidate before `complete`,
  retaining its manifest, hashes, validated media metadata, and validation
  version for the host publisher.
- Retain the controller `terminated` event, host result, client stderr, and
  OmegaFlow failure metadata as separate evidence.
- Map structured failures into `CaptureFailed` without discarding secondary
  cleanup or publication failures.

Completion gate: nominal capture publishes successfully; forced recorder,
output-drain, media, controller, and cleanup failures retain the expected
partial artifacts and structured causes.

### 6. Host orchestration and blueprints

- Package and version the internal non-root toolchain-controller blueprint.
- Compose, materialize, stage, and build the selected application blueprint.
- Prepare distinct controller and workload deployment directories and invoke
  `reploy controlled-session run` with only required endpoint grants.
- Parse the exact host stdout object and preserve host Reploy stderr.
- Split `publish_bundle()` so the host path verifies the controller-produced
  manifest, allowlist, path containment, and hashes and copies bounded files
  without calling `require_browser_media_runtime()`, `ffmpeg`, or `ffprobe`.

Completion gate: a clean host with OmegaFlow, its Python dependencies, and
Docker can run a terminal-and-browser recording without locally installed
capture or media tools.

### 7. Bootstrap, refresh, and migration

- Add application-blueprint bootstrap and deterministic refresh.
- Add capability errors for unsupported hosts, private environments,
  secret-dependent session operations, multi-terminal-pane recordings, missing
  Bash, and unavailable Reploy features.
- Run representative recordings through both paths, make Reploy standard for
  ordinary isolated recordings, and retain host recording until Reploy nesting
  and a separately approved backend migration make replacement possible.

Completion gate: documentation, bootstrap, diagnostics, and release packaging
describe the standard Reploy execution model and the bounded reason host
recording remains supported.

## Validation Plan

Validation proves boundaries and failure behavior before broad language or
multi-pane coverage:

1. Validate the controller stream and host result decoders against every
   public Reploy v1 golden fixture.
2. Exercise fragmented terminal and telemetry bytes, fragmented and invalid
   UTF-8 at cast boundaries, terminal text that resembles protocol content,
   exact private raw-output retention, `real`/`suppress`/`replace` publication,
   exclusive checked ranges supporting echo-disabled interactive input without
   accepting kernel echo or output from earlier supervised PTY writers,
   fail-closed cleanup including disowned descendants, resize, Ctrl-C, command
   completion, cwd, newline-sensitive logical assertions across terminal CRLF
   processing, split-stream `stdout + stderr` compatibility under interleaved
   presentation, planned finalization of an asserted open operation, typed
   workload-side file and produced-output inspection, action gates, and ordered
   output ranges.
3. Prove startup failure, controller cancellation, workload exit, terminal
   output-finalization failure, controller artifact failure, result-delivery
   failure, acknowledgement failure, cleanup failure, and recovery-action
   reporting.
4. Run Reploy's OmegaFlow-shaped conformance test and an OmegaFlow-owned real
   terminal-and-browser integration test on Linux `amd64`; retain an `arm64`
   contract and smoke path.
5. Repeat the nominal end-to-end test enough to detect attachment and teardown
   races rather than accepting a single successful run.
6. Verify that endpoint IDs resolve only through the trusted `opened` event and
   that undeclared or substituted browser destinations fail closed; prove a
   blocking workload reaches controller-observed endpoint readiness without a
   workload file, OSC marker, or browser-specific telemetry message; and prove
   operation completion or failure beats a stale endpoint readiness success.
7. Preserve the complete private run, partial failure artifacts, controller
   session result, host result, diagnostics, and published bundle.
8. Compose a selected `reploy/app` blueprint through Hydra and verify that
   OmegaConf interpolation resolves while Reploy interpolation remains intact.
9. Bootstrap both an in-project recording directory and a sibling recording
   directory; detect a synthetic Go/Python project without executing its code.
10. Refresh discovery deterministically, produce a candidate and diff, and
    leave the canonical blueprint unchanged.
11. Verify that installing OmegaFlow installs Reploy and that Docker is the
    only external host dependency; the host does not require individual
    capture or media tools.

## Decisions

1. Reploy is the target standard execution substrate, not a permanently
   optional peer backend.
2. The toolchain and recorded application environments are distinct.
3. OmegaFlow owns the stable internal toolchain blueprint.
4. Project-owned application blueprints are native Reploy YAML managed through
   a Hydra config group.
5. OmegaFlow resolves Hydra configuration; Reploy remains authoritative for
   Reploy semantics and environment realization.
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
15. The first implementation uses one Reploy bootstrap attachment and supports
    one Envoy-owned persistent terminal pane plus browser capture. Shared-state
    multi-terminal-pane support is deferred.
16. Reploy is distributed as an OmegaFlow Python dependency; Docker is the only
    normal external host dependency.
17. Host recording remains supported until a complete Reploy-nesting solution
    covers recordings of Reploy and Reploy-backed tools. Its backend UX and any
    migration from the FIFO runner to a host Envoy require separate design and
    approval.

## Open Questions

1. Which Debian or Ubuntu image and release should bootstrap select by default?
2. What are the final config-group package name and blueprint-selection
   surface?
3. What are the final CLI names for the recording-directory override and
   blueprint refresh operation?
4. How does host OmegaFlow pass the bounded per-run recording manifest into the
   prepared controller without creating an undeclared host channel?
5. How is project source transferred or mounted, and when is a writable copy
   created?
6. Which caches are shared across recordings, and what inputs define safe
   reuse?
7. Which project detectors are included in the first release?
8. How should toolchain selection work for a facet that lacks a first-class
   Reploy provider?
9. Which Reploy operation validates a composed blueprint without performing an
   unnecessary build?
10. What nesting capability and migration evidence would allow the native local
    recording path to leave the supported product surface?
11. What topology will eventually support multiple terminal panes that share
    one recorded application state without weakening the persistent-shell and
    output-ordering contracts?
12. Which secret-dependent narration, application, and publishing operations
    remain host-side or deferred until controlled sessions support an approved
    secret-delegation boundary?
