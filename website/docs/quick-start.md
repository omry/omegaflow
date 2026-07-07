---
sidebar_position: 2
sidebar_label: Build A Video
---

import VideoPlayer from "@site/src/components/VideoPlayer";

# Build A Video

This repository includes one starter recording at
`recordings/getting-started/omegaflow.md`. It demonstrates the basic loop:
write an OmegaFlow script, build the terminal recording, and embed the generated
video in the website.

<!-- studio:getting-started:start -->
<VideoPlayer
  title="Getting Started With OmegaFlow"
  src="/omegaflow-videos/getting-started/getting-started.retimed.cast"
  audio="/audio/casts/getting-started.mp3"
  audioMeta="/audio/casts/getting-started.json"
/>
<!-- studio:getting-started:end -->

## Build the sample

From the repository root:

```bash
studio recording=getting-started action=build
```

The build records the scripted terminal actions, retimes the cast, publishes the
configured website surface, and checks that visible captions and commands still
line up with the script.

## Useful commands

```bash
studio action=list
studio recording=getting-started action=inspect
studio recording=getting-started action=watch
studio recording=getting-started action=check
```

Use `action=watch` while editing a recording and `action=check` before
publishing. The generated website assets live under
`website/static/omegaflow-videos/`.
