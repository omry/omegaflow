---
kind: video
id: browser-audio-demo
title: Realtime Browser Audio
description: Capture nested OmegaFlow playback as one synchronized browser clip.
browser:
  viewport:
    width: 960
    height: 640
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
      title: Nested OmegaFlow Player
    chrome:
      mode: minimal
    transitions:
      default: cut
audio:
  enabled: true
  voice: marin
---

# Realtime Browser Audio

This implementation demonstration records a short narrated OmegaFlow player
inside a browser beat. The outer and inner narrators intentionally use
different voices.

```yaml studio-directive
scene: Realtime browser audio
```

```yaml studio-directive
beat:
  id: prepare-inner-player
  heading: Prepare The Inner Player
  narration: >-
    First, build and open the short inner video in its local player.
  actions:
  - commands:
    - id: build_inner
      run: >-
        omegaflow recording=browser-audio-demo/inner action=build force=true
      with_env:
      - OPENAI_OMEGAFLOW_API_KEY
      timing: realtime
    - id: watch_inner
      run: >-
        omegaflow recording=browser-audio-demo/inner action=watch
        watch_port=18476 autoplay=false
      display: >-
        omegaflow recording=browser-audio-demo/inner action=watch
      browser_handoff: true
      timing: realtime
      show_prompt_after: false
```

```yaml studio-directive
beat:
  id: capture-inner-playback
  medium: browser
  heading: Capture Inner Playback
  narration: >-
    The browser action now plays the inner video and captures its page audio
    with the same realtime fragment. @play_inner@ @wait:play_inner@ When the
    inner player finishes, the outer narration resumes.
  pointer:
    visible: false
  actions:
  - id: open_inner
    open_page:
      handoff: watch_inner
      display_url: $handoff
      ready:
        visible:
          role: button
          name: Play
          exact: true
  - id: play_inner
    after: "@play_inner@"
    timing: realtime
    audio: capture
    click:
      target:
        role: button
        name: Play
        exact: true
    until:
      visible:
        role: button
        name: Play again
        exact: true
      timeout_ms: 15000
  checks:
  - name: inner playback completed
    visible:
      role: button
      name: Play again
      exact: true
```
