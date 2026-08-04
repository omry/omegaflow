---
sidebar_label: Complete Option Reference
slug: /reference/cli/options/
---

# Complete Option Reference

This page covers the top-level schema printed by `omegaflow --help`. Defaults
shown here are the bundled defaults; `.omegaflow/config.yaml` can replace them.

## Command and selection fields

| Field | Default | Used by | Meaning |
| --- | --- | --- | --- |
| `action` | `null` (defaults to `build`) | recording operations | Public recording operation: `build`, `check`, `clean`, `gc`, `watch`, `inspect`, `output`, `runs`, or `list`. Mutually exclusive with `bootstrap`. |
| `bootstrap` | `null` | project setup | Typed setup operation: `project` or `tutorial`. Mutually exclusive with `action`; `tutorial` is reserved for the forthcoming tutorial workspace. |
| `recording` | `null` | most actions | Video or collection id under `studio.recording_dir`. Collections build ordered groups and provide a watch index; other single-video actions require a video id. |
| `output_format` | `text` | `runs`, `clean`, build preview | Use `json` for machine-readable output from supported operations. |
| `verbose` | `false` | `build` | Show detailed freshness and artifact information. |
| `dry_run` | `false` | `build`, `bootstrap`, `gc` | `true` previews a build, lists bootstrap files, or reports runs that GC would remove. Bootstrap also accepts `diff`. |
| `force` | `false` | `build`, `bootstrap` | Rebuild reusable stages or replace bootstrap-created targets. |
| `headed` | `false` | `build` | Override headless capture and show the recorder terminal. |
| `open` | `true` | `watch` | Open an isolated browser. Set `false` to serve the player without opening one. |
| `autoplay` | `true` | `watch` | Count down and start playback automatically. Set `false` to open paused. |
| `beat` | `null` | `watch` | Start at this top-level beat ID. Nested pane beats are not independent watch targets. |
| `watch_port` | `null` | `watch` | Bind the local player server to this fixed port. The default lets the operating system select a free port. |
| `surface` | `null` | `build` | Publish only the named configured surface. |

## Run selection fields

| Field | Default | Used by | Meaning |
| --- | --- | --- | --- |
| `run_id` | `null` | `inspect`, `output` | Timestamped preserved run id. Add `recording` if the id is ambiguous. |
| `runs_since` | `null` | `runs` | Age filter such as `30m`, `2h`, or `1d`; `null`/`all` means no age limit. |
| `runs_limit` | `10` | `runs` | Maximum rows to return; use a positive integer or `null`. |

## Environment and recording override fields

| Field | Default | Meaning |
| --- | --- | --- |
| `project_root` | auto-discovered | Base directory for relative project, recording, data, output, and env-file paths. May be overridden with an absolute or current-directory-relative path. |
| `load_env_file` | `false` | Load an explicitly configured process-level env file before actions that execute or inspect recording work. |
| `env_file` | `null` | Optional env file path resolved from the project root. This advanced compatibility path is separate from OmegaFlow's private TTS environment. |
| `env_override` | `false` | Let env-file values replace variables already present in the process environment. |
| `rec` | `{}` | Recording config merged after workspace defaults and the recording's `config` directive. Use CLI keys such as `rec.capture.headless=false`. |
| `script_params` | `{}` | Values for names declared by the recording's `parameters` mapping. |

## Bootstrap fields

| Field | Default | Meaning |
| --- | --- | --- |
| `workspace` | `null` | Destination for `bootstrap=project`; when unset, uses `studio.recording_dir`. |

## `studio.*` project fields

| Field | Default | Meaning |
| --- | --- | --- |
| `studio.recording_dir` | `recordings` | Workspace containing `config.yaml` and one `index.md` per recording id. |
| `studio.data_dir` | `recordings/.omegaflow` | Local run state, scratch data, caches, and generated runtime artifacts. |
| `studio.keep_output_dir` | `true` | Preserve OmegaFlow's Hydra output directory after the recording session. |
| `studio.asciinema_path` | `null` | Explicit asciinema 3.x executable. When unset, OmegaFlow uses its bundled recorder when available, then `asciinema` on `PATH`. |
| `studio.run_gc.enabled` | `true` | Run garbage collection after successful builds and allow explicit `action=gc`. |
| `studio.run_gc.max_age_days` | `30` | Remove run directories whose filesystem modification time is older than this many days. |
| `studio.run_gc.max_runs_per_recording` | `10` | Retain at most this many runs per recording, subject to protected-run exceptions. |
| `studio.run_gc.preserve_latest_failure` | `true` | Protect the newest failed run for each recording so its diagnostics remain available. |

## Advanced build-stage fields

`step` is a typed, public interface for running selected build stages. The
remaining fields below are internal plumbing shared by OmegaFlow's tools and
are not normal project configuration.

| Field | Default | Meaning |
| --- | --- | --- |
| `step` | `null` | Runs one build stage directly. Supported values are `capture` and `narration`. |
| `output` | `null` | Overrides a stage's generated output path. It is unrelated to `action=output`. |
| `timeline` | `null` | Supplies the captured timing timeline to the presentation-processing stage. |
| `audio_metadata` | `null` | Supplies narration timing metadata to the presentation-processing stage. |

The internal fields remain visible because all tools share one typed
configuration. Their presence in `--help` is not a stability promise for direct
use.

## Related schemas

This table covers the tool schema only. The much larger recording schema is
documented under [Recording Configuration](../configuration/recordings.md), and
command/beat structure is documented under
[Recording schema](../recording-files/schema.md).
