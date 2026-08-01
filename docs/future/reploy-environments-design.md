# Reploy Recording Environments Design

## Status

- Draft for review before implementation
- Date: 2026-07-30
- Scope: Reploy-backed OmegaFlow execution environments, application
  blueprints, and project bootstrap

This document expands the Reploy environment item in
[the backlog](../BACKLOG.md). It records the intended product and configuration
model. Exact CLI spelling, the initial distribution image, and the migration
cutover remain open.

## Summary

Reploy becomes OmegaFlow's standard execution substrate. Docker is Reploy's
initial runtime. A user should not need to install asciinema, Playwright
Chromium, ffmpeg, ffprobe, codecs, or the other recording and media-processing
tools directly on the host.

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

Project bootstrap always runs from the application project root. It creates
`.omegaflow` there and records the location of the recording directory, which
may live outside the project root. Bootstrap inspects declarative project
metadata and creates a conservative initial application blueprint. A separate
refresh operation can repeat discovery and produce a candidate blueprint for
the user to merge manually.

## Goals

1. Reduce normal host requirements to Reploy and Docker.
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

## Non-goals

1. A second OmegaFlow-specific package or environment schema.
2. Perfect dependency inference from arbitrary source code.
3. Executing project code during bootstrap to discover dependencies.
4. Silently updating an application blueprint during an ordinary build.
5. Treating the host and Reploy execution paths as permanently equivalent
   production backends.
6. Giving the OmegaFlow toolchain environment broad Docker-socket access.
7. Designing remote execution, portable environment transfer, or a general
   multi-service orchestrator in this phase.
8. Adding missing Reploy package providers as part of the initial OmegaFlow
   integration.

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
  processing dependencies.

**Recorded application environment**
: The Reploy environment whose shell, files, tools, services, and browser
  application are demonstrated.

**Application blueprint**
: A project-owned Reploy blueprint selected through a Hydra config group for
  use as the recorded application environment.

## Architecture

```text
Host
├── Reploy
├── Docker
└── project root
    ├── .omegaflow/config.yaml
    └── application source

Reploy
├── OmegaFlow toolchain environment
│   ├── OmegaFlow
│   ├── asciinema
│   ├── Playwright and Chromium
│   ├── ffmpeg, ffprobe, and codecs
│   └── narration and publishing tools
└── recorded application environment
    ├── project source or working copy
    ├── project toolchains and packages
    ├── terminal session
    └── demonstrated workload and endpoints
```

The host-side Reploy process owns both environment lifecycles. The toolchain
environment must not receive a general Docker socket merely so that it can
create or control the recorded application environment. OmegaFlow should use a
narrow Reploy-owned control surface for execution, endpoint discovery,
artifacts, diagnostics, and teardown.

Terminal capture occurs at the recorded environment's TTY boundary. Browser
capture may run in the toolchain environment against an endpoint exposed by
the recorded application environment. Media processing can continue after the
recorded application environment has stopped, once the private capture
artifacts are available to the toolchain environment.

The exact controller/session API is deferred until a bounded prototype proves
the required terminal, browser, endpoint, artifact, and cancellation behavior.

## Reploy as the Standard Execution Substrate

Reploy is required for operations that execute a recording or require the
managed media toolchain. Pure authoring and source-inspection operations may
remain direct CLI operations, but they do not define a second recording
backend.

The current native workflow remains available during migration until the
Reploy path covers real recordings and preserves useful diagnostics. The
target architecture does not maintain two equally supported dependency,
isolation, and execution models indefinitely. The cutover and removal schedule
is an implementation and release decision.

## OmegaFlow Toolchain Blueprint

OmegaFlow owns the toolchain blueprint because the dependency set is an
implementation detail of the OmegaFlow release:

- supported Python runtime
- OmegaFlow itself
- asciinema
- Playwright and the supported Chromium build
- ffmpeg and ffprobe
- required WebP and H.264 codec capabilities
- fonts and graphical runtime libraries
- optional narration clients and timing tools

The blueprint is logically hard-coded and not selected through the project's
Hydra config. It should be stored as readable packaged data rather than as a
large Python string. OmegaFlow versions and tests it with the release that
uses it.

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
release.

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

Only declared artifacts cross from the environments to the host. The complete
private run should account for casts, action timelines, browser media,
narration audio and timestamps, logs, failure metadata, and the published
bundle. Published output continues to use OmegaFlow's allowlist and integrity
checks.

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

OmegaFlow should add project, recording, and selected-blueprint context without
rewriting a precise Reploy error into a generic environment failure.

A failed bootstrap or refresh must not leave a partially written canonical
application blueprint. A failed refresh may retain its generated candidate and
diagnostics for inspection.

## Security

1. The host Reploy process, not the toolchain container, owns Docker control.
2. Project discovery reads supported declarative metadata and does not execute
   project code.
3. Generated blueprints contain no secret values.
4. Secret delegation remains operation-scoped and must not enter published
   artifacts.
5. Source, cache, output, and runtime mounts are explicit and validated.
6. The recorded application environment does not inherit arbitrary host
   environment variables or host paths.
7. Failure artifacts are private until OmegaFlow's publishing allowlist accepts
   them.

## Validation Plan

The initial prototype should prove the boundaries rather than broad language
coverage:

1. Bootstrap a project with the default in-project recording directory.
2. Bootstrap a project whose recording directory is a sibling of the project
   root and verify the relative link.
3. Detect a synthetic multi-faceted Go and Python project without executing its
   code.
4. Compose a selected `reploy/app` blueprint through Hydra and verify that
   OmegaConf interpolation resolves while Reploy interpolation remains intact.
5. Validate the materialized blueprint through Reploy.
6. Run one terminal-and-browser recording using the internal toolchain
   environment and the recorded application environment.
7. Preserve the complete private run, failure metadata, and published bundle.
8. Refresh discovery, produce a deterministic candidate and diff, and leave
   the canonical blueprint unchanged.
9. Verify that the host requires Reploy and Docker but not the individual
   capture and media tools.

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

## Open Questions

1. Which Debian or Ubuntu image and release should bootstrap select by default?
2. What are the final config-group package name and blueprint-selection
   surface?
3. What are the final CLI names for the recording-directory override and
   blueprint refresh operation?
4. What narrow Reploy control interface should connect the toolchain and
   recorded application environments?
5. How is project source transferred or mounted, and when is a writable copy
   created?
6. Which caches are shared across recordings, and what inputs define safe
   reuse?
7. Which project detectors are included in the first release?
8. How should toolchain selection work for a facet that lacks a first-class
   Reploy provider?
9. Which Reploy operation validates a composed blueprint without performing an
   unnecessary build?
10. When does the native local recording path leave the supported product
    surface?

