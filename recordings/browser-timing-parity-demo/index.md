---
kind: video
id: browser-timing-parity-demo
title: Browser Timing Parity
description: Compare presentation-timed browser state with a captured realtime interval.
environment:
  working_directory: recordings/browser-timing-parity-demo
browser:
  base_url: http://127.0.0.1:18475
  viewport:
    width: 720
    height: 520
  context:
    locale: en-US
    timezone: UTC
    color_scheme: dark
    reduced_motion: no-preference
presentation:
  browser:
    window:
      mode: none
    chrome:
      mode: hidden
    transitions:
      default: cut
audio:
  enabled: false
setup:
- name: start timing demonstration
  run: >-
    rm -f .timing-server-ready timing-server.log;
    python3 scripts/timing_server.py --port 18475 >timing-server.log 2>&1 &
    export TIMING_SERVER_PID=$!;
    for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30; do
      test -f .timing-server-ready && break;
      sleep 0.1;
    done;
    test -f .timing-server-ready
cleanup:
- name: stop timing demonstration
  run: >-
    kill "$TIMING_SERVER_PID" 2>/dev/null || true;
    wait "$TIMING_SERVER_PID" 2>/dev/null || true;
    rm -f .timing-server-ready timing-server.log
---

# Browser Timing Parity

```yaml studio-directive
scene: Browser timing parity
panes:
- id: presentation
  kind: browser
  title: Presentation timing
- id: realtime
  kind: browser
  title: Realtime timing
```

```yaml studio-directive
beat:
  id: compare-browser-timing
  heading: Presentation And Realtime Browser Actions
  narration: Compare presentation timing on the left with realtime timing on the right.
  layout:
    areas:
    - [presentation, realtime]
  panes:
    presentation:
    - id: presentation-action
      actions:
      - id: open-presentation
        open_page:
          url: /?mode=presentation
          ready:
            visible:
              text: Presentation ready
              exact: true
      - id: complete-presentation
        timing: presentation
        hold_after_ms: 3000
        click:
          target: {test_id: start}
      checks:
      - name: presentation action completed
        visible: {test_id: complete}
    realtime:
    - id: realtime-action
      actions:
      - id: open-realtime
        open_page:
          url: /?mode=realtime
          ready:
            visible:
              text: Realtime ready
              exact: true
      - id: complete-realtime
        timing: realtime
        click:
          target: {test_id: start}
        until:
          visible: {test_id: complete}
          timeout_ms: 5000
      checks:
      - name: realtime action completed
        visible: {test_id: complete}
```
