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
  narration: Open the original artwork in Tiny Canvas.
  caption: Launch the editor from the terminal.
  layout:
    areas:
    - [terminal]
  panes:
    terminal:
    - id: launch-editor
      actions:
      - id: edit-file
        run: python recordings/sunset-beach/scripts/tiny_canvas.py sunset-study.svg
        display: python scripts/tiny_canvas.py sunset-study.svg
        browser_handoff: {target: after}
        timing: realtime
        pre_enter_pause: 1.0
```

```yaml studio-directive
beat:
  id: edit-artwork
  heading: Edit And Save A Copy
  narration: Rename the artwork, adjust the composition, and save the result as a new file.
  caption: The original remains unchanged.
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
        hold_after_ms: 500
        fill:
          target: {test_id: artwork-title}
          text: Coconut Sunset
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
```

```yaml studio-directive
beat:
  id: compare-files
  heading: Compare Before And After
  narration: Open the original on the left, then the newly saved file on the right.
  caption: Both browser panes display the SVG files directly.
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
        run: python recordings/sunset-beach/scripts/tiny_canvas.py --view sunset-study.svg
        display: python scripts/tiny_canvas.py --view sunset-study.svg
        browser_handoff: {target: before}
        timing: realtime
        pre_command_pause: 0.6
        pre_enter_pause: 1.0
    - id: open-saved
      actions:
      - id: open-saved
        run: python recordings/sunset-beach/scripts/tiny_canvas.py --view coconut-sunset.svg
        display: python scripts/tiny_canvas.py --view coconut-sunset.svg
        browser_handoff: {target: after}
        timing: realtime
        pre_command_pause: 0.6
        pre_enter_pause: 1.0
```
