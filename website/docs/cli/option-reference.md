---
sidebar_label: Complete Option Reference
slug: /omegaflow/options
---

# Complete Option Reference

This page covers the top-level schema printed by `omegaflow --help`. Defaults
shown here are the bundled defaults; `.omegaflow/config.yaml` can replace them.

## Command and selection fields

| Field | Default | Used by | Meaning |
| --- | --- | --- | --- |
| `action` | `build` | all | Public operation: `bootstrap`, `build`, `check`, `clean`, `watch`, `play`, `inspect`, `output`, `runs`, or `list`. |
| `recording` | `null` | most actions | Recording id under `studio.recording_dir`. Required by `build`, `check`, `clean`, and `watch`; optional where run-wide selection is supported. |
| `output_format` | `text` | `runs`, `clean`, build preview | Use `json` for machine-readable output from supported operations. |
| `verbose` | `false` | `build` | Show detailed freshness and artifact information. |
| `dry_run` | `false` | `build`, `bootstrap` | `true` previews a build or lists bootstrap files. Bootstrap also accepts `diff`. |
| `force` | `false` | `build`, `bootstrap` | Rebuild reusable stages or replace bootstrap-created targets. |
| `headed` | `false` | `build` | Override headless capture and show the recorder terminal. |
| `surface` | `null` | `build` | Publish only the named configured surface. |
| `timestamps` | `true` | `build` | Generate narration word timestamps when audio/transcription settings require them. |
| `allow_mismatch` | `false` | `check`, `build` | Advanced escape hatch: report an alignment mismatch without failing that alignment stage. Prefer fixing the script. |

## Run and playback fields

| Field | Default | Used by | Meaning |
| --- | --- | --- | --- |
| `cast` | `null` | `play` | Path to a cast file to play directly, bypassing preserved-run selection. |
| `run_id` | `null` | `play`, `inspect`, `output` | Timestamped preserved run id. Add `recording` if the id is ambiguous. |
| `runs_since` | `null` | `runs` | Age filter such as `30m`, `2h`, or `1d`; `null`/`all` means no age limit. |
| `runs_limit` | `10` | `runs` | Maximum rows to return; use a positive integer or `null`. |

## Environment and recording override fields

| Field | Default | Meaning |
| --- | --- | --- |
| `project_root` | auto-discovered | Base directory for relative project, recording, data, output, and env-file paths. May be overridden with an absolute or current-directory-relative path. |
| `load_env_file` | `true` | Load a process-level env file before actions that execute or inspect recording work. |
| `env_file` | `.env` | Env file path resolved from the project root; `null` disables it even when loading is enabled. |
| `env_override` | `false` | Let env-file values replace variables already present in the process environment. |
| `rec` | `{}` | Recording config merged after workspace defaults and recording frontmatter. Use CLI keys such as `rec.capture.headless=false`. |
| `script_params` | `{}` | Values for names declared by the recording's `parameters` mapping. |

## Bootstrap field

| Field | Default | Meaning |
| --- | --- | --- |
| `workspace` | `null` | Destination for `action=bootstrap`; when unset, uses `studio.recording_dir`. |

## `studio.*` project fields

| Field | Default | Meaning |
| --- | --- | --- |
| `studio.recording_dir` | `recordings` | Workspace containing `config.yaml` and one `index.md` per recording id. |
| `studio.data_dir` | `recordings/.omegaflow` | Local run state, scratch data, caches, and generated runtime artifacts. |
| `studio.keep_output_dir` | `true` | Preserve OmegaFlow's Hydra output directory after the recording session. |
| `studio.asciinema_path` | `null` | Explicit asciinema 3.x executable. When unset, OmegaFlow uses its bundled recorder when available, then `asciinema` on `PATH`. |
| `studio.run_gc.enabled` | `true` | Run age-based garbage collection after successful builds. |
| `studio.run_gc.max_age_days` | `30` | Remove run directories whose filesystem modification time is older than this many days. |
| `studio.run_gc.dry_run` | `false` | Report what retention would remove without deleting it. |

## Internal build-stage fields

Hydra's generated help also displays the fields below because the public
`build` action passes one composed config through its internal stages. They are
not independent public operations and are not normal project configuration.

| Field | Default | Internal use |
| --- | --- | --- |
| `step` | `null` | Selects a private build stage when OmegaFlow invokes its own modules. Do not set it in normal CLI use. |
| `output` | `null` | Overrides a stage's generated output path. It is unrelated to `action=output`. |
| `timeline` | `null` | Supplies the captured timing timeline to the presentation-processing stage. |
| `audio_metadata` | `null` | Supplies narration timing metadata to the presentation-processing stage. |

These fields remain visible so all stages share one typed configuration. Their
presence in `--help` is not a stability promise for direct use.

## Related schemas

This table covers the tool schema only. The much larger recording schema is
documented under [Recording Configuration](../recording-files/config.md), and
command/beat structure is documented under
[Beat](../recording-files/beat.md).
