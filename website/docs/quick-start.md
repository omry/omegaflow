---
sidebar_position: 2
sidebar_label: Quick Start
---

import OmegaFlowVideo from "@site/src/components/OmegaFlowVideo";

# Quick Start

This repository includes one starter recording at
`studio/recordings/getting-started.md`. It demonstrates the basic loop:
write a Studio script, build the terminal recording, and embed the generated
OmegaFlow Video in the website.

<!-- studio:getting-started:start -->
<OmegaFlowVideo
  title="Getting Started With OmegaFlow Studio"
  src="/omegaflow-videos/getting-started/getting-started.retimed.cast"
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
