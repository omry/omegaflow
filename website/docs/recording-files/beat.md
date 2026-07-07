---
sidebar_position: 4
sidebar_label: Beat
---

# Beat

A beat is one narrated section of a recording. It can describe what the viewer
is seeing, run terminal actions, run checks, and provide guided-mode prompts.

```yaml
beat:
  id: install
  heading: Install The CLI
  narration: Install the package and confirm the studio command is available.
```

## Fields

| Field | Type | Notes |
| --- | --- | --- |
| `id` | string | Unique beat id. |
| `heading` | string | Section heading and narration label. |
| `narration` | string | Spoken narration text. Supports markers such as `@anchor@` and `@wait:name+1s@`. |
| `marker` | string | Optional UI marker id. |
| `caption` | string | Text printed visibly in the terminal recording. |
| `viewer_hold` | number | Extra viewer pause after the beat. |
| `actions` | list | Commands to record. |
| `checks` | list | Commands that validate the result. |
| `guide` | mapping | Guided-mode commands and success hint. |

## Actions And Checks

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
  - run: python -m pip install omegaflow
    display: python -m pip install omegaflow
    output:
      mode: fake
      text: |
        Successfully installed omegaflow
  expect:
    exit_code: 0
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
| `output` | string or mapping | `real`, `suppress`, or `fake` output behavior. |
| `expect` | mapping | Exit code, output, regex, or file-existence expectations. |
| `commands` | list | Command entries for one action. |

Command entries also accept `id`, `follow_along`, `show_prompt_after`,
`retime`, and pre/post command pause fields.

`output: fake` still runs the command. OmegaFlow hides the real stdout/stderr in
the recording and displays `output.text` instead. Use a support script or
controlled environment when the displayed command should be safe and
reproducible during recording.

## Guide

`guide` adds guided-mode prompts to the player:

```yaml
guide:
  commands:
  - studio recording=hello action=build
  success_hint: The build writes a retimed cast and publish surfaces.
```

## Schema

This schema block is generated from `src/omegaflow_studio/studio_config.py`
during the website build.

<details>
<summary>Beat schema</summary>

<!-- recording-beat-schema:start -->

```python
@dataclass
class RecordingCommandConfig:
    id: str | None = None
    run: str | None = None
    run_file: str | None = None
    display: str | None = None
    after: str | None = None
    follow_along: bool = False
    show_prompt_after: bool = True
    output: Any = None
    expect: dict[str, Any] = field(default_factory=dict)
    retime: str = "normal"
    pre_command_pause: float | None = None
    pre_enter_pause: float | None = None
    post_enter_pause: float | None = None
    post_command_pause: float | None = None


@dataclass
class RecordingStepConfig:
    run: str | None = None
    run_file: str | None = None
    display: str | None = None
    name: str | None = None
    after: str | None = None
    progress: list[str] = field(default_factory=list)
    output: Any = None
    expect: dict[str, Any] = field(default_factory=dict)
    commands: list[RecordingCommandConfig] | None = None


@dataclass
class RecordingGuideConfig:
    commands: list[str] = field(default_factory=list)
    success_hint: str | None = None


@dataclass
class RecordingBeatConfig:
    id: str = ""
    heading: str = ""
    narration: str = ""
    marker: str | None = None
    caption: str | None = None
    viewer_hold: float | None = None
    actions: list[RecordingStepConfig] = field(default_factory=list)
    checks: list[RecordingStepConfig] = field(default_factory=list)
    guide: RecordingGuideConfig | None = None
```

<!-- recording-beat-schema:end -->

</details>
