---
kind: video
id: browser-recording-narration-smoke
title: Browser Recording Narration Smoke Test
outputs:
  dir: website/static/omegaflow-videos
environment:
  working_directory: recordings/browser-recording-reference
browser:
  base_url: http://127.0.0.1:18473
  viewport:
    width: 1280
    height: 720
  context:
    locale: en-US
    timezone: UTC
    color_scheme: light
    reduced_motion: reduce
presentation:
  browser:
    window:
      mode: framed
      theme: kde-breeze
      title: OmegaFlow Narration Smoke Test
      opening_transition: window-open
    chrome:
      mode: minimal
    transitions:
      default: fade
audio:
  enabled: true
  env: OPENAI_OMEGAFLOW_API_KEY
  env_file: .env
publish:
  default: html
  surfaces:
    html:
      type: standalone_html
      file: ${outputs.asset_dir}/index.html
setup:
- name: start local reference application
  run: >-
    rm -f .reference-server-ready reference-state.json;
    python scripts/reference_server.py --port 18473 >reference-server.log 2>&1 &
    export REFERENCE_SERVER_PID=$!;
    for attempt in 1 2 3 4 5 6 7 8 9 10; do
      test -f .reference-server-ready && break;
      sleep 0.1;
    done;
    test -f .reference-server-ready
cleanup:
- name: stop local reference application
  run: >-
    kill "$REFERENCE_SERVER_PID" 2>/dev/null || true;
    wait "$REFERENCE_SERVER_PID" 2>/dev/null || true;
    rm -f .reference-server-ready reference-state.json reference-server.log
---

# Browser Recording Narration Smoke Test

This opt-in recording verifies narration continuity across a terminal-to-browser
beat boundary. It is separate from the API-free reference fixture so automated
capture remains deterministic and does not require credentials.

```yaml studio-directive
scene: Mixed capture with continuous narration
```

```yaml studio-directive
beat:
  id: prepare
  heading: Prepare application state
  narration_take: terminal-browser-handoff
  narration: First, the terminal prepares application state,
  viewer_hold: 0.4
  actions:
  - run: >-
      python scripts/set_state.py terminal-ready &&
      printf 'terminal-ready\n'
    expect:
      file_exists:
      - reference-state.json
```

```yaml studio-directive
beat:
  id: browser
  medium: browser
  heading: Operate the browser
  narration_take: terminal-browser-handoff
  narration: >-
    then the browser consumes it, advances the shared state, and enters a
    project name.
  viewer_hold: 0.3
  actions:
  - id: open
    open_page:
      url: /
      display_url: https://demo.omegaflow.dev/projects
      ready:
        visible:
          text: terminal-ready
          exact: true
  - id: advance
    click:
      target:
        role: button
        name: Advance
  - id: updated
    wait_for:
      visible:
        text: browser-updated
        exact: true
  - id: project
    fill:
      target:
        label: Project name
      text: OmegaFlow demo
  - id: shortcut
    press:
      key: Control+Enter
      target:
        label: Project name
  checks:
  - name: browser state updated
    text:
      target:
        test_id: state
      equals: browser-updated
  - name: project name retained
    value:
      target:
        label: Project name
      equals: OmegaFlow demo
  guide:
    success_hint: The application state is browser-updated.
```

```yaml studio-directive
beat:
  id: verify
  heading: Verify shared state
  narration: Finally, the terminal verifies the state produced by the browser.
  viewer_hold: 0.5
  actions:
  - run: python scripts/check_state.py browser-updated
    expect:
      output_contains:
      - browser-updated
```
