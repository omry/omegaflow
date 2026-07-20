---
kind: video
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
  viewport:
    width: 1152
    height: 360
  context:
    locale: en-US
    timezone: UTC
    color_scheme: dark
    reduced_motion: reduce
presentation:
  guided: true
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
cleanup:
- name: remove demo project
  run_file: scripts/cleanup-demo-project.sh
---

# Quickstart Demo

This is the short homepage demo. It creates and builds a terminal quickstart,
then switches to a browser beat that operates the real generated
player.

```yaml studio-directive
scene: Quickstart Demo
```

```yaml studio-directive
beat:
  id: introduction
  heading: What This Video Covers
  narration: >-
    OmegaFlow turns scripted terminal and browser workflows into narrated,
    rebuildable videos. OmegaFlow videos are organized into beats. This video
    is a quickstart demo. We'll install OmegaFlow, prepare a recording
    workspace, then build and open a two-beat quickstart video in a browser.
    The demo runs in @guided_mode_start@ guided mode, which pauses after each
    beat. To watch continuously, turn off Guided mode using the button in the
    player controls.
  caption: Preview the quickstart and learn how guided mode works.
  player:
    highlight:
      control: guided
      start: "@guided_mode_start@"
  guide:
    success_hint: Continue when you are ready to install OmegaFlow.
```

```yaml studio-directive
beat:
  id: install
  heading: Install OmegaFlow
  narration: >-
    Start by @install@ adding OmegaFlow to your project's Python environment.
    @wait:install_command+200ms@ When installation finishes, you can run
    omegaflow from the command line.
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
    We'll now prepare your recording workspace. This is a one-time setup for
    each recording environment, and you can commit the generated files to
    version control. From your repository root, @bootstrap@ run the bootstrap
    command. @wait:bootstrap_run+200ms@ It creates the @project_settings_start@
    project settings, @project_settings_end@ @recording_defaults_start@
    recording defaults, @recording_defaults_end@ and @quickstart_script_start@
    a quickstart video script you can run immediately. @quickstart_script_end@
  marker: bootstrap
  caption: Run bootstrap from your repository root.
  actions:
  - commands:
    - id: bootstrap_run
      run_file: scripts/create-demo-project.sh
      display: omegaflow action=bootstrap
      after: "@bootstrap@"
      pre_command_pause: 0.45
  effects:
  - highlight:
      text: .omegaflow/config.yaml
      start: "@project_settings_start@"
      end: "@project_settings_end@"
  - highlight:
      text: recordings/config.yaml
      start: "@recording_defaults_start@"
      end: "@recording_defaults_end@"
  - highlight:
      text: recordings/quickstart/index.md
      start: "@quickstart_script_start@"
      end: "@quickstart_script_end@"
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
    @build@ Build the quickstart recording to turn the sample workflow into a
    ready-to-watch two-beat video.
    @wait:build_command+200ms@ When the build finishes, @watch@ run the
    follow-up watch command to open the video in a browser.
  marker: build
  caption: Build the generated quickstart recording.
  actions:
  - commands:
    - id: build_command
      run_file: scripts/build-demo-project.sh
      display: omegaflow recording=quickstart action=build
      after: "@build@"
      timing: realtime
    - id: watch_command
      # Keep the captured URL stable across homepage-video rebuilds.
      run: omegaflow recording=quickstart action=watch watch_port=43123 autoplay=false
      display: omegaflow recording=quickstart action=watch
      after: "@watch@"
      pre_command_pause: 0.45
      browser_handoff: true
      timing: realtime
      show_prompt_after: false
  guide:
    commands:
    - omegaflow recording=quickstart action=build
    success_hint: The generated video is ready to play.
```

```yaml studio-directive
beat:
  id: play-in-browser
  medium: browser
  heading: Explore The Player
  narration_take: build-and-browser
  narration: >-
    @open_player@ An OmegaFlow video can move from terminal beats into browser
    beats. Here, OmegaFlow scripts and records browser workflows just as it does
    terminal workflows. The watch command opens the generated player in a
    browser, where this script opens the two-beat video we just created.
    @show_pointer@ This quickstart has two beats, titled @navigate_section@
    First Video Beat and @playback_section@ Second Video Beat. The player lets
    you preview a beat by hovering over it in the timeline. @point_at_speed@
    You can also use the
    @playback_speed_start@ playback speed control. @playback_speed_end@
    To learn more, start the tutorial or read the docs.
  marker: play-in-browser
  caption: Script browser interaction with the generated player.
  pointer:
    visible: false
  actions:
  - id: open_player
    after: "@open_player@"
    hold_before_ms: 350
    open_page:
      handoff: watch_command
      display_url: $handoff
      ready:
        visible:
          role: button
          name: Play
          exact: true
  - id: show_pointer
    after: "@show_pointer@"
    set_pointer:
      visible: true
  - id: preview_navigation_section
    after: "@navigate_section@"
    hold_after_ms: 600
    move_pointer:
      target:
        test_id: section-region-first-video-beat
      position: {x: 0.5, y: 0.5}
  - id: preview_playback_section
    after: "@playback_section@"
    hold_after_ms: 600
    move_pointer:
      target:
        test_id: section-region-second-video-beat
      position: {x: 0.5, y: 0.5}
  - id: point_at_speed
    after: "@point_at_speed@"
    hold_before_ms: 350
    move_pointer:
      target: &speed_control
        role: button
        name: Playback speed
  - id: increase_speed
    after: "@playback_speed_start@"
    hold_after_ms: 600
    click:
      target: *speed_control
  - id: restore_speed
    after: "@playback_speed_end@"
    click:
      target: *speed_control
      button: right
  - id: hide_pointer
    set_pointer:
      visible: false
  guide:
    success_hint: The generated player is ready to publish with your docs.
```
