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

To start the watch server without opening a browser, use:

```bash
omegaflow recording=demo action=watch open=false
```

OmegaFlow prints the local player URL and serves it until you press Ctrl-C.

By default, the operating system selects a free local port. Use a fixed port
when a scripted recording needs the displayed URL to remain stable across
rebuilds:

```bash
omegaflow recording=demo action=watch watch_port=43123
```

OmegaFlow fails with a clear error if the configured port is already occupied;
it does not silently switch to a different URL.

Under WSL, watch launches Windows Chrome or Edge so playback uses the host audio
stack instead of WSLg audio. Native Linux and macOS also use an installed system
browser; watch does not use OmegaFlow's pinned recording browser or require the
`browser` extra.

Watch requires a recording id and a built presentation bundle. It always opens
the latest successful build for that recording.
