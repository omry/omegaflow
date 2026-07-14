---
sidebar_label: Watch and Play
slug: /omegaflow/actions/watch-play
---

# Watch and Play

## Watch the built presentation

`watch` starts a temporary local HTTP server and opens the latest successful
build in OmegaFlow's isolated, managed Chromium:

```bash
omegaflow recording=demo action=watch
```

The player counts down from three and then starts with narration audio enabled.
OmegaFlow launches Chromium with a temporary profile, so this does not change
the autoplay policy or data in your normal browser profile. Close the browser
window or press Ctrl-C to stop the local server.

Under WSL, watch launches Windows Chrome or Edge so playback uses the host audio
stack instead of WSLg audio. Native Linux and macOS use OmegaFlow's pinned
Chromium and require the `browser` extra. See
[Runtime Dependencies](/runtime-dependencies#browser-recording) for setup.

Watch requires a recording id and a built presentation bundle. Use `play` to
select a preserved successful run.

## Play the latest build

With a recording id, `play` opens the manifest player for the latest successful
build:

```bash
omegaflow recording=demo action=play
```

Terminal-only, browser, and mixed builds all use the same manifest player.
Unlike `watch`, `play` uses the default browser and does not start playback
automatically.

To replay a preserved run, provide its id:

```bash
omegaflow recording=demo action=play run_id=20260712-101530
```

The recording selector can be omitted when that run id is unique across all
recordings:

```bash
omegaflow action=play run_id=20260712-101530
```

Without `run_id`, omitting `recording` selects the latest playable presentation
run across the workspace.
