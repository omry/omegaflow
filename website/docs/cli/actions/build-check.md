---
sidebar_label: Build and Check
slug: /omegaflow/actions/build-check
---

# Build and Check

## Build

Build is the default action:

```bash
omegaflow recording=demo
```

A build:

1. records the scripted terminal session when the current recording is stale;
2. prepares narration audio when enabled;
3. adjusts terminal and narration timing for presentation;
4. checks visible commands and captions against the script;
5. publishes the configured output surfaces; and
6. applies run retention after a successful build.

For browser and mixed recordings, the analogous pipeline normalizes the typed
plan, reuses or captures one persistent terminal/browser environment, prepares
narration takes, solves a global semantic timeline, validates the closed public
bundle, and publishes the selected surfaces. Presentation-only changes reuse a
fresh private capture.

Fresh artifacts are reused. Force every rebuildable stage to run with:

```bash
omegaflow recording=demo force=true
```

Use a visible recorder window for one run with `headed=true`:

```bash
omegaflow recording=demo headed=true
```

`verbose=true` exposes more detail about freshness decisions and generated
artifacts.

## Preview a build

`dry_run=true` resolves the recording and prints its inputs, outputs, publish
targets, and processing stages without running commands:

```bash
omegaflow recording=demo dry_run=true
omegaflow recording=demo dry_run=true output_format=json
```

This is a build mode, not a separate action.

## Select a publish surface

When a recording defines multiple publish surfaces, `surface` limits this build
to one named surface:

```bash
omegaflow recording=demo surface=docs
```

Without the override, the recording's `publish.on_build`,
`publish.build_surfaces`, and `publish.default` settings decide what is
published.

## Check

Check validates the recording and the freshness/alignment of generated
artifacts without rebuilding them:

```bash
omegaflow recording=demo action=check
```

Use it when a CI job or release gate should fail instead of silently updating
an output. If it reports stale or missing artifacts, run a build and check
again.

For browser/mixed recordings, check validates the source, capture fingerprint,
complete `run_end`, and the freshest run-local presentation bundle when one
exists. It does not visit external sites or rebuild artifacts.

## Common failures

- **No recording selected:** add `recording=<id>` or use `action=list`.
- **A scripted command failed:** use the printed `action=inspect` and
  `action=output` follow-ups. See
  [Runs and Troubleshooting](../runs-troubleshooting.md).
- **Generated output is stale:** rebuild the selected recording.
- **A publish surface is unknown:** check the recording's `publish.surfaces`
  mapping or remove the `surface` override.
