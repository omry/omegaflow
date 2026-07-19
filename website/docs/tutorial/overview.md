---
sidebar_position: 1
sidebar_label: Overview
slug: /tutorial
---

# Tutorial

The tutorial is the guided path through OmegaFlow. Each chapter pairs a short
website page with a recording under `recordings/tutorial/`.

## Chapters

1. [Quickstart](./quickstart.md): create the smallest useful video.
2. [Recording File](./recording-file.md): source file and support files.
3. [Beat](./beat.md): narration, commands, and checks.
4. [Publishing](./publishing.md): docs embeds and standalone HTML.

Build all tutorial videos in chapter order from the repository root:

```bash
omegaflow recording=tutorial
```

Then open one local index containing every built tutorial video:

```bash
omegaflow recording=tutorial action=watch
```
