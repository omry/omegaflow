---
sidebar_label: Command Syntax
slug: /omegaflow/command-syntax
---

# Command Syntax

OmegaFlow uses Hydra's `key=value` override syntax:

```bash
omegaflow recording=<id> [action=<action>] [key=value ...]
```

Arguments are configuration overrides, not conventional `--long-option`
flags. For example:

```bash
omegaflow recording=tutorial/install action=check verbose=true
```

OmegaFlow discovers the project root automatically. Override it when operating
on another project without changing directories:

```bash
omegaflow project_root=/path/to/project recording=tutorial/install
```

## Select a recording

Recording ids map to directories under `studio.recording_dir`. With the default
workspace, `recording=tutorial/install` selects
`recordings/tutorial/install/index.md`.

```bash
omegaflow action=list
omegaflow recording=tutorial/install
```

Actions that operate across recordings do not require a selection. `list` and
`runs` are the common examples. `inspect`, `output`, and preserved-run `play`
can also work without `recording` when a `run_id` uniquely identifies a run.

## Select an action

`action` defaults to `build`:

```bash
# These are equivalent.
omegaflow recording=demo
omegaflow recording=demo action=build
```

The public actions are `bootstrap`, `build`, `check`, `clean`, `watch`, `play`,
`list`, `runs`, `inspect`, and `output`.

## Override values

Use dotted keys for nested configuration:

```bash
omegaflow action=list studio.recording_dir=demos
omegaflow recording=demo studio.run_gc.max_age_days=14
```

Hydra parses booleans, numbers, lists, mappings, and `null`. Quote values when
your shell or Hydra would otherwise interpret punctuation or whitespace:

```bash
omegaflow action=runs runs_since=2h runs_limit=null
omegaflow recording=demo +script_params.message='hello world'
```

OmegaFlow accepts `rec.*` shorthand even when the key does not exist in the
base tool config:

```bash
omegaflow recording=demo rec.capture.headless=false
```

Other keys added to an initially empty mapping use Hydra's `+` prefix. This is
why declared script parameters are passed as `+script_params.<name>=<value>`.

See [Overrides and Script Parameters](./overrides-parameters.md) before using
`rec.*` in repeatable project workflows.

## Config files versus command overrides

Put shared project defaults in `.omegaflow/config.yaml`:

```yaml
studio:
  recording_dir: recordings
  data_dir: recordings/.omegaflow
```

Use command-line overrides for a single invocation. CLI values have the highest
precedence. See [Project Configuration](../configuration.md) for the complete
composition order.

## Built-in help

```bash
omegaflow --help
omegaflow --hydra-help
```

The first command prints the composed OmegaFlow schema. Because it is generated
from Hydra, it also shows fields used internally by build stages. The
[Complete Option Reference](./option-reference.md) identifies those fields so
they are not mistaken for public actions or routine options.
