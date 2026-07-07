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
timing:
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
  enabled: true
  env: OPENAI_OMEGAFLOW_API_KEY
  env_file: .env
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
    OmegaFlow turns a Markdown recording script into a rebuildable terminal video.
    It is a Python tool, but the demos it records can be anything you drive in a
    terminal. If your project already has a Python environment, use that. If it
    does not, @python_env@ create a local virtual environment first.
    @wait:env_command+300ms@ Then @install@ install OmegaFlow into that
    environment. @wait:install_command+300ms@ Once it is installed, the
    omegaflow command is available for the rest of the workflow.
  marker: install
  caption: Install the OmegaFlow CLI.
  actions:
  - commands:
    - id: env_command
      run: ":"
      display: |-
        python -m venv .venv
        source .venv/bin/activate
      after: "@python_env@"
      output:
        mode: fake
        text: ""
    - id: install_command
      run: python -m pip install omegaflow
      display: python -m pip install omegaflow
      after: "@install@"
      output:
        mode: fake
        text: |
          Successfully installed omegaflow-0.2.0
  guide:
    commands:
    - python -m venv .venv
    - source .venv/bin/activate
    - python -m pip install omegaflow
    success_hint: Use an existing Python environment when your project already has one.
```

```yaml studio-directive
beat:
  id: bootstrap
  heading: Bootstrap Quickstart
  narration: >-
    Bootstrap prepares the project for OmegaFlow. @bootstrap@ Run the bootstrap
    command. It prepares your environment for recording videos and creates a
    small demo recording script. @wait:bootstrap_run+300ms@ Look at the
    recording script later, when you are ready to customize it.
  marker: bootstrap
  caption: Run bootstrap from your repository root.
  actions:
  - commands:
    - id: bootstrap_command
      run: ":"
      display: "# From your repository root"
      after: "@bootstrap@"
      post_command_pause: 0.45
      output:
        mode: fake
        text: ""
    - id: bootstrap_run
      run: bash recordings/quickstart-demo/scripts/create-demo-project.sh
      display: omegaflow action=bootstrap
      output:
        mode: fake
        text: |
          workspace recordings
          created .omegaflow/config.yaml
          created recordings/config.yaml
          created recordings/quickstart/index.md
          created recordings/quickstart/scripts/hello.sh

          next    omegaflow recording=quickstart action=build
      expect:
        file_exists:
        - /tmp/omegaflow-quickstart-demo/.omegaflow/config.yaml
        - /tmp/omegaflow-quickstart-demo/recordings/config.yaml
        - /tmp/omegaflow-quickstart-demo/recordings/quickstart/index.md
        - /tmp/omegaflow-quickstart-demo/recordings/quickstart/scripts/hello.sh
  guide:
    commands:
    - omegaflow action=bootstrap
    success_hint: Look at the generated recording script later when you are ready to customize it.
```

```yaml studio-directive
beat:
  id: build
  heading: Build The Video
  narration: >-
    Now build the generated recording. @build@ Run the build command.
    OmegaFlow records the terminal and turns it into a video.
    @wait:build_command+300ms@
  marker: build
  caption: Build the generated quickstart recording.
  actions:
  - commands:
    - id: build_command
      run: bash recordings/quickstart-demo/scripts/build-demo-project.sh
      display: omegaflow recording=quickstart action=build
      after: "@build@"
      timing: realtime
      expect:
        file_exists:
        - /tmp/omegaflow-quickstart-demo/recordings/.omegaflow/videos/quickstart.retimed.cast
        - /tmp/omegaflow-quickstart-demo/recordings/.omegaflow/videos/quickstart.html
  guide:
    commands:
    - omegaflow recording=quickstart action=build
    success_hint: The generated video is ready to play.
```

```yaml studio-directive
beat:
  id: play
  heading: Play It In The Terminal
  narration: >-
    Review the result without opening a browser. @play@ The play action replays
    the retimed asciinema cast directly in the terminal.
    The generated video is now ready to inspect or publish.
    @wait:play_command+300ms@
  marker: play
  caption: Play the generated cast in the terminal.
  actions:
  - commands:
    - id: play_command
      run: bash recordings/quickstart-demo/scripts/play-demo-project.sh
      display: omegaflow recording=quickstart action=play
      after: "@play@"
      output:
        mode: fake
        text: |
          hello from quickstart
      expect:
        output_contains:
        - hello from quickstart
  guide:
    commands:
    - omegaflow recording=quickstart action=play
    success_hint: The terminal should replay the generated cast.
```

```yaml studio-directive
beat:
  id: publish
  heading: Inspect Publish Surfaces
  narration: >-
    Finally, inspect the publish surface. @inspect@ A dry run shows where the
    finished video can go without recording it again. The generated quickstart
    starts with standalone HTML, and other recordings can publish into docs pages.
    @wait:inspect_command+300ms@
  marker: publish
  caption: Inspect the configured publish surface.
  actions:
  - commands:
    - id: inspect_command
      run: bash recordings/quickstart-demo/scripts/inspect-demo-project.sh
      display: omegaflow recording=quickstart action=build dry_run=true
      after: "@inspect@"
      output:
        mode: fake
        text: |
          Publish surfaces:
            html:
              type: standalone_html
              file: recordings/.omegaflow/videos/quickstart.html
      expect:
        output_contains:
        - "Publish surfaces:"
        - "type: standalone_html"
  guide:
    commands:
    - omegaflow recording=quickstart action=build dry_run=true
    success_hint: The dry run lists the configured publish surface.
```
