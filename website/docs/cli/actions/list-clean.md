---
sidebar_label: List, Clean, and GC
slug: /omegaflow/actions/list-clean
---

# List, Clean, and GC

## List recordings

Print every recording id discovered under `studio.recording_dir`:

```bash
omegaflow action=list
```

Nested directories become slash-separated ids. For example,
`recordings/tutorial/install/index.md` is listed as `tutorial/install`.

Override the workspace for one invocation when needed:

```bash
omegaflow action=list studio.recording_dir=demos
```

## Clean generated outputs

Remove rebuildable published artifacts for one recording:

```bash
omegaflow recording=demo action=clean
```

Clean deliberately retains the narration cache and preserved recording runs. It
removes the published `presentation/` bundle but retains private capture and
diagnostics. Those can be expensive or valuable for diagnosis, whereas removed
public artifacts can be rebuilt.

For machine-readable output:

```bash
omegaflow recording=demo action=clean output_format=json
```

The JSON result has `removed` and `retained` lists.

Run retention is separate from `clean`. Preserved runs are bounded by age and
per-recording count after a successful build.

## Preview or run garbage collection

Preview cleanup across all recordings:

```bash
omegaflow action=gc dry_run=true
```

Remove the reported runs:

```bash
omegaflow action=gc
```

Add `recording=demo` to target one recording. Automatic post-build cleanup
protects the current build; both automatic and explicit cleanup protect the
newest failed run when configured to do so. See
[Runs and Troubleshooting](../runs-troubleshooting.md#retention).
