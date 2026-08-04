---
title: Refine a Sunset Beach Poster
---

# Refine a Sunset Beach Poster

```yaml studio-directive
config:
  browser:
    base_url: http://127.0.0.1:18476
    viewport: {width: 1280, height: 800}
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
      chrome: {mode: minimal}
      transitions: {default: fade}
  setup:
  - name: restore the Tiny Canvas draft
    run: python recordings/sunset-beach/scripts/reset_artwork.py
  - name: start Tiny Canvas
    run: >-
      export TINY_CANVAS_URL=http://127.0.0.1:18476;
      export TINY_CANVAS_LOG="$OMEGAFLOW_RUN_DIR/tiny-canvas.log";
      python recordings/sunset-beach/app/server.py --port 18476
      > "$TINY_CANVAS_LOG" 2>&1 &
      export TINY_CANVAS_PID=$!;
      for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
        grep -q "Tiny Canvas ready" "$TINY_CANVAS_LOG" && break;
        sleep 0.1;
      done;
      grep -q "Tiny Canvas ready" "$TINY_CANVAS_LOG"
  cleanup:
  - name: stop Tiny Canvas
    run: >-
      kill "$TINY_CANVAS_PID" 2>/dev/null || true;
      wait "$TINY_CANVAS_PID" 2>/dev/null || true
```

```yaml studio-directive
beat:
  id: inspect-draft
  medium: terminal
  heading: Inspect The Draft
  caption: Confirm the Tiny Canvas draft before editing it.
  actions:
  - commands:
    - run: python recordings/sunset-beach/scripts/inspect_artwork.py
      display: python scripts/inspect_artwork.py
      expect:
        output_contains:
        - "Title: Sunset Study"
        - "Objects: sun, coconut-tree"
        - "Status: ready"
    - id: open-editor
      run: python recordings/sunset-beach/scripts/tiny_canvas.py sunset-study.svg
      display: python scripts/tiny_canvas.py sunset-study.svg
      browser_handoff: true
      timing: realtime
      show_prompt_after: false
```

```yaml studio-directive
beat:
  id: edit-artwork
  medium: browser
  heading: Edit And Save A Copy
  caption: Script one semantic browser workflow.
  actions:
  - id: open-editor
    open_page:
      handoff: open-editor
      ready:
        visible: {text: Ready, exact: true}
  - id: rename-artwork
    type_text:
      target: {test_id: artwork-title}
      text: Coconut Sunset
      interval_ms: 90
  - id: move-sun
    timing: realtime
    drag:
      from: {target: {test_id: sun}}
      to: {target: {test_id: sunset-target}}
  - id: move-tree
    timing: realtime
    drag:
      from: {target: {test_id: coconut-tree}}
      to: {target: {test_id: tree-target}}
  - id: save-new-file
    click:
      target: {test_id: export-artwork}
  - id: saved-new-file
    wait_for:
      visible: {text: Saved coconut-sunset.svg, exact: true}
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
