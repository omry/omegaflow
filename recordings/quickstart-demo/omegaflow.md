---
id: quickstart-demo
title: Quickstart Demo
capture:
  window_size: 90x24
  headless: true
  baseline_compressed: true
style:
  color: true
  typing: true
  typing_min_delay: 0.02
  typing_max_delay: 0.06
  typing_space_delay: 0.03
  typing_punctuation_delay: 0.05
  typing_newline_delay: 0.12
  typing_seed: 5
outputs:
  cast: website/static/omegaflow-videos/quickstart-demo/quickstart-demo.cast
publish:
  default: docusaurus
  surfaces:
    docusaurus:
      type: docusaurus_mdx
      file: website/docs/quick-start.md
      placeholder: quickstart-demo
      component: VideoPlayer
    standalone_html:
      type: standalone_html
      file: website/static/omegaflow-videos/quickstart-demo/index.html
retime:
  typing_char_delay: 0.03
  typing_space_delay: 0.02
  typing_punctuation_delay: 0.04
  typing_newline_delay: 0.0
  post_enter_pause: 0.25
  post_command_pause: 0.55
  minimum_section_spacing: 0.6
environment:
  working_directory: .
  path_prepend:
  - recordings/quickstart-demo/bin
audio:
  enabled: false
---

# Quickstart Demo

This is the short homepage demo. It is not the tutorial itself: it shows the
bootstrap command creating the `quickstart` recording, then builds and plays
that generated recording.

```yaml studio-directive
scene: Quickstart Demo
```

```yaml studio-directive
beat:
  id: install
  heading: Install The CLI
  narration: >-
    Start with the OmegaFlow command line tool. Install the package, then use
    the studio command to create and build recordings.
  marker: install
  caption: Install the OmegaFlow CLI.
  actions:
  - commands:
    - run: python -m pip install omegaflow
      display: python -m pip install omegaflow
      output:
        mode: fake
        text: |
          Successfully installed omegaflow
  guide:
    commands:
    - python -m pip install omegaflow
    success_hint: The install provides the studio command.
```

```yaml studio-directive
beat:
  id: bootstrap
  heading: Bootstrap Quickstart
  narration: >-
    Bootstrap creates the default recording workspace and writes the generated
    quickstart recording into it.
  marker: bootstrap
  caption: Create the generated quickstart recording.
  actions:
  - commands:
    - run: bash recordings/quickstart-demo/scripts/create-demo-project.sh
      display: |-
        mkdir -p /tmp/omegaflow-quickstart-demo
        cd /tmp/omegaflow-quickstart-demo
        studio action=bootstrap
      output:
        mode: fake
        text: |
          workspace recordings
          created recordings/config.yaml
          created recordings/quickstart/omegaflow.md
          created recordings/quickstart/scripts/hello.sh

          next    studio recording=quickstart action=build
      expect:
        file_exists:
        - /tmp/omegaflow-quickstart-demo/recordings/config.yaml
        - /tmp/omegaflow-quickstart-demo/recordings/quickstart/omegaflow.md
        - /tmp/omegaflow-quickstart-demo/recordings/quickstart/scripts/hello.sh
    - run: find /tmp/omegaflow-quickstart-demo/recordings -maxdepth 4 -type f | sort
      display: find recordings -maxdepth 4 -type f | sort
      output:
        mode: fake
        text: |
          recordings/config.yaml
          recordings/quickstart/omegaflow.md
          recordings/quickstart/scripts/hello.sh
    - run: sed -n '1,85p' /tmp/omegaflow-quickstart-demo/recordings/quickstart/omegaflow.md
      display: sed -n '1,85p' recordings/quickstart/omegaflow.md
  guide:
    commands:
    - studio action=bootstrap
    - sed -n '1,85p' recordings/quickstart/omegaflow.md
    success_hint: Bootstrap writes config, Markdown, and a local support script.
```

```yaml studio-directive
beat:
  id: build
  heading: Build The Video
  narration: >-
    Build records the terminal cast, retimes it for playback, checks alignment,
    and writes the configured publish surface.
  marker: build
  caption: Build the generated quickstart recording.
  actions:
  - commands:
    - run: bash recordings/quickstart-demo/scripts/build-demo-project.sh
      display: studio recording=quickstart action=build
      output:
        mode: fake
        text: |
          pass wrote retimed cast: recordings/.omegaflow/videos/quickstart.retimed.cast
          pass wrote publish surface: recordings/.omegaflow/videos/quickstart.html
      expect:
        file_exists:
        - /tmp/omegaflow-quickstart-demo/recordings/.omegaflow/videos/quickstart.retimed.cast
        - /tmp/omegaflow-quickstart-demo/recordings/.omegaflow/videos/quickstart.html
  guide:
    commands:
    - studio recording=quickstart action=build
    success_hint: The build writes a retimed cast and standalone HTML surface.
```

```yaml studio-directive
beat:
  id: play
  heading: Play It In The Terminal
  narration: >-
    The play action replays the retimed asciinema cast directly in the terminal,
    so the recording can be reviewed without opening a browser.
  marker: play
  caption: Play the generated cast in the terminal.
  actions:
  - commands:
    - run: bash recordings/quickstart-demo/scripts/play-demo-project.sh
      display: studio recording=quickstart action=play
      output:
        mode: fake
        text: |
          hello from quickstart
      expect:
        output_contains:
        - hello from quickstart
  guide:
    commands:
    - studio recording=quickstart action=play
    success_hint: The terminal should replay the generated cast.
```

```yaml studio-directive
beat:
  id: publish
  heading: Inspect Publish Surfaces
  narration: >-
    A dry run shows the build plan and publish surfaces without recording a new
    cast. The generated quickstart starts with standalone HTML.
  marker: publish
  caption: Inspect the configured publish surface.
  actions:
  - commands:
    - run: bash recordings/quickstart-demo/scripts/inspect-demo-project.sh
      display: studio recording=quickstart action=build dry_run=true
      expect:
        output_contains:
        - "Publish surfaces:"
        - "type: standalone_html"
  guide:
    commands:
    - studio recording=quickstart action=build dry_run=true
    success_hint: The dry run lists the configured publish surface.
```
