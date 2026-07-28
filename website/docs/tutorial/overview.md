---
sidebar_position: 1
sidebar_label: Tutorial
slug: /tutorial
---

import VideoPlayer from "@site/src/components/VideoPlayer";

# Build a Rebuildable Tiny Canvas Demo

In this tutorial, you will build one OmegaFlow video from source to published
output. The video starts in a terminal, edits a sunset poster in the supplied
Tiny Canvas browser app, and finishes with the original and edited artwork
side by side.

Along the way you will learn how to:

- bootstrap an OmegaFlow project and tutorial workspace;
- describe a video as a sequence of beats;
- make a terminal beat reproducible with setup and checks;
- script semantic browser actions such as fill, drag, click, and wait;
- compose terminal and browser panes in one video;
- synchronize actions with narration anchors;
- add a guided checkpoint; and
- build, review, check, and publish the result.

The written steps are complete on their own. The supporting walkthrough
demonstrates the same workflow without replacing the commands or source below.

<!-- studio:tutorial:start -->
<VideoPlayer
  title="Build a Tiny Canvas Video"
  manifest="/omegaflow-videos/tutorial/presentation/recording.presentation.json"
/>
<!-- studio:tutorial:end -->

## Before you begin

Use a Python environment with OmegaFlow installed:

```bash
python -m pip install omegaflow
```

Browser capture also requires the browser dependencies described in the
[installation guide](../intro.md). Narration requires an OpenAI API key. You
can complete the silent video first and add narration later.

## 1. Create the project and tutorial workspace

From an empty project directory, bootstrap OmegaFlow:

```bash
omegaflow bootstrap=project
omegaflow bootstrap=tutorial
```

The first command creates project configuration, recording defaults, a private
OmegaFlow service environment placeholder, and a small test video. The second
command adds Tiny Canvas and a starter `sunset-beach` recording.

The relevant files are:

```text
.omegaflow/
  .gitignore
  config.yaml
  omegaflow-secret.env
recordings/
  config.yaml
  sunset-beach/
    app/
    scripts/
    index.md
```

`.omegaflow/omegaflow-secret.env` is ignored by the generated `.gitignore`.
When you are ready to generate narration, uncomment its
`OPENAI_OMEGAFLOW_API_KEY` entry and provide your key. OmegaFlow does not pass
that service credential to recorded commands.

## 2. Read the starter recording

Open `recordings/sunset-beach/index.md` in your editor. Its frontmatter names
the recording and configures standalone HTML output:

```yaml
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
```

The Markdown body contains `studio-directive` blocks. A `scene` names the
presentation, and each `beat` defines one recorded unit:

```yaml
scene: Refine a Sunset Beach Poster
```

```yaml
beat:
  id: inspect-draft
  heading: Inspect The Draft
  narration: Inspect the supplied artwork and confirm its known starting state.
  caption: Confirm the Tiny Canvas draft before editing it.
  actions:
  - commands:
    - run: python scripts/reset_artwork.py
      expect:
        output_contains:
        - Restored the Tiny Canvas draft.
    - run: python scripts/inspect_artwork.py
      expect:
        output_contains:
        - "Title: Sunset Study"
        - "Objects: sun, coconut-tree"
        - "Status: ready"
```

A beat may record terminal or browser behavior. `terminal` is the default
medium, so the starter does not repeat it.

### See typed validation fail early

Temporarily add an invalid medium to the beat:

```yaml
beat:
  id: inspect-draft
  medium: shell
```

Then validate the recording:

```bash
omegaflow recording=sunset-beach action=check
```

OmegaFlow rejects `shell` before capture and reports that the supported values
are `terminal` and `browser`. Change it to `terminal`, or remove the field to
use the default.

## 3. Build a repeatable terminal beat

Build and watch the starter:

```bash
omegaflow recording=sunset-beach action=build
omegaflow recording=sunset-beach action=watch
```

This small beat already has three reliability layers:

1. setup restores the immutable packaged draft;
2. the action inspects the real working SVG; and
3. expectations fail the build if the title or semantic objects are wrong.

That distinction matters. A plausible terminal transcript is not enough; a
rebuildable video must prove that its visible state came from the expected
application state.

## 4. Add the browser workflow

The completed silent workflow uses three globally named panes:

```yaml
scene: Refine a Sunset Beach Poster
panes:
- id: before
  kind: browser
  title: Before
- id: after
  kind: browser
  title: After
- id: terminal
  kind: terminal
  title: Tiny Canvas workflow
```

Pane declarations appear once, before every beat. The first beat launches Tiny
Canvas from the terminal and hands the resulting browser session to the
`after` pane:

```yaml
beat:
  id: launch-editor
  heading: Open The Artwork
  layout:
    areas:
    - [terminal]
  panes:
    terminal:
    - id: launch-editor
      actions:
      - id: edit-file
        run: python scripts/tiny_canvas.py sunset-study.svg
        browser_handoff: {target: after}
        timing: realtime
        pre_enter_pause: 1.0
```

The browser beat consumes that handoff, waits for the editor, changes the title,
drags semantic SVG objects, and saves a new file:

```yaml
beat:
  id: edit-artwork
  heading: Edit And Save A Copy
  layout:
    areas:
    - [after]
  panes:
    after:
    - id: editor
      actions:
      - id: open-editor
        open_page:
          handoff: edit-file
          ready:
            visible: {text: Ready, exact: true}
      - id: rename-artwork
        type_text:
          target: {test_id: artwork-title}
          text: Coconut Sunset
          interval_ms: 90
      - id: move-sun
        drag:
          from: {target: {test_id: sun}}
          to: {target: {test_id: sunset-target}}
      - id: move-tree
        drag:
          from: {target: {test_id: coconut-tree}}
          to: {target: {test_id: tree-target}}
      - id: save-new-file
        click:
          target: {test_id: export-artwork}
      - id: saved-new-file
        wait_for:
          visible: {text: Saved coconut-sunset.svg, exact: true}
```

Semantic targets make the script resilient to layout and viewport changes.
Component-relative percentages are available when an application does not
provide a meaningful destination element; absolute screen pixels are not
required here.

The final beat opens the original and saved SVGs into two browser panes above
the terminal:

```yaml
beat:
  id: compare-files
  heading: Compare Before And After
  layout:
    areas:
    - [before, after]
    - [before, after]
    - [terminal, terminal]
  panes:
    before:
    - id: original-file
      actions:
      - open_page:
          handoff: open-original
          ready:
            response: {contains: /files/sunset-study.svg, status: 200}
    after:
    - id: saved-file
      actions:
      - open_page:
          handoff: open-saved
          ready:
            response: {contains: /files/coconut-sunset.svg, status: 200}
    terminal:
    - id: open-original
      actions:
      - id: open-original
        run: python scripts/tiny_canvas.py --view sunset-study.svg
        browser_handoff: {target: before}
        timing: realtime
    - id: open-saved
      actions:
      - id: open-saved
        run: python scripts/tiny_canvas.py --view coconut-sunset.svg
        browser_handoff: {target: after}
        timing: realtime
```

Build the silent three-beat recording before adding narration:

```bash
omegaflow recording=sunset-beach action=build
omegaflow recording=sunset-beach action=watch beat=edit-artwork
```

`beat=edit-artwork` starts review at that top-level beat. It does not make the
nested `editor` pane beat an independent video section.

## 5. Synchronize narration and actions

Enable narration in frontmatter:

```yaml
audio:
  enabled: true
  env: OPENAI_OMEGAFLOW_API_KEY
  voice: ash
```

If you add narration but leave the browser actions unscheduled, the short edits
will run ahead of the words describing them. Narration anchors give meaningful
moments stable names, and `after` joins an action to one of those moments:

```yaml
narration: >-
  Rename the poster @rename@ Coconut Sunset. Move the @sun@ sun toward the
  horizon, reposition the @tree@ coconut tree, and @save@ save a new copy.
```

```yaml
- id: rename-artwork
  after: "@rename@"
  type_text:
    target: {test_id: artwork-title}
    text: Coconut Sunset
    interval_ms: 90
- id: move-sun
  after: "@sun@"
  drag:
    from: {target: {test_id: sun}}
    to: {target: {test_id: sunset-target}}
- id: move-tree
  after: "@tree@"
  drag:
    from: {target: {test_id: coconut-tree}}
    to: {target: {test_id: tree-target}}
- id: save-new-file
  after: "@save@"
  click:
    target: {test_id: export-artwork}
```

Use `step=narration` while revising spoken text without rebuilding capture:

```bash
omegaflow recording=sunset-beach step=narration
```

Presentation-time actions may move on the compiled timeline so they line up
with narration. Realtime actions preserve their internal elapsed behavior and
may be positioned, but not stretched or reordered. A narration wait is a
different tool: it pauses speech until an event completes. This tutorial has no
long browser operation, so it does not add a fake wait.

## 6. Add a guided checkpoint

A guide pauses at a beat boundary when Guided mode is enabled. Add this to the
comparison beat:

```yaml
guide:
  summary: Compare the original and edited artwork.
  commands:
  - python scripts/tiny_canvas.py --view sunset-study.svg
  - python scripts/tiny_canvas.py --view coconut-sunset.svg
  success_hint: Both SVG files are open in their browser panes.
```

The player labels the action **Copy commands** because the checkpoint contains
more than one command. With Guided mode disabled, the same video continues
without stopping.

## 7. Check and publish

Build the finished recording, review it, and run the non-mutating check:

```bash
omegaflow recording=sunset-beach action=build
omegaflow recording=sunset-beach action=watch
omegaflow recording=sunset-beach action=check
```

The configured standalone player is written under:

```text
recordings/.omegaflow/videos/sunset-beach/
```

Commit the recording source, Tiny Canvas support files, and project
configuration. Do not commit ignored runtime runs or secret environment files.

Change the narration or one browser action and build again. OmegaFlow behaves
like a compiler: it fingerprints source inputs, reuses unchanged capture and
narration intermediates, and rebuilds the affected presentation and publish
surfaces.

## Where to go next

- [Recording files](../recording-files/overview.md) explains the complete source
  model.
- [Build and check](../cli/actions/build-check.md) covers focused authoring
  steps and CI validation.
- [Watch](../cli/actions/watch.md) covers beat links, collections, and local
  playback.
- [Video output](../video-output.md) explains generated and published
  artifacts.
