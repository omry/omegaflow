---
sidebar_label: Watch
slug: /omegaflow/actions/watch-play
---

# Watch

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

Watch requires a recording id and a built presentation bundle. It always opens
the latest successful build for that recording.
