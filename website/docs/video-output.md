---
sidebar_position: 4
sidebar_label: Video
---

# Video Output

Video output is the generated, website-ready media built from an OmegaFlow
script.

Terminal-only recordings use an asciinema cast plus a small browser player.
OmegaFlow records a fast baseline cast, then writes a retimed cast that is
comfortable to watch in documentation.

Browser and mixed recordings use a semantic presentation manifest. Each beat
has its own zero-based payload and the manifest assigns its offset in the whole
recording. Terminal beats remain text-based casts. Browser beats contain a
deterministic event timeline plus lossless WebP page states and, only when a
stable state cannot reproduce motion, muted VP8 WebM fragments. This is not a
live DOM replay and not one opaque full-video recording.

## Asset contract

A published terminal-only video normally includes:

- a retimed `.cast` file
- the original baseline `.cast` and timeline sidecar
- a recording fingerprint that describes source dependencies
- optional audio and audio metadata
- optional timestamp sidecars used for narration alignment

The Docusaurus component embeds those assets with:

```mdx
<VideoPlayer
  title="Quickstart Demo"
  src="/omegaflow-videos/quickstart-demo/recording.retimed.cast"
/>
```

A browser or mixed build publishes an atomic bundle under
`<asset_dir>/presentation/`:

```text
presentation/
  recording.presentation.json
  recording.recording.json
  beats/*.cast
  beats/*.browser.json
  media/*.webp
  media/*.webm             # only when captured motion is required
  audio.*                  # when narration is enabled
  audio.json
  timestamps/*.json
```

The Docusaurus embed points at the manifest for terminal, browser, and mixed
recordings alike:

```mdx
<VideoPlayer
  title="Quickstart Demo"
  manifest="/omegaflow-videos/quickstart-demo/presentation/recording.presentation.json"
/>
```

The publisher validates every reference, hash, path, media stream, and allowed
file class before atomically replacing the previous bundle. Private capture
logs, diagnostics, authentication state, and raw Playwright video are never
publish candidates.

## Player behavior

The embedded player supports normal playback controls, seeking, playback-rate
changes, beat markers, guide text, and optional voiceover. It switches terminal
and browser renderers on one global clock. Browser payloads scale into the
available player area without reflowing the recorded viewport and can add
browser chrome, a mocked display URL, and a Windows/KDE-style window frame.
