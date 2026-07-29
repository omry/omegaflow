---
sidebar_position: 2
sidebar_label: First Video
slug: /getting-started/first-video/
---

import VideoPlayer from "@site/src/components/VideoPlayer";

# Build Your First Video

Start from the root of the repository where you want to keep the recording
source.

<!-- studio:quickstart-demo:start -->
<VideoPlayer
  title="Quickstart Demo"
  manifest="/omegaflow-videos/quickstart-demo/presentation/recording.presentation.json"
/>
<!-- studio:quickstart-demo:end -->

## Create the workspace

```bash
omegaflow bootstrap=project
```

OmegaFlow creates project settings, recording defaults, ignored local service
credentials, and a small `test-video` recording. The generated source belongs
in version control; runtime runs and secrets do not.

## Build and watch

```bash
omegaflow recording=test-video action=build
omegaflow recording=test-video action=watch
```

The build captures the scripted workflow and assembles a local player. Watch
serves the latest successful build and rebuilds when the recording source
changes.

## Make one source change

Open `recordings/test-video/index.md` and change the visible text in one
terminal command and its matching expectation. Save the file. The watch
process waits briefly for editing to settle, rebuilds the video, and reports
when the watched surface is updated. Refresh the player to load that completed
build.

Before publishing a recording, check its generated artifacts:

```bash
omegaflow recording=test-video action=check
```

You now have the core loop: **author, build, watch, and check**. Use
[Next steps](./next-steps.md) to add browser capture, narration, timing, or
guided playback.
