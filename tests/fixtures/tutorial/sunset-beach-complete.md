---
id: sunset-beach
title: Refine a Sunset Beach Poster
environment:
  working_directory: .
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
  enabled: false
setup:
- name: start Tiny Canvas
  run: >-
    python recordings/sunset-beach/scripts/reset_artwork.py;
    export TINY_CANVAS_URL=http://127.0.0.1:18476;
    python recordings/sunset-beach/app/server.py --port 18476
    > recordings/.omegaflow/tutorial/sunset-beach/server.log 2>&1 &
    export TINY_CANVAS_PID=$!;
    for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
      if grep -q "Tiny Canvas ready" recordings/.omegaflow/tutorial/sunset-beach/server.log; then
        break;
      fi;
      sleep 0.1;
    done;
    grep -q "Tiny Canvas ready" recordings/.omegaflow/tutorial/sunset-beach/server.log
cleanup:
- name: stop Tiny Canvas
  run: >-
    kill "$TINY_CANVAS_PID" 2>/dev/null || true;
    wait "$TINY_CANVAS_PID" 2>/dev/null || true
publish:
  default: html
  surfaces:
    html:
      type: standalone_html
      file: ${outputs.asset_dir}/index.html
---

# Refine a Sunset Beach Poster

```yaml studio-directive
scene: Refine a Sunset Beach Poster
```

```yaml studio-directive
beat:
  id: open-artwork
  heading: Open The Artwork
  narration: Open the original artwork in Tiny Canvas.
  caption: Launch the editor from the generated terminal beat.
  actions:
  - commands:
    - id: edit-file
      run: python recordings/sunset-beach/scripts/tiny_canvas.py sunset-study.svg
      display: python scripts/tiny_canvas.py sunset-study.svg
      browser_handoff: true
      timing: realtime
      pre_enter_pause: 1.0
      show_prompt_after: false
  guide:
    summary: The generated terminal beat opens Tiny Canvas.
    success_hint: Continue when you are ready to watch the browser edit.
```

```yaml studio-directive
beat:
  id: edit-artwork
  medium: browser
  heading: Edit And Save A Copy
  narration: Rename the artwork, adjust the composition, and save a new file.
  caption: Script one semantic browser workflow.
  actions:
  - id: open-editor
    open_page:
      handoff: edit-file
      ready:
        visible:
          text: Ready
          exact: true
  - id: rename-artwork
    hold_after_ms: 500
    type_text:
      target: {test_id: artwork-title}
      text: Coconut Sunset
      interval_ms: 90
  - id: move-sun
    timing: realtime
    hold_before_ms: 500
    hold_after_ms: 700
    drag:
      from:
        target: {test_id: sun}
        position: {x: 0.5, y: 0.5}
      to:
        target: {test_id: sunset-target}
        position: {x: 0.5, y: 0.5}
  - id: move-tree
    timing: realtime
    hold_before_ms: 500
    hold_after_ms: 700
    drag:
      from:
        target: {test_id: coconut-tree}
        position: {x: 0.5, y: 0.5}
      to:
        target: {test_id: tree-target}
        position: {x: 0.5, y: 0.5}
  - id: save-new-file
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
    summary: The two-beat Tiny Canvas recording is ready.
    success_hint: Build and watch the finished video from the terminal.
```
