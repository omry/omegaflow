---
kind: video
title: Build a Tiny Canvas Video
description: >-
  Build a browser-only Tiny Canvas recording with scripted editing,
  synchronized narration, guided playback, and publishing.
---

# Build a Tiny Canvas Video

This guided walkthrough builds one cumulative Tiny Canvas recording. Chapters
are implemented and reviewed one at a time.

```yaml studio-directive
config:
  capture:
    window_size: 90x24
    headless: true
    timeout: 120
  browser: {}
  style:
    color: true
    typing: true
  presentation:
    guided: true
    pane_chrome:
      style: framed
  audio:
    enabled: true
    env: OPENAI_OMEGAFLOW_API_KEY
  setup:
  - name: prepare isolated tutorial workspace
    run_file: scripts/setup-tutorial-environment.sh
  cleanup:
  - name: remove isolated tutorial workspace
    run_file: scripts/cleanup-tutorial-environment.sh
```

```yaml studio-directive
panes:
- id: overview
  kind: visualization
  title: hidden
- id: terminal
  kind: terminal
  title: Terminal
  window_size: 90x11
- id: source
  kind: terminal
  title: Editor
  window_size: 90x24
- id: desktop
  kind: browser
  title: Desktop
- id: browser
  kind: browser
  title: Browser
```

```yaml studio-directive
beat:
  id: orientation
  heading: Build A Tiny Canvas Workflow
  narration: >-
    Here is the recording we'll make. It opens Sunset Study in Tiny Canvas,
    renames it Coconut Sunset, moves the sun and coconut tree, saves a copy, and
    confirms the result. The finished recording is browser-only. Terminals in
    this walkthrough are where we author and build it; they are not part of the
    finished video. The editor stays on the left, the desktop and browser stay
    on the right, and commands run in the terminal below. @guided_mode_start@
    Guided mode pauses after each chapter while you complete the same step. Use
    the highlighted shield button for uninterrupted playback.
  caption: See the workflow you will build in this guided walkthrough.
  player:
    highlight:
      control: guided
      start: "@guided_mode_start@"
  layout:
    areas:
    - [overview, desktop]
    - [terminal, terminal]
    rows: [3, 1]
  panes:
    overview:
    - id: workflow-map
      actions:
      - id: show-workflow
        show:
          language: text
          text: |-
            TINY CANVAS WORKFLOW

            1  Define the recording goal
            2  Prepare the workspace
            3  Understand the recording
            4  See validation
            5  Build a browser baseline
            6  Add the editing workflow
            7  Save and verify
            8  Add narration
            9  Add viewer guidance
            10  Build and review
    desktop:
    - id: idle-orientation-desktop
      chrome: {mode: hidden}
      actions:
      - id: show-tutorial-desktop
        open_page:
          url: http://127.0.0.1:43124/desktop.html
          ready:
            visible: {text: OmegaFlow tutorial, exact: true}
    terminal:
    - id: idle-orientation-terminal
  guide:
    summary: >-
      Build a browser-only Tiny Canvas recording from its starter through
      editing, narration, guidance, and publishing.
    success_hint: Continue when you are ready to prepare the tutorial workspace.
```

```yaml studio-directive
beat:
  id: prepare-workspace
  heading: Add The Tutorial Workspace
  narration: >-
    This tutorial picks up where Getting Started ends. You already have
    OmegaFlow installed and a bootstrapped project. @bootstrap_tutorial@ Run
    the bootstrap command below to add the packaged tutorial workspace. This
    creates @tiny_canvas_start@ the Tiny Canvas app,
    @tiny_canvas_end@ @starter_artwork_start@ a starter coconut-beach drawing,
    @starter_artwork_end@ @automation_scripts_start@ supporting automation
    scripts, @automation_scripts_end@ and @starter_recording_start@ a starter
    OmegaFlow recording.@starter_recording_end@
  caption: Materialize the Tiny Canvas app and starter recording.
  layout:
    areas:
    - [source, desktop]
    - [terminal, terminal]
    rows: [3, 1]
  panes:
    source:
    - id: idle-prepare-source
    desktop:
    - id: idle-prepare-desktop
    terminal:
    - id: prepare-environment
      actions:
      - id: tutorial-bootstrap
        after: voiceover.bootstrap_tutorial.started
        run: >-
          omegaflow project_root="$TUTORIAL_WORKSPACE" bootstrap=tutorial
        display: omegaflow bootstrap=tutorial
        inputs:
        - project://src/omegaflow/tutorial/tiny_canvas
        produces:
          recording: recordings/sunset-beach/index.md
        pre_command_pause: 0.4
      checks:
      - name: project configuration created
        run: test -f "$TUTORIAL_WORKSPACE/.omegaflow/config.yaml"
      - name: tutorial recording created
        run: test -f "$TUTORIAL_WORKSPACE/recordings/sunset-beach/index.md"
      - name: Tiny Canvas application created
        run: test -f "$TUTORIAL_WORKSPACE/recordings/sunset-beach/app/index.html"
      - name: starter coconut-beach drawing created
        run: test -f "$TUTORIAL_WORKSPACE/recordings/sunset-beach/example.svg"
  effects:
  - highlight:
      pane: terminal
      targets:
      - text: recordings/sunset-beach/app/index.html
      - text: recordings/sunset-beach/app/app.js
      - text: recordings/sunset-beach/app/server.py
      - text: recordings/sunset-beach/app/styles.css
      start: "@tiny_canvas_start@"
      end: "@tiny_canvas_end@"
  - highlight:
      pane: terminal
      targets:
      - text: recordings/sunset-beach/example.svg
      start: "@starter_artwork_start@"
      end: "@starter_artwork_end@"
  - highlight:
      pane: terminal
      targets:
      - text: recordings/sunset-beach/scripts/inspect_artwork.py
      - text: recordings/sunset-beach/scripts/reset_artwork.py
      - text: recordings/sunset-beach/scripts/tiny_canvas.py
      start: "@automation_scripts_start@"
      end: "@automation_scripts_end@"
  - highlight:
      pane: terminal
      targets:
      - text: recordings/sunset-beach/index.md
      start: "@starter_recording_start@"
      end: "@starter_recording_end@"
  guide:
    commands:
    - omegaflow bootstrap=tutorial
    summary: Add the packaged Tiny Canvas workspace to an existing project.
    success_hint: Continue when the generated files are visible.
```

```yaml studio-directive
beat:
  id: introduce-recording
  heading: Understand The Recording File
  narration: >-
    @open_source@ Open the generated recording in the editor on the left.
    @show_frontmatter@ Frontmatter contains its basic metadata: the title.
    @show_config@ The config directive
    describes the browser capture and presentation. @reveal_setup@
    @wait:reveal-recording-setup+300ms@ @show_setup@ Setup resets and verifies
    the example, then starts Tiny Canvas behind the scenes. This preparation is
    not visible in the finished recording. @reveal_beat@
    @wait:reveal-recording-beat+300ms@
    @show_beat@ The first beat is the visible recording. Its browser medium
    opens Tiny Canvas and checks that Sunset Study loaded. @structure_done@
  caption: See how a recording file is organized.
  layout:
    areas:
    - [source, desktop]
    - [terminal, terminal]
    rows: [3, 1]
  panes:
    source:
    - id: introduce-recording-source
      actions:
      - id: open-recording-structure
        after: voiceover.open_source.started
        run: >-
          tutorial_workspace="$(cat "$OMEGAFLOW_RUN_DIR/tutorial-workspace")";
          cd "$tutorial_workspace";
          nano --rcfile
          recordings/sunset-beach/.nanorc
          recordings/sunset-beach/index.md
        display: nano recordings/sunset-beach/index.md
        inputs:
        - {output: tutorial-bootstrap.recording}
        timing: realtime
        pre_enter_pause: 1
        input:
        - wait_for: Write Out
          timeout: 5
      - id: reveal-recording-setup
        after: voiceover.reveal_setup.started
        continue_from: open-recording-structure
        timing: realtime
        input:
        - {key: page_down}
        - {pause: 1}
      - id: reveal-recording-beat
        after: voiceover.reveal_beat.started
        continue_from: reveal-recording-setup
        timing: realtime
        input:
        - {control: _}
        - wait_for: Enter line number
          timeout: 5
        - {text: "47", interval: 0.1}
        - {key: enter}
        - wait_for: Write Out
          timeout: 5
        - {pause: 1}
    desktop:
    - id: idle-introduction-desktop
    terminal:
    - id: idle-introduction-terminal
  effects:
  - highlight:
      pane: source
      targets:
      - text: "title: Refine a Sunset Beach Poster"
      start: "@show_frontmatter@"
      end: "@show_config@"
  - highlight:
      pane: source
      targets:
      - text: "config:"
      start: "@show_config@"
      end: "@reveal_setup@"
  - highlight:
      pane: source
      targets:
      - text: "setup:"
      - text: "name: prepare the example artwork"
      - text: "name: verify the example artwork"
      - text: "name: start Tiny Canvas"
      start: "@show_setup@"
      end: "@reveal_beat@"
  - highlight:
      pane: source
      targets:
      - text: "beat:"
      - text: "medium: browser"
      - text: "heading: Open Tiny Canvas"
      start: "@show_beat@"
      end: "@structure_done@"
```

```yaml studio-directive
beat:
  id: validate-starter
  heading: See Recording Validation
  narration: >-
    @open_validation@ OmegaFlow type-checks directives before capture. We will
    deliberately replace browser with term, save the file, and build in the
    terminal below. @start_demo@
    @wait:build-invalid-starter+500ms@
    @show_validation@ The build fails before setup or capture. The error names
    beat.medium and lists the accepted values. @restore_medium@ Restore browser
    and save. @validation_done@
  caption: See OmegaFlow reject an invalid beat medium before capture.
  layout:
    areas:
    - [source, desktop]
    - [terminal, terminal]
    rows: [3, 1]
  panes:
    source:
    - id: validate-starter-source
      actions:
      - id: edit-invalid-medium
        after: voiceover.open_validation.started
        continue_from: reveal-recording-beat
        timing: realtime
        input:
        - {control: _}
        - wait_for: Enter line number
          timeout: 5
        - {text: "49", interval: 0.1}
        - {key: enter}
        - wait_for: Write Out
          timeout: 5
        - {pause: 2}
        - {key: end}
        - {key: backspace}
        - {key: backspace}
        - {key: backspace}
        - {key: backspace}
        - {key: backspace}
        - {key: backspace}
        - {key: backspace}
        - {text: term, interval: 0.08}
        - {control: o}
        - wait_for: File Name to Write
          timeout: 5
        - {key: enter}
        - wait_for: Write Out
          timeout: 5
      - id: restore-valid-medium
        after: voiceover.restore_medium.started
        continue_from: edit-invalid-medium
        timing: realtime
        input:
        - {key: end}
        - {key: backspace}
        - {key: backspace}
        - {key: backspace}
        - {key: backspace}
        - {text: browser, interval: 0.08}
        - {control: o}
        - wait_for: File Name to Write
          timeout: 5
        - {key: enter}
        - wait_for: Write Out
          timeout: 5
        - {pause: 2}
    desktop:
    - id: idle-validation-desktop
    terminal:
    - id: validate-starter-build
      actions:
      - id: build-invalid-starter
        after: source.validate-starter-source.edit-invalid-medium.ended
        run: >-
          tutorial_workspace="$(cat "$OMEGAFLOW_RUN_DIR/tutorial-workspace")";
          omegaflow project_root="$tutorial_workspace"
          recording=sunset-beach action=build;
          status=$?;
          (exit "$status")
        display: omegaflow recording=sunset-beach action=build
        inputs:
        - {output: tutorial-bootstrap.recording}
        timing: presentation
        pre_command_pause: 0.4
        expect:
          exit_code: 1
          output_contains:
          - "Invalid value 'term', expected one of [terminal, browser]"
  effects:
  - highlight:
      pane: source
      targets:
      - regex: 'medium: [^\n]*'
      start: "@open_validation@"
      end: "@validation_done@"
  - highlight:
      pane: terminal
      targets:
      - text: "beat.medium"
      - text: "Invalid value 'term'"
      start: "@show_validation@"
      end: "@restore_medium@"
```

```yaml studio-directive
beat:
  id: build-browser-baseline
  heading: Build The Browser Baseline
  narration: >-
    The recording is valid again. Build its browser beat in the terminal below.
    Setup
    prepares the artwork and starts Tiny Canvas behind the scenes; the visible
    recording begins in the browser. @build_baseline@
    @wait:build-browser-baseline-command+300ms@
    @baseline_built@ The completed progress line confirms that the browser-only
    baseline is ready. The desktop on the right remains idle until we start
    watch. Next, we will open the result and add the editing workflow.
  caption: Build the browser-only starter recording.
  layout:
    areas:
    - [source, desktop]
    - [terminal, terminal]
    rows: [3, 1]
  panes:
    source:
    - id: retain-valid-recording-source
      actions:
      - id: hold-valid-editor
        continue_from: restore-valid-medium
        timing: realtime
        input:
        - {control: l}
        - {pause: 1}
    desktop:
    - id: idle-baseline-desktop
    terminal:
    - id: build-browser-baseline-pane
      after: source.validate-starter-source.restore-valid-medium.ended
      actions:
      - id: build-browser-baseline-command
        after: voiceover.build_baseline.started
        run: >-
          tutorial_workspace="$(cat "$OMEGAFLOW_RUN_DIR/tutorial-workspace")";
          cd "$tutorial_workspace" &&
          omegaflow recording=sunset-beach action=build force=true
        display: omegaflow recording=sunset-beach action=build
        inputs:
        - {output: tutorial-bootstrap.recording}
        timing: realtime
        pre_command_pause: 0.4
        expect:
          exit_code: 0
  guide:
    commands:
    - omegaflow recording=sunset-beach action=build
    summary: Build the starter's browser-only baseline.
    success_hint: Continue when the build completes successfully.
```

```yaml studio-directive
beat:
  id: author-browser-edit
  heading: Script The Browser Edit
  narration: >-
    Start watch in the terminal below. @start_edit_watch@ Watch starts a local
    server for the generated player and monitors the recording source. When the
    source changes, it rebuilds the video as needed.
    @wait:watch-browser-baseline-command+300ms@ @open_baseline_player@ The player
    opens in a maximized browser on the right at a stable address.
    @wait:open-baseline-player+300ms@ @play_baseline@ Play the baseline.
    @wait:play-baseline-video+300ms@ @baseline_ready@ It opens Sunset Study in
    Tiny Canvas. The page stays on that baseline while we edit its source. This
    is one example of a browser workflow, not a tour of every available field.
    It will rename the poster, move the sun, and move the coconut tree.
    The Record a Browser Workflow guide documents the available actions,
    targets, waits, and checks. @open_actions@ Open the recording at its current
    action list. @wait:open-edit-actions+700ms@ @show_existing_action@ It already
    contains the action that opens Tiny Canvas. We will add the edit beneath it,
    one action at a time. @rename_intro@ First, rename the poster. The type text
    operation identifies the title by its test id, supplies Coconut Sunset, and
    controls the typing pace. A short hold before and after the edit lets the
    viewer see both states. @insert_rename@ Add only that action now.
    @wait:insert-rename-action+1200ms@ @rename_done@ The first action is complete.
    It changes the title and nothing else. @sun_intro@ Next, move the sun. A drag
    names the object to pick up and the target where it should land. Realtime
    timing preserves the visible gesture, and a hold keeps its result on screen
    before the next edit. @insert_sun@ Add the sun action below the rename.
    @wait:insert-sun-action+1200ms@ @sun_done@ The second action is complete.
    @tree_intro@ Finally, move the coconut tree with the same semantic drag
    structure, using the tree and its destination as targets. Give its result
    the same hold. @insert_tree@ Add that last action.
    @wait:insert-tree-action+1200ms@ @tree_done@ Now all three interactions are
    present. @review_actions@ Read them in order: rename the poster, move the
    sun, then move the tree. @check_intro@ The existing
    end-state check still expects the starter title. Update it to the edited
    title before building. @replace_check@ Make that change now and save the
    complete edit once.
    @wait:update-edited-title-check+900ms@ @check_done@ The workflow and its
    check now agree. Watch detects that single saved source change below and
    rebuilds the recording automatically. @wait:watch-edited-recording+300ms@
    @actions_done@ The successful build is ready at the same watch address.
  caption: Add and verify three semantic browser interactions, one at a time.
  layout:
    areas:
    - [source, browser]
    - [terminal, terminal]
    rows: [3, 1]
  panes:
    source:
    - id: author-browser-edit-source
      after: browser.play-browser-baseline-pane.play-baseline-video.ended
      actions:
      - id: open-edit-actions
        after: voiceover.open_actions.started
        continue_from: hold-valid-editor
        timing: realtime
        input:
        - {control: _}
        - wait_for: Enter line number
          timeout: 5
        - {text: "53", interval: 0.1}
        - {key: enter}
        - wait_for: Write Out
          timeout: 5
        - {pause: 2}
      - id: insert-rename-action
        after: voiceover.insert_rename.started
        continue_from: open-edit-actions
        timing: realtime
        input:
        - {control: _}
        - wait_for: Enter line number
          timeout: 5
        - {text: "58", interval: 0.1}
        - {key: enter}
        - wait_for: Write Out
          timeout: 5
        - {key: end}
        - {key: enter}
        - text: |2-
              - id: rename-artwork
                hold_before_ms: 700
                hold_after_ms: 900
                type_text:
                  target: {test_id: artwork-title}
                  text: Coconut Sunset
                  interval_ms: 90
          interval: 0.035
        - {pause: 2}
      - id: insert-sun-action
        after: voiceover.insert_sun.started
        continue_from: insert-rename-action
        timing: realtime
        input:
        - {key: end}
        - {key: enter}
        - text: |2-
              - id: move-sun
                timing: realtime
                hold_after_ms: 900
                drag:
                  from: {target: {test_id: sun}}
                  to: {target: {test_id: sunset-target}}
          interval: 0.035
        - {pause: 2}
      - id: insert-tree-action
        after: voiceover.insert_tree.started
        continue_from: insert-sun-action
        timing: realtime
        input:
        - {key: end}
        - {key: enter}
        - text: |2-
              - id: move-tree
                timing: realtime
                hold_after_ms: 900
                drag:
                  from: {target: {test_id: coconut-tree}}
                  to: {target: {test_id: tree-target}}
          interval: 0.035
        - {pause: 3}
      - id: update-edited-title-check
        after: voiceover.replace_check.started
        continue_from: insert-tree-action
        timing: realtime
        input:
        - {control: w}
        - wait_for: Search
          timeout: 5
        - {text: "checks:", interval: 0.08}
        - {key: enter}
        - wait_for: Write Out
          timeout: 5
        - {key: down}
        - {control: k}
        - {control: k}
        - {control: k}
        - {control: k}
        - text: |2-
              - name: edited title retained
                value:
                  target: {test_id: artwork-title}
                  equals: Coconut Sunset
          interval: 0.035
        - {key: enter}
        - {control: o}
        - wait_for: File Name to Write
          timeout: 5
        - {key: enter}
        - wait_for: Write Out
          timeout: 5
        - {pause: 2}
    browser:
    - id: play-browser-baseline-pane
      actions:
      - id: open-baseline-player
        after: terminal.watch-edited-workflow-pane.watch-browser-baseline-command.ended
        open_page:
          url: http://127.0.0.1:43125/watch/sunset-beach/
          display_url: http://127.0.0.1:43125/watch/sunset-beach/
          ready:
            visible:
              role: button
              name: Play
              exact: true
      - id: play-baseline-video
        after: voiceover.play_baseline.started
        timing: realtime
        click:
          target:
            role: button
            name: Play
            exact: true
        until:
          visible: {css: '#progress-wrap[data-complete="true"]'}
          timeout_ms: 10000
    terminal:
    - id: watch-edited-workflow-pane
      actions:
      - id: watch-browser-baseline-command
        after: voiceover.start_edit_watch.started
        run_file: scripts/start-tutorial-watch.sh
        display: omegaflow recording=sunset-beach action=watch
        timing: presentation
        show_prompt_after: false
      - id: watch-edited-recording
        after: voiceover.open_actions.started
        run_file: scripts/follow-tutorial-watch.sh
        display: '# watch remains active; waiting for source changes'
        timing: realtime
        show_prompt_after: false
  effects:
  - highlight:
      pane: source
      targets:
      - text: "actions:"
      - text: "id: open-editor"
      start: "@show_existing_action@"
      end: "@rename_intro@"
  - highlight:
      pane: source
      targets:
      - text: "id: rename-artwork"
      start: "@rename_done@"
      end: "@sun_intro@"
  - highlight:
      pane: source
      targets:
      - text: "id: move-sun"
      start: "@sun_done@"
      end: "@tree_intro@"
  - highlight:
      pane: source
      targets:
      - text: "id: move-tree"
      start: "@tree_done@"
      end: "@review_actions@"
  - highlight:
      pane: source
      targets:
      - text: "id: rename-artwork"
      - text: "id: move-sun"
      - text: "id: move-tree"
      start: "@review_actions@"
      end: "@check_intro@"
  - highlight:
      pane: source
      targets:
      - text: "name: edited title retained"
      - text: "equals: Coconut Sunset"
      start: "@check_done@"
      end: "@actions_done@"
  guide:
    commands:
    - omegaflow recording=sunset-beach action=watch
    summary: >-
      Add one example browser workflow. See Guides → Browser Workflows for the
      complete action, target, wait, and check syntax.
    success_hint: Continue when the three browser actions and edited-title check are saved.
```

```yaml studio-directive
beat:
  id: build-edited-workflow
  heading: Refresh The Edited Workflow
  narration: >-
    The automatic rebuild is complete in the terminal below. The maximized
    browser on the right keeps the same address. @refresh_edited@ Refresh Chrome to load
    the completed build from that stable URL.
    @wait:refresh-edited-player+500ms@ @play_edited@ Play
    the updated video. @wait:play-edited-video+300ms@ @edited_ready@ Tiny Canvas
    opens as Sunset Study, changes to Coconut Sunset, then moves the sun and
    coconut tree to their semantic targets.
  caption: Build and play the edited Tiny Canvas workflow.
  layout:
    areas:
    - [source, browser]
    - [terminal, terminal]
    rows: [3, 1]
  panes:
    source:
    - id: close-edited-source-pane
      actions:
      - id: close-edited-source
        after: browser.play-edited-workflow-pane.play-edited-video.ended
        continue_from: update-edited-title-check
        timing: realtime
        input:
        - {control: x}
    browser:
    - id: play-edited-workflow-pane
      actions:
      - id: refresh-edited-player
        after: voiceover.refresh_edited.started
        reload_page:
          ready:
            visible:
              role: button
              name: Play
              exact: true
      - id: play-edited-video
        after: voiceover.play_edited.started
        timing: realtime
        click:
          target:
            role: button
            name: Play
            exact: true
        until:
          visible: {css: '#progress-wrap[data-complete="true"]'}
          timeout_ms: 15000
    terminal:
    - id: retain-edited-watch
  guide:
    summary: Refresh the existing watch page and play the automatically rebuilt workflow.
    success_hint: Continue when the generated video completes all three edits.
```
