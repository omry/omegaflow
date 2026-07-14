---
id: quickstart-demo
title: Quickstart Demo
capture:
  window_size: 90x24
  headless: true
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
  path_prepend:
  - recordings/quickstart-demo/bin
browser:
  base_url: http://127.0.0.1:18474
  viewport:
    width: 1280
    height: 720
  context:
    locale: en-US
    timezone: UTC
    color_scheme: dark
    reduced_motion: reduce
presentation:
  browser:
    window:
      mode: framed
      theme: kde-breeze
      title: OmegaFlow Player
      opening_transition: window-open
    chrome:
      mode: full
    transitions:
      default: cut
audio:
  enabled: true
  env: OPENAI_OMEGAFLOW_API_KEY
  env_file: .env
setup:
- name: start the generated-player preview server
  run_file: scripts/start-demo-server.sh
cleanup:
- name: stop the generated-player preview server
  run_file: scripts/stop-demo-server.sh
- name: remove demo project
  run_file: scripts/cleanup-demo-project.sh
---

# Quickstart Demo

This is the short homepage demo. It creates and builds a terminal quickstart,
then switches to a scripted browser beat that operates the real generated
player.

```yaml studio-directive
scene: Quickstart Demo
```

```yaml studio-directive
beat:
  id: install
  heading: Install OmegaFlow
  narration: >-
    OmegaFlow turns scripted terminal and browser workflows into narrated,
    rebuildable videos. Start by @install@ adding the package to your project's
    Python environment. @wait:install_command+200ms@ When installation
    finishes, you can run omegaflow from the command line.
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
    Next, from your repository root, @bootstrap@ run bootstrap to set up the
    recording workspace. @wait:bootstrap_run+200ms@ The command creates the
    project settings, recording defaults, and workspace layout. The included
    quickstart is only a small example of that structure.
  marker: bootstrap
  caption: Run bootstrap from your repository root.
  actions:
  - commands:
    - id: bootstrap_run
      run_file: scripts/create-demo-project.sh
      display: omegaflow action=bootstrap
      after: "@bootstrap@"
      pre_command_pause: 0.45
  guide:
    commands:
    - omegaflow action=bootstrap
    success_hint: Inspect and commit the generated recording workspace.
```

```yaml studio-directive
beat:
  id: build
  heading: Build The Video
  narration_take: build-and-browser
  narration: >-
    Then, @build@ build the sample recording. OmegaFlow runs the scripted
    workflow and creates a ready-to-watch player.
    @wait:build_command+200ms@ When the build finishes, the command shows where
    to find the player.
  marker: build
  caption: Build the generated quickstart recording.
  actions:
  - commands:
    - id: build_command
      run_file: scripts/build-demo-project.sh
      display: omegaflow recording=quickstart
      after: "@build@"
      timing: realtime
  guide:
    commands:
    - omegaflow recording=quickstart
    success_hint: The generated video is ready to play.
```

```yaml studio-directive
beat:
  id: play-in-browser
  medium: browser
  heading: Play It In The Browser
  narration_take: build-and-browser
  narration: >-
    The narration continues as the recording moves from the terminal to the
    browser. @open_player@ The browser script opens the player created by the
    build. @wait:open_player+300ms@ Next, @play@ the script starts playback and
    captures the result. A single narration take can span both recording types,
    keeping commands and browser actions synchronized with the voiceover. To
    learn more, start the tutorial or read the docs.
  marker: play-in-browser
  caption: Script browser interaction with the generated player.
  actions:
  - id: open_player
    after: "@open_player@"
    open_page:
      url: /cast-player.html?manifest=/quickstart/presentation/recording.presentation.json&title=Quickstart
      display_url: https://omegaflow.dev/videos/quickstart
      ready:
        visible:
          role: button
          name: Play
          exact: true
  - id: play
    after: "@play@"
    click:
      target:
        role: button
        name: Play
        exact: true
  guide:
    success_hint: The generated player is ready to publish with your docs.
```
