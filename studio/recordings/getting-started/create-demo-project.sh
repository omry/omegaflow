#!/usr/bin/env bash
set -euo pipefail

demo_dir=/tmp/omegaflow-hello
rm -rf "$demo_dir"
mkdir -p "$demo_dir/studio/conf" "$demo_dir/studio/recordings" "$demo_dir/docs"

cat >"$demo_dir/studio/conf/config.yaml" <<'YAML'
defaults:
  - studio_schema
  - override hydra/job_logging: disabled
  - override hydra/hydra_logging: disabled
  - _self_

recording: null
action: build
load_env_file: false
studio:
  data_dir: studio
  keep_output_dir: true
hydra:
  output_subdir: null
  run:
    dir: ${studio_run_dir:${studio.data_dir},${action},${step},${dry_run},${recording},${now:%Y%m%d-%H%M%S}}
  job:
    chdir: false
YAML

cat >"$demo_dir/docs/hello.md" <<'MD'
# Hello Video

<!-- studio:hello-video:start -->
<!-- studio:hello-video:end -->
MD

cat >"$demo_dir/studio/recordings/hello.md" <<'MD'
# Hello Video

```yaml studio-directive
scene: Hello Video
```

```yaml studio-directive
recording:
  id: hello
  title: Hello Video
  capture:
    window_size: 72x14
    headless: true
    baseline_compressed: true
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
  audio:
    enabled: false
    provider: openai
    env: OPENAI_OMEGAFLOW_API_KEY
    model: gpt-4o-mini-tts
    voice: marin
    format: mp3
```

```yaml studio-directive
beat:
  id: hello
  heading: Say Hello
  narration: Print one line in the terminal.
  caption: A one-command terminal recording.
  actions:
  - commands:
    - run: printf 'hello from OmegaFlow\n'
```
MD
