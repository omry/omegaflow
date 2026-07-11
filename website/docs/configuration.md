---
sidebar_position: 3
sidebar_label: OmegaFlow Configuration
---

# OmegaFlow Configuration

OmegaFlow configuration controls the `omegaflow` tool itself: where recording files
live, where generated run state is written, which action runs by default, and
how the process loads its `.env` file. It is separate from recording
configuration, which lives in the recording workspace and describes individual
videos.

The bundled OmegaFlow base config is used by default. `omegaflow
action=bootstrap` creates a project-local tool config so the recording workspace
is explicit for everyone working in that project.

## Override Order

OmegaFlow composes tool config in this order:

1. Schema default values.
2. The bundled `base-config.yaml`.
3. `$PWD/.omegaflow/config.yaml`, when it exists.
4. CLI overrides such as `omegaflow action=list studio.recording_dir=demos`.

The local file is optional, but bootstrap creates it.

```text
.omegaflow/
  config.yaml
```

## Project Config File

A project-owned OmegaFlow config should contain only the fields your project
wants to override:

```yaml
studio:
  recording_dir: recordings
  data_dir: recordings/.omegaflow
```

OmegaFlow looks for `.omegaflow/config.yaml` in the current project. The file is
optional, and `omegaflow action=bootstrap` creates it for new projects.

Use the file for project defaults that should be shared by everyone working in
the repository. Use CLI overrides for one-off changes:

```bash
omegaflow action=list studio.recording_dir=demos
omegaflow recording=hello rec.capture.headless=false
```

## Common Fields

| Field | Purpose |
| --- | --- |
| `studio.recording_dir` | Directory containing `config.yaml` plus one directory per video. Each video directory contains `index.md`. |
| `studio.data_dir` | Directory for generated run state, scratch output, caches, and generated artifacts. Defaults to `recordings/.omegaflow`. |
| `studio.keep_output_dir` | Keeps Hydra's output directory metadata when a run is created. |
| `studio.run_gc.enabled` | Removes old recording runs after a successful build. Defaults to `true`. |
| `studio.run_gc.max_age_days` | Retains successful, failed, and incomplete runs modified within this many days. Defaults to `30`. |
| `studio.run_gc.dry_run` | Reports runs that retention would remove without deleting them. Defaults to `false`. |
| `load_env_file` | Enables loading a process-level `.env` file before running actions. |
| `env_file` | Path to the process-level `.env` file, resolved from the project root. |
| `env_override` | Allows values from `env_file` to replace existing environment variables. |
| `workspace` | Bootstrap-only destination for `action=bootstrap`; defaults to `studio.recording_dir`. |
| `dry_run` | Preview without writing. For bootstrap, use `dry_run=true` to list generated files or `dry_run=diff` to show unified diffs. |
| `rec` | Recording config overrides merged on top of the selected recording. CLI shorthand such as `rec.capture.headless=false` is supported. |

Recording defaults such as capture style, audio generation, publish surfaces,
beats, setup, and cleanup belong in the recording workspace. See
[Recording Configuration](./recording-files/config.md).
