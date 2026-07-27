---
kind: video
id: realtime-terminal-input-demo
title: Realtime Terminal Input
description: Drive a terminal interface from typed, readiness-aware input steps.
capture:
  window_size: 80x20
style:
  color: true
  typing: true
audio:
  enabled: false
requirements:
  commands:
  - nano
setup:
- name: prepare editable artwork
  run: >-
    mkdir -p "$OMEGAFLOW_RUN_DIR/demo";
    cp recordings/realtime-terminal-input-demo/artwork.svg
    "$OMEGAFLOW_RUN_DIR/demo/artwork.svg"
---

# Realtime Terminal Input

This demonstration edits an isolated copy of `artwork.svg` through nano's real
terminal interface.

```yaml studio-directive
scene: Realtime terminal input
```

```yaml studio-directive
beat:
  id: edit
  heading: Edit Artwork In Nano
  narration: Update the artwork title and color, save the file, and exit the editor.
  actions:
  - commands:
    - id: edit_artwork
      run: >-
        nano
        --rcfile recordings/realtime-terminal-input-demo/nanorc
        "$OMEGAFLOW_RUN_DIR/demo/artwork.svg"
      display: nano artwork.svg
      timing: realtime
      input:
      - wait_for: Write Out
        timeout: 5
      - pause: 0.8
      - key: down
      - pause: 0.3
      - key: home
      - pause: 0.2
      - control: k
      - pause: 0.4
      - key: up
      - key: end
      - key: enter
      - pause: 0.3
      - text: "  <title>Realtime terminal input</title>"
        interval: 0.055
      - pause: 0.7
      - key: down
      - key: home
      - control: k
      - pause: 0.4
      - key: up
      - key: end
      - key: enter
      - pause: 0.3
      - text: "  <rect width=\"320\" height=\"180\" rx=\"12\" fill=\"#f6a85f\"/>"
        interval: 0.055
      - pause: 0.7
      - control: o
      - wait_for: File Name to Write
        timeout: 5
      - pause: 0.4
      - key: enter
      - wait_for: Wrote 4 lines
        timeout: 5
      - pause: 0.8
      - control: x
      expect:
        file_exists:
        - $OMEGAFLOW_RUN_DIR/demo/artwork.svg
  checks:
  - name: artwork changes were saved
    run: >-
      grep -F '<title>Realtime terminal input</title>'
      "$OMEGAFLOW_RUN_DIR/demo/artwork.svg" &&
      grep -F 'rx="12" fill="#f6a85f"'
      "$OMEGAFLOW_RUN_DIR/demo/artwork.svg"
```
