---
sidebar_label: Runs and Troubleshooting
slug: /omegaflow/runs-and-troubleshooting
---

# Runs and Troubleshooting

Every recording attempt gets a timestamped run directory under:

```text
<studio.data_dir>/runs/<recording-id>/<run-id>/
```

With the defaults, that is
`recordings/.omegaflow/runs/<recording-id>/<run-id>/`.

Successful runs preserve their captured cast and build inputs. Failed runs can
also preserve a partial cast, a structured failure report, captured output,
timeline progress, and an `enter` script for a postmortem shell.

## Failure workflow

When a build fails, start with the follow-up commands printed by OmegaFlow:

```bash
omegaflow recording=demo action=output run_id=<run-id>
omegaflow recording=demo action=inspect run_id=<run-id>
```

- `output` shows the captured output associated with the failure.
- `inspect` enters the preserved shell state and working directory.

Use `action=runs` when the run id has scrolled out of view:

```bash
omegaflow recording=demo action=runs
```

## Selecting runs safely

A timestamp can appear under more than one recording. Supplying both
`recording` and `run_id` removes that ambiguity:

```bash
omegaflow recording=demo action=inspect run_id=20260712-101530
```

Without `run_id`, diagnostic actions select the latest run that contains the
artifact they need. That convenience is useful interactively, but explicit run
ids are safer in notes and automation.

## Retention

After a successful build, OmegaFlow removes run directories whose filesystem
modification time is older than the configured maximum age. The current run is
always protected.

```yaml
studio:
  run_gc:
    enabled: true
    max_age_days: 30
    dry_run: false
```

Preview retention without deleting runs:

```bash
omegaflow recording=demo studio.run_gc.dry_run=true
```

This preview occurs when the successful build reaches its retention stage.
`action=clean` does not remove preserved runs.

## Moving run state

Set `studio.data_dir` in `.omegaflow/config.yaml` when runtime state should live
somewhere else:

```yaml
studio:
  data_dir: .cache/omegaflow
```

The `runs`, `inspect`, and `output` actions all use that configured directory.
