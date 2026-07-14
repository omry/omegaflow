---
sidebar_label: Watch and Play
slug: /omegaflow/actions/watch-play
---

# Watch and Play

## Watch the built presentation

`watch` starts a temporary local HTTP server and opens OmegaFlow's browser
player for the latest successful build:

```bash
omegaflow recording=demo action=watch
```

The server exposes the built presentation assets without requiring them to live
under the website's static directory. Press Ctrl-C to stop it. In an environment
where OmegaFlow cannot open a graphical browser, open the printed URL manually.

Watch requires a recording id and built artifacts. It does not accept
`run_id` or `cast`; use `play` for preserved or arbitrary casts.

## Play the latest build

With a recording id, `play` replays the presentation-timed cast from the latest
successful build:

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

Without `run_id`, omitting `recording` selects the latest preserved playable
run across the workspace. Preserved runs contain the fast captured cast, so
this form is mainly useful for diagnosis.

## Play a cast file directly

`cast` bypasses run selection:

```bash
omegaflow action=play cast=path/to/recording.cast
```

Paths are resolved from the project root. Direct cast playback does not add
narration or the browser player's presentation controls.
