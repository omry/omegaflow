---
sidebar_position: 3
sidebar_label: OmegaFlow Configuration
---

# OmegaFlow Configuration

OmegaFlow configuration controls the `studio` tool itself: where recording files
live, where generated run state is written, which action runs by default, and
how the process loads its `.env` file. It is separate from recording
configuration, which lives in the recording workspace and describes individual
videos.

The bundled OmegaFlow base config is used by default. Projects only need their own
local OmegaFlow config when the tool defaults should change for everyone working in
that project.

## Override Order

OmegaFlow composes tool config in this order:

1. Schema default values.
2. The bundled `base-config.yaml`.
3. `$PWD/.omegaflow/config.yaml`, when it exists.
4. CLI overrides such as `studio action=list studio.recording_dir=demos`.

The local file is optional.

```text
.omegaflow/
  config.yaml
```

## Project Config File

A project-owned OmegaFlow config is a small Hydra config fragment. Put only the
fields your project wants to override:

```yaml
studio:
  recording_dir: demos
  data_dir: demos/.omegaflow
env_file: .env.studio
```

The bundled `base-config.yaml` owns the schema, Hydra logging defaults, Hydra
run directory, and the search path that discovers the local file:

```yaml
defaults:
  - studio_schema
  - _self_
  - optional /.omegaflow@_here_: config
  - override hydra/job_logging: disabled
  - override hydra/hydra_logging: disabled

hydra:
  searchpath:
    - file://.
```

## Common Fields

| Field | Purpose |
| --- | --- |
| `studio.recording_dir` | Directory containing `config.yaml` plus one directory per video. Each video directory contains `omegaflow.md`. |
| `studio.data_dir` | Directory for generated run state, scratch output, caches, and generated artifacts. Defaults to `recordings/.omegaflow`. |
| `studio.keep_output_dir` | Keeps Hydra's output directory metadata when a run is created. |
| `load_env_file` | Enables loading a process-level `.env` file before running actions. |
| `env_file` | Path to the process-level `.env` file, resolved from the project root. |
| `env_override` | Allows values from `env_file` to replace existing environment variables. |
| `workspace` | Bootstrap-only destination for `action=bootstrap`; defaults to `studio.recording_dir`. |

Recording defaults such as capture style, audio generation, publish surfaces,
beats, setup, and cleanup belong in the recording workspace. See
[Recording Configuration](./recording-files/config.md).

## How The Local File Is Found

[Hydra](https://hydra.cc/) can only mark config group options as optional, so
the base config adds the current directory to the search path, treats
`.omegaflow/config.yaml` as the `config` option in the hidden
`.omegaflow` group, and merges it back into the root package with
`@_here_`.

Relative `file://` search path entries are resolved from the current working
directory, so `$PWD/.omegaflow/config.yaml` is the project-local OmegaFlow
config.
