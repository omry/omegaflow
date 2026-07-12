---
sidebar_label: Runs, Inspect, and Output
slug: /omegaflow/actions/runs-inspect-output
---

# Runs, Inspect, and Output

OmegaFlow preserves successful and failed recording runs under
`studio.data_dir`. Three actions expose that history.

## List runs

List the ten most recent completed runs across all recordings:

```bash
omegaflow action=runs
```

Select one recording, filter by age, or change the limit:

```bash
omegaflow recording=demo action=runs
omegaflow action=runs runs_since=2h runs_limit=25
omegaflow action=runs runs_since=all runs_limit=null
```

`runs_since` accepts a number followed by `s`, `m`, `h`, or `d`. `all`, `none`,
an empty value, and `null` disable the age filter. `runs_limit` must be a
positive integer or `null`.

For automation, request JSON:

```bash
omegaflow action=runs output_format=json
```

Each entry reports the run id, age, recording id, result, playable length when
available, and failure reason.

## Inspect a failed run

Open the preserved postmortem shell for a run:

```bash
omegaflow recording=demo action=inspect run_id=20260712-101530
```

The shell restores the recording's working directory and exported session
state so you can inspect files and rerun commands. It is a diagnostic shell;
changes made there are not applied to the recording script automatically.

If `run_id` is omitted, OmegaFlow selects the latest run with a postmortem
entrypoint in the requested scope. If `recording` is omitted, a supplied run id
must be unique across recordings.

## Show captured failure output

Print or page the output attached to a failed command or check:

```bash
omegaflow recording=demo action=output run_id=20260712-101530
```

When stdout is a terminal, OmegaFlow uses `$PAGER` or `less`. When output is
redirected, it writes the captured text directly:

```bash
omegaflow action=output run_id=20260712-101530 > failure.log
```

See [Runs and Troubleshooting](../runs-troubleshooting.md) for the complete
failure workflow and retention behavior.
