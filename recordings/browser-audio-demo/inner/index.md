---
kind: video
id: browser-audio-demo/inner
title: Inner Narrated Video
audio:
  enabled: true
  voice: ash
publish:
  default: html
  surfaces:
    html:
      type: standalone_html
      file: ${outputs.asset_dir}/index.html
---

# Inner Narrated Video

This short recording supplies deterministic nested narration for the browser
audio demonstration.

```yaml studio-directive
scene: Inner narrated video
```

```yaml studio-directive
beat:
  id: inner-message
  heading: Inner Narration
  narration: >-
    This voice belongs to the OmegaFlow player inside the recorded browser.
  caption: The inner player owns this narration.
  viewer_hold: 0.5
  actions:
  - run: "printf 'Inner player narration\\n'"
```
