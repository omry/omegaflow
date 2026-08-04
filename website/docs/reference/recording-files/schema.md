---
sidebar_position: 4
sidebar_label: Recording Schema
slug: /reference/recording-files/schema/
---

# Recording Schema

A beat is one recorded unit in a video. It can describe what the viewer is
seeing, operate a terminal or browser, run checks, and provide guide text.
Narration can be absent, local to one beat, or shared across multiple beats.

```yaml
beat:
  id: install
  heading: Install OmegaFlow
  narration: Install the package and confirm the omegaflow command is available.
```

## Fields

| Field | Type | Notes |
| --- | --- | --- |
| `id` | string | Unique beat id. |
| `medium` | `terminal` or `browser` | Renderer and action vocabulary for this beat. Defaults to `terminal`. |
| `heading` | string | Section heading and narration label. |
| `narration` | string | Spoken narration text. Supports markers such as `@anchor@` and `@wait:command_id+1s@`. |
| `narration_take` | string | Optional take id shared by adjacent beats. When omitted, this beat receives an implicit singleton take. |
| `marker` | string | Optional UI marker id. |
| `caption` | string | Text printed visibly in the terminal recording. |
| `viewer_hold` | number | Extra viewer pause after the beat. |
| `pointer` | mapping | Browser-beat override for pointer visibility. Use `visible: false` to hide the cursor for the whole beat. |
| `actions` | list | Commands to record. |
| `checks` | list | Commands that validate the result. |
| `effects` | list | Narration-synchronized presentation effects for terminal or visualization text. |
| `guide` | mapping | Guided-mode summary, commands, and success hint. |
| `layout` | mapping | Grid of pane IDs for an explicit multi-pane beat. |
| `panes` | mapping | Pane IDs mapped to their ordered pane beats. |

## Multi-pane Beats

The regular `medium`, `actions`, `checks`, and `effects` fields are convenient
single-pane shorthand. To show multiple synchronized views, declare recording
panes once in a `studio-directive` before any `beat` or `beats` declaration:

```yaml
scene: Highlighting terminal text
panes:
- id: definition
  kind: visualization
  title: Beat definition
- id: output
  kind: terminal
  title: Live output
```

Omitting `title` derives a label from the pane ID. Use a string for explicit
text, `hidden` to suppress the label, or a mapping to place it relative to the
pane frame:

```yaml
- id: preview
  kind: browser
  title:
    text: Preview
    alignment_x: right
    alignment_y: bottom
    position_x: 0.25rem
    position_y: 0.25rem
```

The full title form defaults to `right`, `top`, `0.25rem`, and `0.25rem`.

Pane IDs are stable across the recording. Every declared pane must be used by
at least one outer beat. The outer beat owns narration and layout. Each pane
then supplies its own pane beat and identified actions:

```yaml
beat:
  id: explain-highlight
  heading: Explain A Highlight
  narration: >-
    @definition_start@ Compare the highlight definition
    @definition_end@ with its terminal output.
  effects:
  - highlight:
      pane: definition
      color: brand
      targets:
      - text: "Renderer: ready"
      start: "@definition_start@"
      end: "@definition_end@"
  layout:
    areas:
    - [definition]
    - [output]
  panes:
    definition:
    - id: show-definition
      actions:
      - id: show-source
        show:
          language: yaml
          text: |
            effects:
            - highlight:
                pane: output
                targets:
                - text: "Renderer: ready"
    output:
    - id: show-output
      actions:
      - id: run-status
        run: printf 'Renderer: ready\n'
```

Effects belong to the outer beat because their timing comes from its narration.
Set `highlight.pane` to the pane whose recording surface contains the target
text. The same effect syntax works for syntax-colored visualization text and
captured terminal text.

Pane tracks can use visualization, terminal, or browser renderers and can
contain sequential pane beats. Pane beats follow their authored order.
An `after` join can additionally delay any pane beat until an event from
narration or another pane:

```yaml
narration: >-
  @exact@ First, show an exact target.
  @pattern@ Then show a pattern.
panes:
  definition:
  - id: exact
    actions:
    - show:
        text: Exact target
  - id: pattern
    after: voiceover.pattern.started
    actions:
    - show:
        text: Pattern target
```

`voiceover` is the recording's narration stream id, `pattern` is the narration
segment introduced by `@pattern@`, and `started` selects the segment's start
event. Use `ended` to wait for its end instead.

The pane declaration is authoring structure, not recording configuration. It
cannot be placed in frontmatter, inherited from `recordings/config.yaml`, or
changed with a `rec.*` override.

### Presentation And Realtime Timing

Actions use `timing: presentation` by default. Presentation timing reconstructs
captured states and lets OmegaFlow pace them with narration. Use
`timing: realtime` when the observed duration and motion are part of the result,
such as a terminal progress display or a browser animation.

Timing can be set on an outer beat, a pane beat, or an executable action. The
nearest explicit value wins:

```yaml
beat:
  id: compare-timing
  timing: realtime
  panes:
    preview:
    - id: presentation-preview
      timing: presentation
      actions:
      - id: open
        open_page: {url: /preview}
      - id: animate
        timing: realtime
        click:
          target: {role: button, name: Animate}
        until:
          visible: {text: Complete, exact: true}
```

A terminal action containing `commands` supplies their timing default; an
explicit timing value on one command overrides it. Browser actions with
asynchronous work can add `until` only when their resolved timing is realtime.
The condition uses the same typed shape as `wait_for` and bounds the captured
interval through observable completion.

## Synchronizing Narration And Commands

The mental model is:

- **`after: "@anchor@"`: the command waits for narration.** The command starts
  when narration reaches the named anchor.
- **`@wait:command_id@`: narration waits for the command.** Narration resumes
  after the command with that `id` finishes.

This synchronization is applied during the processing stage. OmegaFlow first
captures the command session and obtains the narration timing, then uses the
markers to construct the final presentation timeline. The recorded shell does
not wait for live narration while commands are being captured.

Think of narration and commands as two concurrent timelines with two
synchronization points:

```mermaid
flowchart LR
    N1[Narration speaks] --> A(("@anchor@"))
    A --> N2[Narration continues] --> W(("@wait:command_id@")) --> N3[Narration resumes]
    A -->|after releases command| C1[Command runs] --> C2[Command completes] --> W
```

Use both directions when narration introduces a command, the command runs for
an unpredictable amount of time, and narration should discuss the result only
after it is ready.

### The Command Waits For Narration: Anchors And `after`

An anchor such as `@install@` identifies a point in the processed narration
timeline. A command with `after: "@install@"` appears to start when playback
reaches that anchor:

```yaml
narration: First, @install@ install the package.
actions:
- commands:
  - id: install_command
    run: python -m pip install omegaflow
    after: "@install@"
```

The anchor name is local timing vocabulary chosen by the recording author. The
command `id` is separate: it identifies the command in the presentation so
other timing directives can refer to its completion.

Quote anchor values used in YAML. `after: "@install@"` is valid, while the
unquoted `after: @install@` is not a valid YAML scalar.

### Narration Waits For The Command: `@wait:...@`

A wait marker points in the opposite direction. It pauses the narration
timeline at the marker until the command with the referenced `id` has finished:

```yaml
narration: >-
  First, @install@ install the package.
  @wait:install_command@ Now inspect the result.
```

Here, `@wait:install_command@` prevents “Now inspect the result” from being
spoken while `install_command` is still running. This is completion-based
synchronization, so it remains correct when command duration varies between
recordings or machines.

In an explicit multi-pane beat, a wait can target an action in any pane. The
target action id must be unique within that outer beat so OmegaFlow can resolve
the completion milestone unambiguously.

Add an optional gap when the result needs a little time to settle visually:

```text
@wait:build+250ms@
@wait:build+1s@
@wait:build+1.5s@
```

The gap guarantees a minimum pause between command completion and narration
continuing. Supported units are milliseconds (`ms`) and seconds (`s`). Without
a gap, narration may resume as soon as the command completes.

### Complete Example

This beat starts the command from narration, then waits for that same command
before continuing:

```yaml
beat:
  id: install
  heading: Install OmegaFlow
  narration: >-
    First, @install@ install the package.
    @wait:install_command+300ms@ Now the command is ready.
  actions:
  - commands:
    - id: install_command
      run: python -m pip install omegaflow
      after: "@install@"
```

Anchor and wait markers are timing instructions, not spoken content. OmegaFlow
removes them before generating narration audio. Timing markers require
`audio.enabled: true`.

## Actions And Checks

### Terminal beats

Actions and checks can run a single inline command:

```yaml
actions:
- run: printf 'hello\n'
  display: printf 'hello\n'
```

Or a script file, resolved relative to the video directory:

```yaml
actions:
- run_file: scripts/hello.sh
  display: bash scripts/hello.sh
```

For multi-command actions, use `commands`:

```yaml
actions:
- commands:
  - id: prepare_output
    run: mkdir -p build
  - id: write_output
    run: printf 'hello\n' > build/message.txt
  expect:
    file_exists:
    - build/message.txt
```

Step fields:

| Field | Type | Notes |
| --- | --- | --- |
| `run` | string | Inline shell command. |
| `run_file` | string | Shell script file to read and execute. |
| `display` | string | Command text shown in the terminal. |
| `name` | string | Check/setup/cleanup label. |
| `after` | string | Anchor syntax such as `@server@`. |
| `progress` | list | Progress labels for visible command chunks. |
| `output` | string or mapping | Show real output, suppress it, or replace it with configured text. |
| `expect` | mapping | Exit code, output, regex, or file-existence expectations. |
| `commands` | list | Command entries for one action. |

Command entries also accept `id`, `show_prompt_after`, `timing`, `with_env`,
`input`, and pre/post command pause fields.

`with_env` is a narrow exception for trusted nested OmegaFlow operations that
need an OmegaFlow service credential:

```yaml
actions:
- commands:
  - id: build_test_video
    run: omegaflow recording=test-video action=build
    with_env:
    - OPENAI_OMEGAFLOW_API_KEY
```

The allowlisted value reaches that command and its descendants only. It is
removed before the next command, unavailable to setup, cleanup, checks, and
browser actions, and rejected if the command emits it. A recording using
`with_env` is always captured again instead of reusing an earlier capture.

Command output is buffered until the command finishes by default. Set
`timing: realtime` to run the command in a pseudo-terminal and preserve its live
output timing and terminal-state updates. Use realtime timing for progress bars
and terminal interfaces that redraw in place. Output replacement and suppression
remain non-streaming.

#### Drive a realtime terminal interface

Add ordered `input` steps to a realtime command when OmegaFlow needs to operate
an interactive terminal program:

```yaml
actions:
- commands:
  - run: nano artwork.svg
    timing: realtime
    input:
    - wait_for: Write Out
      timeout: 5
    - key: down
    - control: k
    - key: up
    - key: end
    - key: enter
    - text: Updated title
      interval: 0.02
    - control: o
    - wait_for: File Name to Write
      timeout: 5
    - key: enter
    - control: x
```

Each input step defines exactly one operation:

| Operation | Behavior |
| --- | --- |
| `wait_for` | Wait for a new occurrence of the text in captured terminal output. An optional `timeout` is measured in seconds and defaults to 10. |
| `text` | Type text into the program. Newlines submit Enter. An optional `interval` controls seconds between characters and defaults to 0.035. |
| `key` | Press `enter`, `tab`, `escape`, `backspace`, `delete`, an arrow key, `home`, `end`, `page_up`, or `page_down`. |
| `control` | Press Control with one ASCII letter, such as `x`. |
| `pause` | Wait for the specified number of seconds before the next input step. |

Input is supported only on `timing: realtime` command entries with real output.
OmegaFlow waits for the program to exit after all input has been sent. A
readiness timeout or a program that exits before its remaining input steps
fails the recording and identifies the unfinished step.

#### Hand a browser URL to a browser pane

Use `browser_handoff.target` when a real, blocking OmegaFlow command opens its
managed browser and a named browser pane should take control. OmegaFlow keeps
the command running, captures its real output, routes the reported URL to the
named pane, and releases the command after that pane beat ends. The current
handoff integration is supported by OmegaFlow's managed `action=watch` browser
launch; it does not intercept arbitrary system browser commands.

```yaml
beat:
  id: inspect-player
  heading: Inspect the player
  narration: Open the generated player.
  layout:
    areas:
    - [terminal, player]
  panes:
    terminal:
    - id: watch
      actions:
      - id: watch_command
        run: omegaflow recording=demo action=watch
        browser_handoff:
          target: player
        timing: realtime
    player:
    - id: inspect
      actions:
      - id: open-player
        open_page:
          handoff: watch_command
          display_url: https://app.example.com/player
```

The handoff command must have an explicit `id`, use real output, and be the
last command in its terminal pane beat. The target must name a declared browser
pane containing exactly one matching `open_page.handoff` consumer.
`browser_handoff: true` is a shortcut only when the recording declares exactly
one browser pane; OmegaFlow rejects it as ambiguous otherwise.

Set `display_url: $handoff` only when the exact captured URL is safe to publish.
For authentication and other token-bearing URLs, use a static, sanitized
`display_url` as shown above. The display URL changes only the browser chrome;
the recorder still navigates to the handed-off URL.

### Highlight pane text during narration

Use a highlight effect to draw attention to text already visible in a pane.
Its `start` and `end` values reference local narration anchors, so the
highlight follows the spoken words when narration is regenerated or retimed.
In an explicit multi-pane beat, `pane` is required. Single-pane shorthand
implicitly targets its `main` pane.

```yaml
narration: >-
  The command creates the @settings_start@ project settings,
  @settings_end@ then continues.
effects:
- highlight:
    pane: output
    targets:
    - text: |-
        audio:
          enabled: true
    - text: recordings/config.yaml
      occurrence: 2
    - regex: 'completed after [0-9]+\.[0-9]+s'
    start: "@settings_start@"
    end: "@settings_end@"
```

Each target contains exactly one matcher:

- `text` matches literally and may span multiple lines.
- `regex` matches changing or variable text with a portable regular expression.

Put several targets in one effect to highlight disjoint text at the same time.
Set the optional, one-based `occurrence` on a target when its match appears
more than once; matching is deterministic and starts at the beginning of the
rendered terminal buffer. Active regex highlights are recalculated when
terminal output redraws, so the emphasis follows changing status text.
Visualization targets are resolved against the active pane beat's displayed
text and retain its syntax coloring.

Set `color: brand` for a purple explanatory callout. The default
`color: cue` uses gold for the text being demonstrated.

Regex patterns are limited to 256 characters and use an RE2-compatible,
linear-time engine in the player. Groups and alternation are supported.
Patterns cannot match empty text or use backreferences, lookaround, possessive
quantifiers, or engine-specific character escapes.

Highlights are valid on terminal and visualization text surfaces and require
`audio.enabled: true` because their timing comes from narration timestamps.

### Browser beats

Browser actions operate one persistent Playwright page shared by every browser
beat in the recording. Terminal and browser beats also share the same process
environment, so a terminal beat can start a local server that a later browser
beat visits.

Hide the mouse cursor in a browser beat that does not demonstrate a pointer
action. The override applies for the whole beat; later beats return to their own
override or the recording-wide `presentation.browser.pointer.visible` default.

```yaml
- id: inspect-result
  medium: browser
  pointer:
    visible: false
```

Browser beats inherit their window and browser chrome from the recording's
`presentation.browser` defaults. Override either mode on a beat when its
captured viewport should appear without one or both decorations:

```yaml
- id: full-application-view
  medium: browser
  window:
    mode: none
  chrome:
    mode: hidden
```

Window mode is `none` or `framed`. Chrome mode is `hidden`, `minimal`, or
`full`. The recording-level window theme and title continue to apply when a
beat changes only the window mode.

```yaml
- id: create-project
  medium: browser
  heading: Create a project
  narration: >-
    @open@ Open the application, then @name@ enter a project name.
    @wait:project_ready+300ms@ The project is ready.
  guide:
    success_hint: The new project appears in the project list.
  actions:
  - id: open
    open_page:
      url: /projects
      display_url: https://app.example.com/projects
      ready:
        visible: {role: heading, name: Projects}
  - id: new-project
    click:
      target: {role: button, name: New project}
  - id: name
    fill:
      target: {label: Project name}
      text: OmegaFlow demo
  - id: submit
    press:
      key: Enter
      target: {label: Project name}
  - id: project_ready
    wait_for:
      visible: {text: OmegaFlow demo}
  checks:
  - name: project was created
    visible: {text: OmegaFlow demo}
```

Each action has an `id` and exactly one operation:

| Operation | Purpose |
| --- | --- |
| `open_page` | Navigate, optionally show loading, and wait for a readiness boundary. |
| `click` | Click a semantic target. |
| `move_pointer` | Move the pointer to viewport-relative coordinates or a normalized position within a semantic target. |
| `drag` | Hold the primary pointer button while moving between two semantic targets. |
| `set_pointer` | Show or hide the recorded pointer without moving it. |
| `fill` | Set a field efficiently; this is the default for text entry. |
| `type_text` | Replace a field by visibly typing text, including the page's response to each character. |
| `press` | Send a key or shortcut, optionally to a target. |
| `scroll` | Scroll the page, a target, or a container. |
| `wait_for` | Synchronize with a visible, hidden, URL, or network-response condition. |

Use `fill` when only the resulting value matters. Use `type_text` when the
edit itself belongs in the video. `type_text.interval_ms` controls the delay
between characters and defaults to 50 milliseconds.

Use `move_pointer.viewport` for a stable location relative to the browser
viewport. Both coordinates are normalized from `0` to `1`:

```yaml
- id: point-near-top
  move_pointer:
    viewport: {x: 0.4, y: 0.12}
```

Use `move_pointer.target` to resolve one visible DOM element. It moves to the
center by default:

```yaml
- id: point-at-submit
  move_pointer:
    target: {role: button, name: Submit}
```

Add normalized `position` coordinates to choose a point within that element;
`{x: 0, y: 0}` is its top-left and `{x: 1, y: 1}` is its bottom-right:

```yaml
- id: point-inside-timeline-section
  move_pointer:
    target: {test_id: timeline-section}
    position: {x: 0.5, y: 0.5}
```

Pointer moves use the same deterministic motion as the movement before a
click. The common action fields `timing`, `after`, `hold_before_ms`, and
`hold_after_ms` can choose presentation or realtime behavior, synchronize the
move with narration, pause before it starts, and keep the pointer at its
destination.

Use `drag.from` and `drag.to` to resolve the source and destination elements.
Each endpoint defaults to the element center and accepts the same normalized
component-relative `position` as a targeted pointer move:

```yaml
- id: move-sun
  drag:
    from:
      target: {test_id: sun}
      position: {x: 0.5, y: 0.5}
    to:
      target: {test_id: sky}
      position: {x: 0.75, y: 0.25}
```

The generated player shows the pointer held down throughout the drag. Because
both endpoints are semantic and component-relative, the action remains stable
when the page or player is resized.

Use `set_pointer` when the pointer should appear only for a specific part of a
browser beat:

```yaml
- id: show-pointer
  set_pointer: {visible: true}
- id: point-at-submit
  move_pointer:
    target: {role: button, name: Submit}
- id: hide-pointer
  set_pointer: {visible: false}
```

`hold_before_ms` and `hold_after_ms` are presentation timing: they do not slow
down browser capture or require recapturing the page. A changed hold recompiles
the presentation from the existing capture.

Prefer targets based on `role` and `name`, `label`, `placeholder`, `text`, or
`test_id`. CSS and XPath are available as best-effort escape hatches and emit a
non-blocking portability warning. Checks validate the real browser state during
capture but are not published as presentation events.

`open_page` waits for `DOMContentLoaded` by default. Use `ready` when the page
has an application-specific readiness condition. `loading: show` includes the
loading stage in the presentation; the default `hide` starts the visible action
at the ready state. `display_url` changes only the safe URL shown in browser
chrome and never changes navigation.

Actions use presentation timing by default: OmegaFlow reconstructs their
visible state and can retime it with narration. Set `timing: realtime` when the
recorded browser motion itself must remain in the video. For asynchronous page
work, add `until` to the triggering action using the same condition shape as
`wait_for`; capture then continues until that bounded condition succeeds.
A standalone realtime `wait_for` uses its own condition and does not also
define `until`.

Automatic dynamic segments retain a three-second safety limit, including any
final-frame alignment. Explicit realtime segments use the action's completion
and optional `until` timeout as their authored boundary, then may retain
additional video through the rendered frame where that condition completed.
All realtime segments remain subject to the encoded-size limit. `transition`
controls only the change between stable states and accepts `cut` or `fade`.

Add `audio: capture` to a realtime browser action when audio produced by the
page is part of the observed result:

```yaml
- id: play-preview
  timing: realtime
  audio: capture
  click:
    target: {role: button, name: Play}
  until:
    visible: {text: Complete, exact: true}
```

OmegaFlow captures media-element and Web Audio output from the active top-level
document inside its isolated browser, then muxes it into the action's video
fragment. The player therefore uses one media clock for play, pause, seek,
playback speed, and mute. Audio capture is explicit, is unavailable on
`open_page` because navigation replaces the document-local recorder, and is
unavailable on the reconstruction-only `set_pointer` action because it creates
no realtime fragment. Capture failures stop the build rather than silently
publishing a muted fragment.

### Narration takes across beats

Omitting `narration_take` keeps the common case local to one beat. Give adjacent
beats the same explicit id when a sentence or paragraph should flow naturally
across their boundary:

```yaml
- id: introduce
  narration_take: project-creation
  narration: First, open the project list,
- id: create
  medium: browser
  narration_take: project-creation
  narration: then create a new project.
```

OmegaFlow derives all implicit singleton takes before checking that each take
is contiguous. Reordering beats covered by a multi-beat take is allowed but
emits a non-blocking review warning because the narration may need rewriting.

## Controlling Visible Command Output

Real command output is the default and should be preferred. Use `output` when
the raw terminal output would distract from the recording:

- `real` shows the command's actual stdout and stderr.
- `suppress` runs the command without showing its output.
- `output.replace` runs the command but replaces its visible output with the
  configured text.

Replacement output is useful for uncommon cases where real output is noisy,
unstable, or contains irrelevant machine-specific detail. Use it sparingly: the
replacement must not claim a result that OmegaFlow did not actually verify.
Pair it with an expectation that validates the real result, and explain the
substitution in a nearby source comment.

```yaml
actions:
- run: ./scripts/build-package.sh
  display: ./scripts/build-package.sh
  output:
    replace: |
      Built dist/example.whl
  expect:
    file_exists:
    - dist/example.whl
```

Here the real build runs and the file expectation proves that it produced the
artifact. Only the verbose build log is replaced with a concise line for the
viewer.

## Guide

`guide` adds prompts to the player. Use `summary` to author the checkpoint's
main instruction and `success_hint` to describe the expected result or next
step. Terminal beats can also provide commands to copy; browser beats cannot.

```yaml
guide:
  summary: Build the recording before continuing.
  commands:
  - omegaflow recording=hello
  success_hint: The build writes video assets and publish surfaces.
```

Set `presentation.guided: true` in recording configuration to start the player
in guided mode. The player pauses at each guided checkpoint before the next
beat is rendered. Viewers can turn guided mode off for later checkpoints; the
checkpoint already on screen remains until they continue or restart it.

### Highlight a player control

Use `player.highlight` to draw attention to one toolbar control while its beat
is playing. `start` and optional `end` reference narration anchors, so the cue
stays synchronized if the narration is regenerated. Without `end`, the cue
lasts through the end of the beat.

```yaml
narration: >-
  This video runs in @guided_start@ guided mode. @guided_end@
player:
  highlight:
    control: guided
    start: "@guided_start@"
    end: "@guided_end@"
```

Supported controls are `previous`, `play`, `restart`, `next`, `guided`, `speed`,
and `mute`. Clicking the highlighted control dismisses its cue, and moving to
the next beat clears it. Player highlights require `audio.enabled: true`
because their timing comes from narration timestamps.

## Schema

This schema block is generated from `src/omegaflow/studio_config.py`
during the website build.

<details>
<summary>Beat schema</summary>

<!-- recording-beat-schema:start -->

```python
@dataclass
class RecordingExpectationConfig:
    exit_code: int = 0
    output_contains: list[str] = field(default_factory=list)
    output_regex: list[str] = field(default_factory=list)
    file_exists: list[str] = field(default_factory=list)


@dataclass
class RecordingInvocationConfig:
    run: str | None = None
    run_file: str | None = None
    display: str | None = None
    after: str | None = None
    output: str | dict[str, str] | None = None
    expect: RecordingExpectationConfig = field(
        default_factory=RecordingExpectationConfig
    )


@dataclass
class TerminalInputStepConfig:
    wait_for: str | None = None
    text: str | None = None
    key: str | None = None
    control: str | None = None
    pause: float | None = None
    timeout: float | None = None
    interval: float | None = None


@dataclass
class BrowserHandoffConfig:
    target: str = ""


@dataclass
class RecordingCommandConfig(RecordingInvocationConfig):
    id: str | None = None
    browser_handoff: bool | BrowserHandoffConfig = False
    with_env: list[str] = field(default_factory=list)
    show_prompt_after: bool = True
    timing: ActionTiming = ActionTiming.presentation
    pre_command_pause: float | None = None
    pre_enter_pause: float | None = None
    post_enter_pause: float | None = None
    post_command_pause: float | None = None
    input: list[TerminalInputStepConfig] = field(default_factory=list)


@dataclass
class RecordingStepConfig(RecordingInvocationConfig):
    name: str | None = None
    progress: list[str] = field(default_factory=list)
    commands: list[RecordingCommandConfig] | None = None


@dataclass
class BrowserTargetConfig:
    role: str | None = None
    name: str | None = None
    label: str | None = None
    placeholder: str | None = None
    text: str | None = None
    test_id: str | None = None
    css: str | None = None
    xpath: str | None = None
    exact: bool = False


@dataclass
class BrowserUrlMatcherConfig:
    equals: str | None = None
    contains: str | None = None
    matches: str | None = None


@dataclass
class BrowserResponseMatcherConfig(BrowserUrlMatcherConfig):
    method: str | None = None
    status: int | None = None


@dataclass
class BrowserStateMatcherConfig:
    visible: BrowserTargetConfig | None = None
    hidden: BrowserTargetConfig | None = None
    url: BrowserUrlMatcherConfig | None = None
    response: BrowserResponseMatcherConfig | None = None


@dataclass
class BrowserConditionConfig(BrowserStateMatcherConfig):
    timeout_ms: int | None = None


@dataclass
class BrowserOpenPageConfig:
    url: str | None = None
    handoff: str | None = None
    display_url: str | None = None
    lifecycle: str = "domcontentloaded"
    ready: BrowserConditionConfig | None = None
    loading: str = "hide"
    timeout_ms: int | None = None


@dataclass
class BrowserClickConfig:
    target: BrowserTargetConfig = field(default_factory=BrowserTargetConfig)
    button: str = "left"
    position: str | dict[str, float] = "center"


@dataclass
class BrowserViewportPointConfig:
    x: float = 0.5
    y: float = 0.5


@dataclass
class BrowserMovePointerConfig:
    viewport: BrowserViewportPointConfig | None = None
    target: BrowserTargetConfig | None = None
    position: BrowserViewportPointConfig | None = None


@dataclass
class BrowserDragEndpointConfig:
    target: BrowserTargetConfig = field(default_factory=BrowserTargetConfig)
    position: BrowserViewportPointConfig | None = None


@dataclass
class BrowserSecretConfig:
    env: str = ""
    presentation: str = "masked"
    placeholder: str | None = None


@dataclass
class BrowserFillConfig:
    target: BrowserTargetConfig = field(default_factory=BrowserTargetConfig)
    text: str | None = None
    secret: BrowserSecretConfig | None = None


@dataclass
class BrowserTypeTextConfig(BrowserFillConfig):
    interval_ms: int = 50


@dataclass
class BrowserPressConfig:
    key: str = ""
    target: BrowserTargetConfig | None = None


@dataclass
class BrowserScrollOffsetConfig:
    x: int = 0
    y: int = 0


@dataclass
class BrowserScrollConfig:
    target: BrowserTargetConfig | None = None
    by: BrowserScrollOffsetConfig | None = None
    to: BrowserScrollOffsetConfig | None = None
    container: BrowserTargetConfig | None = None


@dataclass
class BrowserWaitForConfig(BrowserConditionConfig):
    pass


@dataclass
class BrowserActionConfig:
    id: str = ""
    open_page: BrowserOpenPageConfig | None = None
    click: BrowserClickConfig | None = None
    move_pointer: BrowserMovePointerConfig | None = None
    drag: dict[str, BrowserDragEndpointConfig] | None = None
    set_pointer: BrowserSetPointerConfig | None = None
    fill: BrowserFillConfig | None = None
    type_text: BrowserTypeTextConfig | None = None
    press: BrowserPressConfig | None = None
    scroll: BrowserScrollConfig | None = None
    wait_for: BrowserWaitForConfig | None = None
    until: BrowserConditionConfig | None = None
    timing: ActionTiming = ActionTiming.presentation
    audio: BrowserAudioMode | None = None
    after: str | None = None
    hold_before_ms: int | None = None
    hold_after_ms: int | None = None
    transition: str | None = None
    display_url_after: str | None = None


@dataclass
class BrowserTextCheckConfig(BrowserUrlMatcherConfig):
    target: BrowserTargetConfig = field(default_factory=BrowserTargetConfig)


@dataclass
class BrowserCountCheckConfig:
    target: BrowserTargetConfig = field(default_factory=BrowserTargetConfig)
    equals: int | None = None


@dataclass
class BrowserCheckConfig(BrowserStateMatcherConfig):
    name: str = ""
    text: BrowserTextCheckConfig | None = None
    value: BrowserTextCheckConfig | None = None
    count: BrowserCountCheckConfig | None = None


@dataclass
class RecordingActionConfig(RecordingStepConfig):
    """Structured YAML envelope for terminal and browser actions."""

    id: str = ""
    timing: ActionTiming | None = None
    audio: BrowserAudioMode | None = None
    open_page: BrowserOpenPageConfig | None = None
    click: BrowserClickConfig | None = None
    move_pointer: BrowserMovePointerConfig | None = None
    drag: dict[str, BrowserDragEndpointConfig] | None = None
    set_pointer: BrowserSetPointerConfig | None = None
    fill: BrowserFillConfig | None = None
    type_text: BrowserTypeTextConfig | None = None
    press: BrowserPressConfig | None = None
    scroll: BrowserScrollConfig | None = None
    wait_for: BrowserWaitForConfig | None = None
    until: BrowserConditionConfig | None = None
    hold_before_ms: int | None = None
    hold_after_ms: int | None = None
    transition: str | None = None
    display_url_after: str | None = None


@dataclass
class RecordingCheckConfig(RecordingStepConfig):
    """Structured YAML envelope for terminal and browser checks."""

    url: BrowserUrlMatcherConfig | None = None
    visible: BrowserTargetConfig | None = None
    hidden: BrowserTargetConfig | None = None
    text: BrowserTextCheckConfig | None = None
    value: BrowserTextCheckConfig | None = None
    count: BrowserCountCheckConfig | None = None
    response: BrowserResponseMatcherConfig | None = None


@dataclass
class RecordingGuideConfig:
    commands: list[str] = field(default_factory=list)
    summary: str | None = None
    success_hint: str | None = None


@dataclass
class TextHighlightTargetConfig:
    text: str | None = None
    regex: str | None = None
    occurrence: int = 1


@dataclass
class TextHighlightConfig:
    pane: str | None = None
    targets: list[TextHighlightTargetConfig] = field(default_factory=list)
    color: TerminalHighlightColor = TerminalHighlightColor.cue
    start: str = ""
    end: str = ""


@dataclass
class BeatEffectConfig:
    """Typed envelope for presentation effects owned by a container beat."""

    highlight: TextHighlightConfig | None = None


class PaneTitleAlignmentX(str, Enum):
    left = "left"
    right = "right"


class PaneTitleAlignmentY(str, Enum):
    top = "top"
    bottom = "bottom"


@dataclass
class PaneTitleConfig:
    visible: bool = True
    text: str | None = None
    alignment_x: PaneTitleAlignmentX = PaneTitleAlignmentX.right
    alignment_y: PaneTitleAlignmentY = PaneTitleAlignmentY.top
    position_x: str = "0.25rem"
    position_y: str = "0.25rem"


@dataclass
class PaneConfig:
    id: str = ""
    kind: PaneKind = PaneKind.visualization
    title: Literal["hidden"] | str | PaneTitleConfig | None = None


@dataclass
class PaneTransitionConfig:
    kind: PaneTransitionKind = PaneTransitionKind.cut
    duration_ms: int = 0


@dataclass
class PaneLayoutConfig:
    areas: list[list[str]] = field(default_factory=list)


@dataclass
class VisualizationShowConfig:
    language: str = "text"
    text: str = ""


@dataclass
class PaneActionConfig(RecordingActionConfig):
    show: VisualizationShowConfig | None = None
    browser_handoff: bool | BrowserHandoffConfig = False
    pre_command_pause: float | None = None
    pre_enter_pause: float | None = None
    input: list[TerminalInputStepConfig] = field(default_factory=list)


@dataclass
class PaneBeatConfig:
    id: str = ""
    after: str | None = None
    timing: ActionTiming | None = None
    transition: PaneTransitionConfig = field(default_factory=PaneTransitionConfig)
    pointer: BrowserPointerPresentationConfig | None = None
    window: BrowserWindowModeConfig | None = None
    chrome: BrowserChromePresentationConfig | None = None
    actions: list[PaneActionConfig] = field(default_factory=list)
    checks: list[RecordingCheckConfig] = field(default_factory=list)


@dataclass
class OuterBeatPaneTrackConfig:
    pane_id: str = ""
    beats: list[PaneBeatConfig] = field(default_factory=list)


@dataclass
class RecordingBeatConfig:
    id: str = ""
    medium: RecordingMedium = RecordingMedium.terminal
    timing: ActionTiming | None = None
    heading: str = ""
    narration: str = ""
    narration_take: str | None = None
    marker: str | None = None
    caption: str | None = None
    viewer_hold: float | None = None
    pointer: BrowserPointerPresentationConfig | None = None
    window: BrowserWindowModeConfig | None = None
    chrome: BrowserChromePresentationConfig | None = None
    player: BeatPlayerConfig | None = None
    actions: list[RecordingActionConfig] = field(default_factory=list)
    checks: list[RecordingCheckConfig] = field(default_factory=list)
    effects: list[BeatEffectConfig] = field(default_factory=list)
    guide: RecordingGuideConfig | None = None
    layout: PaneLayoutConfig | None = None
    panes: dict[str, list[PaneBeatConfig]] = field(default_factory=dict)
```

<!-- recording-beat-schema:end -->

</details>
