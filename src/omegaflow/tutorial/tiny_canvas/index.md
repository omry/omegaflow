---
title: Refine a Sunset Beach Poster
---

# Refine a Sunset Beach Poster

This starter opens Tiny Canvas in a browser. The tutorial extends that browser
beat with editing, save checks, narration, guidance, and publishing.

```yaml studio-directive
config:
  browser:
    base_url: http://127.0.0.1:18476
    viewport: {width: 1280, height: 800}
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
      chrome: {mode: minimal}
  setup:
  - id: prepare-example
    name: prepare the example artwork
    run: python {{ tutorial_path }}/scripts/reset_artwork.py
    inputs:
    - example.svg
    produces:
      artwork: recordings/.omegaflow/tutorial/sunset-beach/sunset-study.svg
  - name: verify the example artwork
    run: python {{ tutorial_path }}/scripts/inspect_artwork.py
    inputs:
    - {output: prepare-example.artwork}
  - name: start Tiny Canvas
    run_file: scripts/start_server.sh
  cleanup:
  - name: stop Tiny Canvas
    run_file: scripts/stop_server.sh
```

```yaml studio-directive
beat:
  id: open-canvas
  medium: browser
  heading: Open Tiny Canvas
  viewer_hold: 3
  caption: Load the Sunset Study draft in Tiny Canvas.
  actions:
  - id: open-editor
    open_page:
      url: /
      ready:
        visible: {text: Ready, exact: true}
  checks:
  - name: starter title loaded
    value:
      target: {test_id: artwork-title}
      equals: Sunset Study
```
