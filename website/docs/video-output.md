---
sidebar_position: 4
sidebar_label: Video
---

# Video Output

Video output is the generated, website-ready media built from an OmegaFlow
script.

The first supported video format is an asciinema cast plus a small browser
player. OmegaFlow records a fast baseline cast, then writes a retimed cast that is
comfortable to watch in documentation.

## Asset contract

A published video normally includes:

- a retimed `.cast` file
- the original baseline `.cast` and timeline sidecar
- a recording fingerprint that describes source dependencies
- optional audio and audio metadata
- optional timestamp sidecars used for narration alignment

The Docusaurus component embeds those assets with:

```mdx
<VideoPlayer
  title="Quickstart Demo"
  src="/omegaflow-videos/quickstart-demo/quickstart-demo.retimed.cast"
/>
```

## Player behavior

The embedded player supports normal playback controls, section markers, guided
mode metadata, and optional voiceover. It is designed for technical docs: the
terminal output remains inspectable, while the timing is controlled by the
generated metadata rather than by a hand-edited video file.
