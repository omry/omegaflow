---
id: sunset-beach
title: Refine a Sunset Beach Poster
publish:
  default: html
  surfaces:
    html:
      type: standalone_html
      file: ${outputs.asset_dir}/index.html
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
setup:
- name: start Tiny Canvas
  run: >-
    export TINY_CANVAS_URL=http://127.0.0.1:18476;
    export TINY_CANVAS_LOG="$OMEGAFLOW_RUN_DIR/tiny-canvas.log";
    python {{ tutorial_path }}/app/server.py --port 18476
    > "$TINY_CANVAS_LOG" 2>&1 &
    export TINY_CANVAS_PID=$!;
    for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
      if grep -q "Tiny Canvas ready" "$TINY_CANVAS_LOG"; then
        break;
      fi;
      sleep 0.1;
    done;
    grep -q "Tiny Canvas ready" "$TINY_CANVAS_LOG"
cleanup:
- name: stop Tiny Canvas
  run: >-
    kill "$TINY_CANVAS_PID" 2>/dev/null || true;
    wait "$TINY_CANVAS_PID" 2>/dev/null || true
---

# Refine a Sunset Beach Poster

This cumulative tutorial recording begins with one generated terminal beat.
Later milestones add one browser beat, then extend that same beat with
narration and guidance.

```yaml studio-directive
scene: Refine a Sunset Beach Poster
```

```yaml studio-directive
beat:
  id: inspect-draft
  heading: Inspect The Draft
  narration: Inspect the supplied artwork and confirm its known starting state.
  caption: Confirm the Tiny Canvas draft before editing it.
  actions:
  - commands:
    - run: python {{ tutorial_path }}/scripts/reset_artwork.py
      display: python scripts/reset_artwork.py
      expect:
        output_contains:
        - Restored the Tiny Canvas draft.
    - run: python {{ tutorial_path }}/scripts/inspect_artwork.py
      display: python scripts/inspect_artwork.py
      expect:
        output_contains:
        - "Title: Sunset Study"
        - "Objects: sun, coconut-tree"
        - "Status: ready"
```
