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
bootstrap command creating the `quickstart` recording, builds that generated
recording, then points to browser viewing and publishing options.

```yaml studio-directive
scene: Quickstart Demo
```

```yaml studio-directive
beat:
  id: install
  heading: Install The CLI
  narration: >-
    OmegaFlow turns a Markdown recording script into a rebuildable terminal video
    with generated voiceover. It is a Python tool, and it can record any
    terminal workflow. If your project does not already have a Python
    environment, @python_env@ create one now.
    @wait:env_command+300ms@ Then @install@ install OmegaFlow.
    @wait:install_command+300ms@ The omegaflow command is now ready for the
    rest of the workflow.
  marker: install
  caption: Install OmegaFlow in a Python environment.
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
          Successfully installed omegaflow-0.3.0
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
    From your repository root, @bootstrap@ run bootstrap. It prepares the
    project for recording videos and creates a small demo recording script.
    @wait:bootstrap_run+300ms@ You can inspect that script later, when you are
    ready to customize it.
  marker: bootstrap
  caption: Run bootstrap from your repository root.
  actions:
  - commands:
    - id: bootstrap_run
      run: bash recordings/quickstart-demo/scripts/create-demo-project.sh
      display: omegaflow action=bootstrap
      after: "@bootstrap@"
      pre_command_pause: 0.45
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
    @build@ Run the OmegaFlow build command to generate the recording.
    OmegaFlow records the script, retimes the cast, and packages the browser
    player. @wait:build_command+300ms@
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
  id: view-and-publish
  heading: View And Publish
  narration: >-
    To review the result, @watch@ run action equals watch. OmegaFlow starts a
    local server and opens the recording in your browser when a graphical
    browser is available. If it cannot open the browser, it prints the URL
    instead. @wait:watch_command+300ms@ For publishing, OmegaFlow currently
    supports plain HTML and Docusaurus documentation pages. To learn more, start
    the tutorial or read the docs.
  marker: view-and-publish
  caption: Watch in a browser, then publish to docs.
  actions:
  - commands:
    - id: watch_command
      run: ":"
      display: omegaflow recording=quickstart action=watch
      after: "@watch@"
      output:
        mode: fake
        text: |
          step  watch recording
          pass  serving local watch server: http://127.0.0.1:51234/cast-player.html?...
          info  opened browser; press Ctrl-C to stop
  guide:
    commands:
    - omegaflow recording=quickstart action=watch
    success_hint: Use publish surfaces when the video should live with docs.
```
