---
kind: video
id: reference/terminal-highlights
title: Highlight Terminal Output
description: Apply exact-text and multiline regular-expression highlights in sync with narration.
capture:
  window_size: 80x20
  timeout: 50
style:
  color: true
  typing: true
audio:
  enabled: true
outputs:
  dir: website/static/omegaflow-videos
publish:
  default: html
  surfaces:
    html:
      type: standalone_html
      file: ${outputs.asset_dir}/index.html
---

# Highlight Terminal Output

```yaml studio-directive
scene: Highlighting terminal text
panes:
- id: definition
  kind: visualization
  title: Beat definition
- id: terminal
  kind: terminal
  title: Live output
```

```yaml studio-directive
beat:
  id: highlight-targets
  heading: Highlight Exact Text And Patterns
  narration: >-
    The first example shows the relevant part of one beat: its narration and
    highlight effect. @explain_start@ The first anchor marks where the
    highlight begins. @explain_end@ The second anchor marks where it ends.
    @explain_range@ The highlight remains active for the text between those two
    anchors. @play_exact@ Now the beat plays that narration, and the effect
    follows its anchors. Highlight will start @exact_start@ now, and will end now.@exact_end@
    @regex_start@ Next, one regular expression highlights the
    label and changing timer as a single multi-line match. @regex_end@ The regex
    highlight is now off. @combined_start@ Finally, one effect combines the
    exact target and the multi-line expression, highlighting all three lines
    together. @combined_end@
  effects:
  - highlight:
      pane: definition
      color: brand
      targets:
      - text: "@exact_start@"
        occurrence: 1
      - text: "@exact_start@"
        occurrence: 2
      start: "@explain_start@"
      end: "@explain_end@"
  - highlight:
      pane: definition
      color: brand
      targets:
      - text: "@exact_end@"
        occurrence: 1
      - text: "@exact_end@"
        occurrence: 2
      start: "@explain_end@"
      end: "@explain_range@"
  - highlight:
      pane: definition
      color: brand
      targets:
      - text: "now, and will end now."
      start: "@explain_range@"
      end: "@play_exact@"
  - highlight:
      pane: terminal
      targets:
      - text: "Renderer: ready"
      start: "@exact_start@"
      end: "@exact_end@"
  - highlight:
      pane: terminal
      targets:
      - regex: 'Elapsed since start of video:\n.*'
      start: "@regex_start@"
      end: "@regex_end@"
  - highlight:
      pane: terminal
      targets:
      - text: "Renderer: ready"
      - regex: 'Elapsed since start of video:\n.*'
      start: "@combined_start@"
      end: "@combined_end@"
  layout:
    areas:
    - [definition]
    - [terminal]
  panes:
    definition:
    - id: exact-overview
      actions:
      - id: show-exact-overview
        show:
          language: yaml
          text: |-
            narration: >-
              Highlight will start @exact_start@ now, and will end now.@exact_end@
            effects:
            - highlight:
                pane: terminal
                targets:
                - text: "Renderer: ready"
                start: "@exact_start@"
                end: "@exact_end@"
    - id: regex-target
      after: voiceover.regex_start.started
      actions:
      - id: show-regex-target
        show:
          language: yaml
          text: |-
            effects:
            - highlight:
                pane: terminal
                targets:
                - regex: 'Elapsed since start of video:\n.*'
                start: "@regex_start@"
                end: "@regex_end@"
    - id: combined-targets
      after: voiceover.combined_start.started
      actions:
      - id: show-combined-targets
        show:
          language: yaml
          text: |-
            effects:
            - highlight:
                pane: terminal
                targets:
                - text: "Renderer: ready"
                - regex: 'Elapsed since start of video:\n.*'
                start: "@combined_start@"
                end: "@combined_end@"
    terminal:
    - id: target-results
      actions:
      - id: show-status
        run: python3 recordings/reference/terminal-highlights/status_demo.py
        display: python3 status_demo.py
        timing: realtime
```
