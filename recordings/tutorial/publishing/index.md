---
kind: video
id: tutorial/publishing
title: "Tutorial: Publishing"
description: Publish a video as standalone HTML or embed it in documentation.
publish:
  default: html
  surfaces:
    html:
      type: standalone_html
      file: ${outputs.asset_dir}/index.html
audio:
  enabled: false
---

# Tutorial: Publishing

Placeholder tutorial video for the publishing chapter.

```yaml studio-directive
scene: "Tutorial: Publishing"
```

```yaml studio-directive
beat:
  id: surfaces
  heading: Show Publish Surfaces
  narration: Show the two publish surfaces users will learn first.
  caption: Publish to docs or standalone HTML.
  actions:
  - commands:
    - run_file: scripts/show-surfaces.sh
      display: bash scripts/show-surfaces.sh
      expect:
        output_contains:
        - docusaurus_mdx
        - standalone_html
```
