---
sidebar_label: Reference
slug: /reference/
---

# Reference

Use Reference when you need the exact command, configuration, source, or output
contract. Start with [Getting Started](/getting-started/) for a first result or
use the [Guides](/guides/) for task-oriented explanations.

Most commands have this shape:

```bash
omegaflow recording=<id> [action=<action>] [option=value ...]
```

The default action is `build`:

```bash
omegaflow recording=test-video action=build
```

Because `build` is the default, `action=build` can be omitted:

```bash
omegaflow recording=test-video
```

## Main CLI workflows

| Goal | Command |
| --- | --- |
| Build a recording | `omegaflow recording=demo` |
| Watch in a browser | `omegaflow recording=demo action=watch` |
| Verify generated artifacts | `omegaflow recording=demo action=check` |
| Preview the build plan | `omegaflow recording=demo dry_run=true` |
| Troubleshoot runs | `omegaflow action=runs` |
| See available recordings | `omegaflow action=list` |
| Create a starter workspace | `omegaflow bootstrap=project` |

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

See [Project configuration](./configuration/project.md) and
[Recording configuration](./configuration/recordings.md) for the two schemas.

## Recommended commit policy

Commit the files that define reproducible recordings:

- `.omegaflow/config.yaml`
- `recordings/config.yaml`
- `recordings/<id>/index.md` and supporting scripts
- generated public assets when a publish surface intentionally writes to a
  tracked website directory

Ignore local runtime state and secrets. Bootstrap writes the secret rules to
`.omegaflow/.gitignore` and `recordings/.gitignore`. Add the runtime directory
to the repository's root `.gitignore` when it is not already covered:

```gitignore
# OmegaFlow runs, caches, and intermediate outputs
/recordings/.omegaflow/
```

If `studio.data_dir` points somewhere else, ignore that directory instead. Do
not ignore the project-level `.omegaflow/` directory as a whole: that would
also hide the configuration that should normally be committed.

## Reference map

- [Command syntax](./cli/syntax.md) explains Hydra overrides, quoting,
  recording selection, and defaults.
- [Build and check](./cli/build-check.md), [Bootstrap](./cli/bootstrap.md), and
  [Watch](./cli/watch.md) document the main actions.
- [Overrides and script parameters](./configuration/overrides.md) covers
  `rec.*` and `script_params.*`.
- [Runs](./cli/runs.md) specifies preserved run state; the
  [troubleshooting guide](/guides/troubleshooting/) gives the investigation
  workflow.
- [Complete option reference](./cli/options.md) lists every top-level
  CLI and `studio.*` field, including fields reserved for OmegaFlow's internal
  build stages.
- [Recording files](./recording-files/index.md) and the
  [recording schema](./recording-files/schema.md) define authored source.
- [Output](./output/index.md) defines publish surfaces and generated state.

Use `omegaflow --help` to print the composed config and
`omegaflow --hydra-help` for Hydra's own flags.

## Repository

The source lives at
[github.com/omry/omegaflow](https://github.com/omry/omegaflow).
