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

To open the player paused, without the countdown or automatic playback, use:

```bash
omegaflow recording=demo action=watch autoplay=false
```

To start the watch server without opening a browser, use:

```bash
omegaflow recording=demo action=watch open=false
```

OmegaFlow prints the local player URL and serves it until you press Ctrl-C.

## Watch a collection

When the selected recording has `kind: collection`, `watch` opens one local
index for all of its built videos:

```bash
omegaflow recording=tutorial action=watch
```

The index keeps the collection order, shows each video's title and description,
and filters locally as you type. Its compact video list scrolls independently,
so collections are not limited to a particular number of members. Selecting a
video opens its normal player on the same local server.

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
