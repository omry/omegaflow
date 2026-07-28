---
kind: video
id: tutorial
title: Build a Tiny Canvas Video
description: >-
  Follow one generated terminal beat into a learner-authored browser edit and
  finish with a guided, rebuildable video.
capture:
  window_size: 90x24
  headless: true
style:
  color: true
  typing: true
outputs:
  dir: website/static/omegaflow-videos
publish:
  default: docusaurus
  surfaces:
    docusaurus:
      type: docusaurus_mdx
      file: website/docs/tutorial/index.md
      placeholder: tutorial
      component: VideoPlayer
browser:
  base_url: http://127.0.0.1:18476
  viewport:
    width: 1280
    height: 800
  context:
    locale: en-US
    timezone: UTC
    color_scheme: dark
    reduced_motion: reduce
presentation:
  guided: true
  pane_chrome:
    style: none
  browser:
    window:
      mode: framed
      theme: kde-breeze
      title: Tiny Canvas
      opening_transition: window-open
    chrome:
      mode: minimal
    transitions:
      default: fade
audio:
  enabled: true
  env: OPENAI_OMEGAFLOW_API_KEY
  voice: marin
setup:
- name: prepare isolated Tiny Canvas workspace
  run: >-
    export TUTORIAL_WALKTHROUGH_ROOT="$OMEGAFLOW_RUN_DIR/tutorial-workspace";
    export TUTORIAL_RECORDING_ROOT="$TUTORIAL_WALKTHROUGH_ROOT/recordings/sunset-beach";
    rm -rf "$TUTORIAL_WALKTHROUGH_ROOT";
    mkdir -p "$TUTORIAL_RECORDING_ROOT";
    cp -R src/omegaflow/tutorial/tiny_canvas/app
    src/omegaflow/tutorial/tiny_canvas/scripts
    "$TUTORIAL_RECORDING_ROOT/";
    python "$TUTORIAL_RECORDING_ROOT/scripts/reset_artwork.py";
    export TINY_CANVAS_URL=http://127.0.0.1:18476;
    python "$TUTORIAL_RECORDING_ROOT/app/server.py" --port 18476
    > "$TUTORIAL_WALKTHROUGH_ROOT/server.log" 2>&1 &
    export TINY_CANVAS_PID=$!;
    for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
      if grep -q "Tiny Canvas ready" "$TUTORIAL_WALKTHROUGH_ROOT/server.log"; then
        break;
      fi;
      sleep 0.1;
    done;
    grep -q "Tiny Canvas ready" "$TUTORIAL_WALKTHROUGH_ROOT/server.log"
cleanup:
- name: stop Tiny Canvas
  run: >-
    kill "$TINY_CANVAS_PID" 2>/dev/null || true;
    wait "$TINY_CANVAS_PID" 2>/dev/null || true
---

# Build a Tiny Canvas Video

This guided walkthrough follows the same terminal-to-browser path as the
written tutorial.

```yaml studio-directive
scene: Build a Tiny Canvas Video
```

```yaml studio-directive
beat:
  id: open-artwork
  heading: Start With The Generated Beat
  narration: >-
    This walkthrough follows the same path as the written tutorial. It runs in
    @guided_mode@ guided mode, which pauses after each beat. Turn Guided mode
    off in the player controls to watch continuously. The tutorial workspace
    begins with a generated terminal beat. @launch@ Run Tiny Canvas with the
    original Sunset Study artwork. The command hands the editor to the browser
    beat you will add.
  caption: Start from the generated terminal beat.
  player:
    highlight:
      control: guided
      start: "@guided_mode@"
  actions:
  - commands:
    - id: edit-file
      run: >-
        python "$TUTORIAL_RECORDING_ROOT/scripts/tiny_canvas.py"
        sunset-study.svg
      display: python scripts/tiny_canvas.py sunset-study.svg
      after: "@launch@"
      browser_handoff: true
      timing: realtime
      pre_enter_pause: 0.8
      show_prompt_after: false
  guide:
    summary: The generated terminal beat opens the real tutorial application.
    success_hint: Continue when you are ready to watch the browser edit.
```

```yaml studio-directive
beat:
  id: edit-artwork
  medium: browser
  heading: Add One Browser Beat
  narration: >-
    The learner writes one complete browser beat and then improves that same
    beat throughout the tutorial. @rename@ Rename the artwork Coconut Sunset.
    @sun@ Move the sun toward the horizon, where the tree partly covers it.
    @tree@ Reposition the coconut tree on the beach, then @save@ save the edited
    artwork as a new file. The finished two-beat video can now be extended with
    narration anchors and this guided checkpoint without authoring another
    complete beat.
  caption: Script one semantic browser workflow and keep refining it.
  actions:
  - id: open-editor
    open_page:
      handoff: edit-file
      ready:
        visible:
          text: Ready
          exact: true
  - id: rename-artwork
    after: "@rename@"
    hold_after_ms: 500
    type_text:
      target: {test_id: artwork-title}
      text: Coconut Sunset
      interval_ms: 90
  - id: move-sun
    after: "@sun@"
    timing: realtime
    hold_before_ms: 400
    hold_after_ms: 700
    drag:
      from:
        target: {test_id: sun}
        position: {x: 0.5, y: 0.5}
      to:
        target: {test_id: sunset-target}
        position: {x: 0.5, y: 0.5}
  - id: move-tree
    after: "@tree@"
    timing: realtime
    hold_before_ms: 400
    hold_after_ms: 700
    drag:
      from:
        target: {test_id: coconut-tree}
        position: {x: 0.5, y: 0.5}
      to:
        target: {test_id: tree-target}
        position: {x: 0.5, y: 0.5}
  - id: save-new-file
    after: "@save@"
    click:
      target: {test_id: export-artwork}
  - id: saved-new-file
    wait_for:
      visible:
        text: Saved coconut-sunset.svg
        exact: true
  checks:
  - name: title retained
    value:
      target: {test_id: artwork-title}
      equals: Coconut Sunset
  - name: new artwork saved
    text:
      target: {test_id: status}
      equals: Saved coconut-sunset.svg
  guide:
    summary: The two-beat Tiny Canvas recording is ready to build and review.
    success_hint: Use the build and watch commands from the written tutorial.
```
