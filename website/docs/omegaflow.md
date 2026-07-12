---
sidebar_label: Overview
slug: /omegaflow
---

# OmegaFlow CLI

The `omegaflow` command turns a versioned recording script into a terminal
video, checks generated artifacts, opens local playback, and preserves runs for
troubleshooting.

Most commands have this shape:

```bash
omegaflow recording=<id> [action=<action>] [option=value ...]
```

The default action is `build`:

```bash
omegaflow recording=quickstart-demo action=build
```

Because `build` is the default, `action=build` can be omitted:

```bash
omegaflow recording=quickstart-demo
```

## Main workflows

| Goal | Command |
| --- | --- |
| Build a recording | `omegaflow recording=demo` |
| Watch in a browser | `omegaflow recording=demo action=watch` |
| Verify generated artifacts | `omegaflow recording=demo action=check` |
| Play in the terminal | `omegaflow recording=demo action=play` |
| Preview the build plan | `omegaflow recording=demo dry_run=true` |
| Troubleshoot runs | `omegaflow action=runs` |
| See available recordings | `omegaflow action=list` |
| Create a starter workspace | `omegaflow action=bootstrap` |

`build` is the user-facing operation. It records the scripted terminal
session, prepares optional narration, adjusts presentation timing, validates
the result, and publishes the configured surfaces. Those processing stages are
not separate public CLI actions.

## Configuration layers

OmegaFlow has two distinct configuration surfaces:

- **Tool configuration** typically lives in `.omegaflow/config.yaml`. It
  controls the CLI, project paths, environment loading, run retention, and
  one-off overrides.
- **Recording configuration** typically lives in `recordings/config.yaml` for
  workspace defaults and `recordings/<id>/index.md` frontmatter for one
  recording. It controls terminal capture, beats, narration, commands, audio,
  outputs, and publishing.

See [Project Configuration](./configuration.md) and
[Recording Configuration](./recording-files/config.md) for the two schemas.

## Recommended commit policy

Commit the files that define reproducible recordings:

- `.omegaflow/config.yaml`
- `recordings/config.yaml`
- `recordings/<id>/index.md` and supporting scripts
- generated public assets when a publish surface intentionally writes to a
  tracked website directory

Ignore local runtime state and secrets. With the default paths, add:

```gitignore
# OmegaFlow runs, caches, and intermediate outputs
/recordings/.omegaflow/

# Local environment values and secrets
.env
```

If `studio.data_dir` points somewhere else, ignore that directory instead. Do
not ignore `.omegaflow/` as a whole: that would also hide the project config
that should normally be committed.

## Reference map

- [Command Syntax](./cli/command-syntax.md) explains Hydra overrides, quoting,
  recording selection, and defaults.
- **Actions** documents every public action, grouped by workflow.
- [Overrides and Script Parameters](./cli/overrides-parameters.md) covers
  `rec.*` and `script_params.*`.
- [Runs and Troubleshooting](./cli/runs-troubleshooting.md) explains preserved
  run state and the failure investigation workflow.
- [Complete Option Reference](./cli/option-reference.md) lists every top-level
  CLI and `studio.*` field, including fields reserved for OmegaFlow's internal
  build stages.

Use `omegaflow --help` to print the composed config and
`omegaflow --hydra-help` for Hydra's own flags.

## Repository

The source lives at
[github.com/omry/omegaflow](https://github.com/omry/omegaflow).
