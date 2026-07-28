---
id: sunset-beach
title: Refine a Sunset Beach Poster
publish:
  default: html
  surfaces:
    html:
      type: standalone_html
      file: ${outputs.asset_dir}/index.html
---

# Refine a Sunset Beach Poster

This cumulative tutorial recording begins by inspecting a deterministic draft.
Later tutorial milestones add the Tiny Canvas browser edit, narration,
guidance, and publishing.

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
