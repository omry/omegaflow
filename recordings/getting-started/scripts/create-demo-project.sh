#!/usr/bin/env bash
set -euo pipefail

demo_dir=/tmp/omegaflow-hello
rm -rf "$demo_dir"
mkdir -p "$demo_dir/recordings/hello/scripts" "$demo_dir/docs"

cat >"$demo_dir/recordings/config.yaml" <<'YAML'
capture:
  window_size: 72x14
  headless: true
  baseline_compressed: true
audio:
  enabled: false
  provider: openai
  env: OPENAI_API_KEY
  model: gpt-4o-mini-tts
  voice: marin
  format: mp3
YAML

cat >"$demo_dir/docs/hello.md" <<'MD'
# Hello Video

<!-- studio:hello-video:start -->
<!-- studio:hello-video:end -->
MD

cat >"$demo_dir/recordings/hello/scripts/hello.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

printf 'hello from OmegaFlow\n'
SH
chmod +x "$demo_dir/recordings/hello/scripts/hello.sh"

cat >"$demo_dir/recordings/hello/omegaflow.md" <<'MD'
---
id: hello
title: Hello Video
outputs:
  cast: site/videos/hello.cast
publish:
  default: docs
  build_surfaces:
  - docs
  - html
  surfaces:
    docs:
      type: docusaurus_mdx
      file: docs/hello.md
      placeholder: hello-video
      component: VideoPlayer
    html:
      type: standalone_html
      file: site/videos/hello.html
---

# Hello Video

This Markdown file is the source for one generated terminal video.

The YAML header names the recording, chooses output paths, and declares where
the finished video can be published. The prose explains the walkthrough for
readers and future maintainers. The fenced `studio-directive` blocks tell
OmegaFlow what to record.

```yaml studio-directive
scene: Hello Video
```

The scene is the title shown by the player. Beats are the steps in the video.
This beat runs a small shell script kept in this video's `scripts/` directory.

```yaml studio-directive
beat:
  id: hello
  heading: Say Hello
  narration: Run the support script and verify the terminal output.
  caption: A one-command terminal recording.
  actions:
  - commands:
    - run_file: scripts/hello.sh
      display: bash scripts/hello.sh
      expect:
        output_contains:
        - hello from OmegaFlow
```

Publish surfaces in the header let the same recording update docs or write a
standalone HTML page.
MD
