---
sidebar_position: 1
sidebar_label: Tutorial
slug: /tutorial
---

import VideoPlayer from "@site/src/components/VideoPlayer";

# Build a Rebuildable Tiny Canvas Video

This tutorial takes one small recording from generated starter to published
video. You will begin with a reliable terminal beat, add one browser beat that
edits a sunset poster, synchronize that beat with narration, and add a guided
checkpoint.

The written steps are complete on their own. The walkthrough follows the same
terminal-to-browser path and runs in **Guided mode**, which pauses after each
beat. Turn Guided mode off in the player controls if you prefer uninterrupted
playback.

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

Browser capture also requires the browser dependencies described in
[Getting Started](../intro.md). You can build the silent workflow before
configuring narration.

## 1. Prepare the project

Start in the repository where you want to keep your recording sources:

```bash
omegaflow bootstrap=project
```

This creates project settings, recording defaults, a private OmegaFlow service
environment placeholder, and a small `test-video`. Build and watch that video
to verify the installation:

```bash
omegaflow recording=test-video action=build
omegaflow recording=test-video action=watch
```

Now add the tutorial workspace:

```bash
omegaflow bootstrap=tutorial
```

`bootstrap=tutorial` requires the project bootstrap. It adds the supplied Tiny
Canvas application and an editable starter recording at
`recordings/sunset-beach/`.

## 2. Read the generated terminal beat

Open `recordings/sunset-beach/index.md`. Its frontmatter already configures the
local Tiny Canvas server, browser capture, Guided mode, and standalone HTML
publishing. The Markdown body starts with a scene and one terminal beat:

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
    - run: python recordings/sunset-beach/scripts/reset_artwork.py
      expect:
        output_contains:
        - Restored the Tiny Canvas draft.
    - run: python recordings/sunset-beach/scripts/inspect_artwork.py
      expect:
        output_contains:
        - "Title: Sunset Study"
        - "Objects: sun, coconut-tree"
        - "Status: ready"
```

The frontmatter and each `studio-directive` block are typed. To see validation
before capture, temporarily add `medium: shell` to the beat and run:

```bash
omegaflow recording=sunset-beach action=check
```

OmegaFlow identifies the invalid field and lists `terminal` and `browser` as
the supported values. Remove the field afterward; terminal is the default.

## 3. Build the starter

Build and watch the generated beat:

```bash
omegaflow recording=sunset-beach action=build
omegaflow recording=sunset-beach action=watch
```

The beat is reliable for three separate reasons:

1. the first command restores an immutable packaged draft;
2. the second command inspects the real working SVG; and
3. its expectations fail the build if the title or semantic objects are wrong.

The transcript is therefore evidence from a repeatable workflow rather than
plausible prerecorded output.

## 4. Add one browser beat

First extend the existing terminal beat with the command that opens Tiny Canvas.
The handoff lets the following browser beat take control of that exact browser
session:

```yaml
- id: open-editor
  run: python recordings/sunset-beach/scripts/tiny_canvas.py sunset-study.svg
  display: python scripts/tiny_canvas.py sunset-study.svg
  browser_handoff: true
  timing: realtime
  show_prompt_after: false
```

Then add one complete browser beat after the terminal beat:

```yaml
beat:
  id: edit-artwork
  medium: browser
  heading: Edit And Save A Copy
  caption: Script one semantic browser workflow.
  actions:
  - id: open-editor
    open_page:
      handoff: open-editor
      ready:
        visible: {text: Ready, exact: true}
  - id: rename-artwork
    type_text:
      target: {test_id: artwork-title}
      text: Coconut Sunset
      interval_ms: 90
  - id: move-sun
    timing: realtime
    drag:
      from: {target: {test_id: sun}}
      to: {target: {test_id: sunset-target}}
  - id: move-tree
    timing: realtime
    drag:
      from: {target: {test_id: coconut-tree}}
      to: {target: {test_id: tree-target}}
  - id: save-new-file
    click:
      target: {test_id: export-artwork}
  - id: saved-new-file
    wait_for:
      visible: {text: Saved coconut-sunset.svg, exact: true}
  checks:
  - name: title retained
    value:
      target: {test_id: artwork-title}
      equals: Coconut Sunset
  - name: new artwork saved
    text:
      target: {test_id: status}
      equals: Saved coconut-sunset.svg
```

The targets describe application elements rather than screen pixels.
`type_text` records the visible edit, each drag uses semantic SVG objects, and
`wait_for` proves that the application finished saving.

Build the silent two-beat video and review the changed beat directly:

```bash
omegaflow recording=sunset-beach action=build
omegaflow recording=sunset-beach action=watch beat=edit-artwork
```

## 5. Add narration and synchronize the edit

Add your OpenAI API key to the ignored
`.omegaflow/omegaflow-secret.env`, then enable narration in the recording
frontmatter:

```yaml
audio:
  enabled: true
  env: OPENAI_OMEGAFLOW_API_KEY
  voice: ash
```

First add this narration to `edit-artwork` without scheduling its actions:

```yaml
narration: >-
  Rename the poster Coconut Sunset. Move the sun toward the horizon,
  reposition the coconut tree, and save a new copy.
```

Build and watch the beat. The short browser actions race ahead of the words
that describe them. Name the useful moments with narration anchors:

```yaml
narration: >-
  @rename@ Rename the poster Coconut Sunset. @sun@ Move the sun toward the
  horizon, @tree@ reposition the coconut tree, and @save@ save a new copy.
```

Then join each existing action to the corresponding narration event:

```yaml
- id: rename-artwork
  after: "@rename@"
  type_text:
    target: {test_id: artwork-title}
    text: Coconut Sunset
    interval_ms: 90
- id: move-sun
  after: "@sun@"
  timing: realtime
  drag:
    from: {target: {test_id: sun}}
    to: {target: {test_id: sunset-target}}
- id: move-tree
  after: "@tree@"
  timing: realtime
  drag:
    from: {target: {test_id: coconut-tree}}
    to: {target: {test_id: tree-target}}
- id: save-new-file
  after: "@save@"
  click:
    target: {test_id: export-artwork}
```

Regenerate only narration while revising spoken text:

```bash
omegaflow recording=sunset-beach action=build step=narration
```

Presentation-time actions can move on the compiled timeline to align with
narration. Realtime actions preserve their internal elapsed behavior and can be
positioned, but not stretched or reordered. Narration waits are for
asynchronous work that speech must wait for; this short browser workflow does
not need an artificial wait.

## 6. Add guided checkpoints

The generated recording already enables Guided mode. Add a guide to the
terminal beat:

```yaml
guide:
  summary: The generated terminal beat opens Tiny Canvas.
  success_hint: Continue when you are ready to watch the browser edit.
```

Add another to `edit-artwork`:

```yaml
guide:
  summary: The two-beat Tiny Canvas recording is ready.
  success_hint: Build and watch the finished video from the terminal.
```

Guided playback pauses at these beat boundaries. Turning Guided mode off only
changes whether playback pauses; it does not hide or alter the guide content.

## 7. Check and publish

Build the completed recording, review it, and run the non-mutating check:

```bash
omegaflow recording=sunset-beach action=build
omegaflow recording=sunset-beach action=watch
omegaflow recording=sunset-beach action=check
```

The configured standalone player is written under:

```text
recordings/.omegaflow/videos/sunset-beach/
```

Commit the recording source, Tiny Canvas files, and project configuration. Do
not commit ignored runtime runs or secret environment files. On later builds,
OmegaFlow fingerprints its inputs and reuses unchanged capture and narration
instead of recreating every intermediate.

## Where to go next

The tutorial deliberately follows one path. Focused reference pages and videos
cover individual capabilities in greater depth:

- [Recording files](../recording-files/overview.md)
- [Build and check](../cli/actions/build-check.md)
- [Watch](../cli/actions/watch.md)
- [Video output](../video-output.md)
