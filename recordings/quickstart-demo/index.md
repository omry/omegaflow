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
  dir: website/static/omegaflow-videos
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
  working_directory: /tmp
  path_prepend:
  - recordings/quickstart-demo/bin
audio:
  enabled: true
  env: OPENAI_OMEGAFLOW_API_KEY
  env_file: .env
setup:
- name: prepare clean demo project
  run: |-
    rm -rf /tmp/omegaflow-quickstart-demo
    mkdir -p /tmp/omegaflow-quickstart-demo
    cd /tmp/omegaflow-quickstart-demo
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
  heading: Install OmegaFlow
  narration: >-
    OmegaFlow turns scripted terminal workflows into rebuildable videos with
    generated voiceover. @install@ Install it in your project's Python
    environment. @wait:install_command+200ms@ The omegaflow command is now
    ready.
  marker: install
  caption: Install OmegaFlow in a Python environment.
  actions:
  - commands:
    # The homepage video is built from the current checkout, which may be an
    # unreleased version that cannot yet be installed from PyPI. The PATH
    # wrapper verifies that this checkout imports, while replacement output
    # shows the public installation result users should expect after release.
    # It does not claim to validate the published package.
    - id: install_command
      run: python -m pip install omegaflow
      display: python -m pip install omegaflow
      after: "@install@"
      output:
        replace: |
          Successfully installed omegaflow
  guide:
    commands:
    - python -m pip install omegaflow
    success_hint: Install OmegaFlow in your project's Python environment.
```

```yaml studio-directive
beat:
  id: bootstrap
  heading: Bootstrap Quickstart
  narration: >-
    From your repository root, @bootstrap@ run bootstrap once.
    @wait:bootstrap_run+200ms@ It creates the recording workspace and a small
    quickstart example you can keep with your code.
  marker: bootstrap
  caption: Run bootstrap from your repository root.
  actions:
  - commands:
    - id: bootstrap_run
      run_file: scripts/create-demo-project.sh
      display: omegaflow action=bootstrap
      after: "@bootstrap@"
      pre_command_pause: 0.45
      expect:
        file_exists:
        - /tmp/omegaflow-quickstart-demo/.omegaflow/config.yaml
        - /tmp/omegaflow-quickstart-demo/recordings/config.yaml
        - /tmp/omegaflow-quickstart-demo/recordings/quickstart/index.md
        - /tmp/omegaflow-quickstart-demo/recordings/quickstart/scripts/hello.sh
  guide:
    commands:
    - omegaflow action=bootstrap
    success_hint: Inspect and commit the generated recording workspace.
```

```yaml studio-directive
beat:
  id: build
  heading: Build The Video
  narration: >-
    @build@ Build the quickstart recording. OmegaFlow runs the scripted
    workflow, synchronizes it with the narration, and writes a ready-to-watch
    video. @wait:build_command+200ms@ The output shows where it was published.
  marker: build
  caption: Build the generated quickstart recording.
  actions:
  - commands:
    - id: build_command
      run_file: scripts/build-demo-project.sh
      display: omegaflow recording=quickstart
      after: "@build@"
      timing: realtime
      expect:
        file_exists:
        - /tmp/omegaflow-quickstart-demo/recordings/.omegaflow/videos/quickstart/recording.retimed.cast
        - /tmp/omegaflow-quickstart-demo/recordings/.omegaflow/videos/quickstart/index.html
  guide:
    commands:
    - omegaflow recording=quickstart
    success_hint: The generated video is ready to play.
```

```yaml studio-directive
beat:
  id: view-and-publish
  heading: Watch The Result
  narration: >-
    To review the result, @watch@ run the watch command. OmegaFlow opens the
    recording in your browser exactly as viewers will see it.
    @wait:watch_command+200ms@ Keep the script with your code, rebuild it when
    the workflow changes, and publish the video with your docs.
  marker: view-and-publish
  caption: Watch the generated video in your browser.
  actions:
  - commands:
    # This recording is captured in a headless terminal session, so launching
    # the browser would be an external GUI side effect that the cast cannot
    # show. Bootstrap and build above are real; only this browser-launch action
    # and its concise confirmation are staged for the terminal-only demo.
    - id: watch_command
      run: ":"
      display: omegaflow recording=quickstart action=watch
      after: "@watch@"
      output:
        replace: |
          pass  opened quickstart recording in browser
  guide:
    commands:
    - omegaflow recording=quickstart action=watch
    success_hint: Publish the generated video alongside your documentation.
```
