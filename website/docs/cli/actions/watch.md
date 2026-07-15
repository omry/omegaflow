---
sidebar_label: Watch
slug: /omegaflow/actions/watch-play
---

# Watch

## Watch the built presentation

`watch` starts a temporary local HTTP server and opens the latest successful
build in an isolated instance of an available system browser:

```bash
omegaflow recording=demo action=watch
```

The player counts down from three and then starts with narration audio enabled.
OmegaFlow launches an installed Chrome, Chromium, Edge, or Brave browser with a
temporary profile and an audible-autoplay override. This does not change the
autoplay policy or data in your normal browser profile. Close the browser window
or press Ctrl-C to stop the local server.

Under WSL, watch launches Windows Chrome or Edge so playback uses the host audio
stack instead of WSLg audio. Native Linux and macOS also use an installed system
browser; watch does not use OmegaFlow's pinned recording browser or require the
`browser` extra.

Watch requires a recording id and a built presentation bundle. It always opens
the latest successful build for that recording.
