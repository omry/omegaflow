---
kind: video
id: tutorial/beat
title: "Tutorial: Beat"
description: See how narration, actions, and checks form a beat.
publish:
  default: html
  surfaces:
    html:
      type: standalone_html
      file: ${outputs.asset_dir}/index.html
audio:
  enabled: false
---

# Tutorial: Beat

Placeholder tutorial video for the beat chapter.

```yaml studio-directive
scene: "Tutorial: Beat"
```

```yaml studio-directive
beat:
  id: parts
  heading: Show Beat Parts
  narration: Show the fields a beat uses first.
  caption: A beat combines narration, commands, and checks.
  actions:
  - commands:
    - run_file: scripts/show-beat.sh
      display: bash scripts/show-beat.sh
      expect:
        output_contains:
        - heading
        - narration
        - actions
        - expect
```
