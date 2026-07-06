#!/usr/bin/env bash
set -euo pipefail

demo_dir=/tmp/omegaflow-hello
rm -rf "$demo_dir"
mkdir -p "$demo_dir/recordings/hello" "$demo_dir/docs"

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

cat >"$demo_dir/recordings/hello/hello.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

printf 'hello from OmegaFlow\n'
SH
chmod +x "$demo_dir/recordings/hello/hello.sh"

cat >"$demo_dir/recordings/hello.md" <<'MD'
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
      component: OmegaFlowVideo
    html:
      type: standalone_html
      file: site/videos/hello.html
---

# Hello Video

```yaml studio-directive
scene: Hello Video
```

```yaml studio-directive
beat:
  id: hello
  heading: Say Hello
  narration: Print one line in the terminal.
  caption: A one-command terminal recording.
  actions:
  - commands:
    - run_file: hello/hello.sh
      display: bash hello/hello.sh
```
MD
