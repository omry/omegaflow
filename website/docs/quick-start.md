---
sidebar_position: 2
sidebar_label: Build A Video
---

import VideoPlayer from "@site/src/components/VideoPlayer";

# Build A Video

This repository includes one short showcase recording at
`recordings/quickstart-demo/index.md`. It demonstrates the basic loop:
bootstrap the default `quickstart` recording, build it, view it in a browser,
and publish it with the docs.

<!-- studio:quickstart-demo:start -->
<VideoPlayer
  title="Quickstart Demo"
  src="/omegaflow-videos/quickstart-demo/quickstart-demo.retimed.cast"
  audio="/audio/casts/quickstart-demo.mp3"
  audioMeta="/audio/casts/quickstart-demo.json"
/>
<!-- studio:quickstart-demo:end -->

## Build the sample

From the repository root:

```bash
omegaflow recording=quickstart-demo action=build
```

The build records the scripted terminal actions, retimes the cast, publishes the
configured website surface, and checks that visible captions and commands still
line up with the script.

## Useful commands

```bash
omegaflow action=list
omegaflow recording=quickstart-demo action=watch
omegaflow recording=quickstart-demo action=check
```

Use `action=watch` while editing a recording and `action=check` before
publishing. OmegaFlow currently supports plain HTML and Docusaurus publish
surfaces. The generated website assets live under `website/static/`.
