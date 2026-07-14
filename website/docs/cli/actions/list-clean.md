---
sidebar_label: List and Clean
slug: /omegaflow/actions/list-clean
---

# List and Clean

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

Clean deliberately retains audio outputs, audio metadata, the audio cache, and
preserved recording runs. For browser/mixed recordings it removes the published
`presentation/` bundle but retains private capture and diagnostics in preserved
runs. Those can be expensive or valuable for diagnosis, whereas removed public
artifacts can be rebuilt.

For machine-readable output:

```bash
omegaflow recording=demo action=clean output_format=json
```

The JSON result has `removed` and `retained` lists.

Run retention is separate from `clean`. Old preserved runs are managed after a
successful build by `studio.run_gc`; see
[Runs and Troubleshooting](../runs-troubleshooting.md#retention).
