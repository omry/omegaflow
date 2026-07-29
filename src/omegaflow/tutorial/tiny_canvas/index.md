---
id: sunset-beach
title: Refine a Sunset Beach Poster
setup:
- name: restore the Tiny Canvas draft
  run: python {{ tutorial_path }}/scripts/reset_artwork.py
---

# Refine a Sunset Beach Poster

This starter contains one reliable terminal beat. The tutorial adds one browser
beat, then extends that same beat with narration, guidance, and publishing.

```yaml studio-directive
scene: Refine a Sunset Beach Poster
```

```yaml studio-directive
beat:
  id: inspect-draft
  medium: terminal
  heading: Inspect The Draft
  caption: Confirm the Tiny Canvas draft before editing it.
  actions:
  - commands:
    - run: python {{ tutorial_path }}/scripts/inspect_artwork.py
      display: python scripts/inspect_artwork.py
      expect:
        output_contains:
        - "Title: Sunset Study"
        - "Objects: sun, coconut-tree"
        - "Status: ready"
```
