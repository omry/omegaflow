---
sidebar_label: Overrides and Script Parameters
slug: /omegaflow/overrides-and-parameters
---

# Overrides and Script Parameters

OmegaFlow provides two ways to change a selected recording from the command
line. They solve different problems.

## `rec.*`: override recording configuration

Use `rec.*` to replace a recording config value for one invocation:

```bash
omegaflow recording=demo rec.capture.headless=false
omegaflow recording=demo rec.audio.enabled=false
omegaflow recording=demo rec.outputs.asset_dir=preview/demo
```

The override is merged after workspace defaults and recording frontmatter. It
can change recording behavior, but cannot change identity or generated fields
such as `id` and OmegaFlow's private `_...` metadata.

`rec.*` is useful for diagnosis, local previews, and deliberate one-off output
variants. If a value defines the normal project behavior, put it in
`recordings/config.yaml` or the recording's frontmatter so the build remains
reproducible without a remembered command.

The accepted keys are the same as
[Recording Configuration](../recording-files/config.md).

## `script_params.*`: supply declared inputs

A recording can declare shell-safe parameters and their defaults:

```yaml
---
id: greeting
title: Greeting
parameters:
  name: world
  repetitions:
    default: 1
---
```

Override only declared names:

```bash
omegaflow recording=greeting +script_params.name=OmegaFlow
omegaflow recording=greeting +script_params.repetitions=3
```

The `+` is Hydra syntax for adding a key to the initially empty
`script_params` mapping.

Unknown parameter names fail configuration instead of being ignored. Resolved
values must be strings, numbers, or booleans and are exported to the recording
shell as `recording_param_<name>`:

```bash
printf 'hello %s\n' "$recording_param_name"
```

Use script parameters when a recording intentionally exposes an input. Use
`rec.*` when you need to override OmegaFlow recording configuration itself.

## Precedence

For recording data, the effective order is:

1. the recording schema defaults;
2. `recordings/config.yaml` workspace defaults;
3. the selected recording's frontmatter; and
4. command-line `rec.*` overrides.

`script_params.*` does not participate in that merge. It replaces defaults
declared under `parameters` after the recording config has been resolved.
