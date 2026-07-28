---
kind: video
id: tutorial
title: Build a Tiny Canvas Video
description: >-
  Follow a terminal-to-browser OmegaFlow workflow from its first command to a
  side-by-side visual result.
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
      file: website/docs/tutorial/overview.md
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
  guided: false
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

This continuous walkthrough supports the written tutorial with the completed
Tiny Canvas workflow.

```yaml studio-directive
scene: Build a Tiny Canvas Video
panes:
- id: before
  kind: browser
  title: Before
- id: after
  kind: browser
  title: After
- id: terminal
  kind: terminal
  title: Tiny Canvas workflow
```

```yaml studio-directive
beat:
  id: launch-editor
  heading: Open The Artwork
  narration: >-
    OmegaFlow organizes a video into beats. In this first terminal beat,
    @launch@ run Tiny Canvas with the original Sunset Study artwork.
    The command opens the editor in the browser for the next beat.
  caption: Launch Tiny Canvas from a terminal beat.
  layout:
    areas:
    - [terminal]
  panes:
    terminal:
    - id: launch-editor
      actions:
      - id: edit-file
        run: >-
          python "$TUTORIAL_RECORDING_ROOT/scripts/tiny_canvas.py"
          sunset-study.svg
        display: python scripts/tiny_canvas.py sunset-study.svg
        after: voiceover.launch.started
        browser_handoff: {target: after}
        timing: realtime
        pre_enter_pause: 0.8
```

```yaml studio-directive
beat:
  id: edit-artwork
  heading: Script The Browser Edit
  narration: >-
    The second beat scripts the browser through semantic page elements.
    @rename@ Rename the artwork Coconut Sunset.
    @sun@ Drag the sun toward the horizon, where the tree partly covers it.
    @tree@ Reposition the coconut tree, then @save@ save the edited artwork as
    a new, title-derived file.
  caption: Target meaningful controls and artwork objects.
  layout:
    areas:
    - [after]
  panes:
    after:
    - id: editor
      actions:
      - id: open-editor
        open_page:
          handoff: edit-file
          ready:
            visible:
              text: Ready
              exact: true
      - id: rename-artwork
        after: voiceover.rename.started
        hold_after_ms: 500
        type_text:
          target: {test_id: artwork-title}
          text: Coconut Sunset
          interval_ms: 90
      - id: move-sun
        after: voiceover.sun.started
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
        after: voiceover.tree.started
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
        after: voiceover.save.started
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
```

```yaml studio-directive
beat:
  id: compare-files
  heading: Compare Before And After
  narration: >-
    A beat can compose more than one pane. In the final layout, the terminal
    remains visible below two browser panes. @original@ Open the untouched
    Sunset Study on the left. @edited@ Then open Coconut Sunset on the right
    to compare the saved result.
  caption: Combine terminal and browser panes in one beat.
  layout:
    areas:
    - [before, after]
    - [before, after]
    - [terminal, terminal]
  panes:
    before:
    - id: original-file
      actions:
      - id: show-original
        open_page:
          handoff: open-original
          ready:
            response:
              contains: /files/sunset-study.svg
              status: 200
    after:
    - id: saved-file
      actions:
      - id: show-saved
        open_page:
          handoff: open-saved
          ready:
            response:
              contains: /files/coconut-sunset.svg
              status: 200
    terminal:
    - id: open-original
      actions:
      - id: open-original
        run: >-
          python "$TUTORIAL_RECORDING_ROOT/scripts/tiny_canvas.py"
          --view sunset-study.svg
        display: python scripts/tiny_canvas.py --view sunset-study.svg
        after: voiceover.original.started
        browser_handoff: {target: before}
        timing: realtime
        pre_command_pause: 0.5
        pre_enter_pause: 0.8
    - id: open-saved
      actions:
      - id: open-saved
        run: >-
          python "$TUTORIAL_RECORDING_ROOT/scripts/tiny_canvas.py"
          --view coconut-sunset.svg
        display: python scripts/tiny_canvas.py --view coconut-sunset.svg
        after: voiceover.edited.started
        browser_handoff: {target: after}
        timing: realtime
        pre_command_pause: 0.5
        pre_enter_pause: 0.8
  guide:
    summary: Compare the original and edited artwork.
    commands:
    - python scripts/tiny_canvas.py --view sunset-study.svg
    - python scripts/tiny_canvas.py --view coconut-sunset.svg
    success_hint: Both SVG files are open in their browser panes.
```
