---
kind: video
id: browser-drag-demo
title: Semantic Browser Drag
description: Drag an SVG object between semantic targets with visible held-pointer feedback.
environment:
  working_directory: recordings/browser-drag-demo
browser:
  base_url: http://127.0.0.1:18474
  viewport:
    width: 1000
    height: 650
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
- name: start drag demonstration
  run: >-
    rm -f .drag-server-ready drag-server.log;
    python3 scripts/drag_server.py --port 18474 >drag-server.log 2>&1 &
    export DRAG_SERVER_PID=$!;
    for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30; do
      test -f .drag-server-ready && break;
      sleep 0.1;
    done;
    test -f .drag-server-ready
cleanup:
- name: stop drag demonstration
  run: >-
    kill "$DRAG_SERVER_PID" 2>/dev/null || true;
    wait "$DRAG_SERVER_PID" 2>/dev/null || true;
    rm -f .drag-server-ready drag-server.log
---

# Semantic Browser Drag

```yaml studio-directive
scene: Semantic browser drag
```

```yaml studio-directive
beat:
  id: move-sun
  medium: browser
  heading: Move The Sun
  narration: Drag the sun into its new position in the sky.
  viewer_hold: 0.8
  actions:
  - id: open
    open_page:
      url: /
      ready:
        visible:
          text: Canvas ready
          exact: true
  - id: drag-sun
    hold_before_ms: 700
    hold_after_ms: 900
    timing: realtime
    drag:
      from:
        target: {test_id: sun}
        position: {x: 0.5, y: 0.5}
      to:
        target: {test_id: sun-destination}
        position: {x: 0.5, y: 0.5}
  - id: moved
    wait_for:
      visible:
        text: Sun moved
        exact: true
  checks:
  - name: sun reached its destination
    text:
      target: {test_id: status}
      equals: Sun moved
```
