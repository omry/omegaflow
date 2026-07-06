---
sidebar_position: 4
sidebar_label: Video
---

# OmegaFlow Video

An OmegaFlow Video is the generated, website-ready output built from an
OmegaFlow Studio script.

The first supported video format is an asciinema cast plus a small browser
player. Studio records a fast baseline cast, then writes a retimed cast that is
comfortable to watch in documentation.

## Asset contract

A published OmegaFlow Video normally includes:

- a retimed `.cast` file
- the original baseline `.cast` and timeline sidecar
- a recording fingerprint that describes source dependencies
- optional audio and audio metadata
- optional timestamp sidecars used for narration alignment

The Docusaurus component embeds those assets with:

```mdx
<OmegaFlowVideo
  title="Getting Started With OmegaFlow Studio"
  src="/omegaflow-videos/getting-started/getting-started.retimed.cast"
/>
```

## Player behavior

The embedded player supports normal playback controls, section markers, guided
mode metadata, and optional voiceover. It is designed for technical docs: the
terminal output remains inspectable, while the timing is controlled by the
generated metadata rather than by a hand-edited video file.
