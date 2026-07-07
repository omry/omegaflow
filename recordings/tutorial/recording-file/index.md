---
id: tutorial/recording-file
title: "Tutorial: Recording File"
publish:
  default: html
  surfaces:
    html:
      type: standalone_html
      file: ${outputs.asset_dir}/index.html
audio:
  enabled: false
---

# Tutorial: Recording File

Placeholder tutorial video for the recording file chapter.

```yaml studio-directive
scene: "Tutorial: Recording File"
```

```yaml studio-directive
beat:
  id: shape
  heading: Show The Shape
  narration: Show the files that make up one recording.
  caption: A recording is a directory with Markdown and support files.
  actions:
  - commands:
    - run_file: scripts/show-shape.sh
      display: bash scripts/show-shape.sh
      expect:
        output_contains:
        - index.md
        - scripts/
```
