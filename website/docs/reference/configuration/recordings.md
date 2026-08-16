---
sidebar_position: 3
sidebar_label: Recording Configuration
slug: /reference/configuration/recordings/
---

# Recording Configuration

Recording metadata and production configuration have separate owners.
Frontmatter identifies and describes the source; workspace defaults, one
`config` directive, and CLI overrides compose the production configuration.

## Override Order

OmegaFlow builds the final recording config in this order:

1. Schema default values.
2. `<recording-dir>/config.yaml`, the workspace defaults for recordings.
3. The optional `config:` studio directive in `<recording-dir>/<id>/index.md`.
4. CLI `rec.*` overrides, such as `rec.capture.headless=false`.

Later layers override earlier layers. The recording `id` is derived from the
directory path and cannot be authored in config. `title` belongs in frontmatter
and is rejected in workspace `config.yaml`.

This page starts after OmegaFlow has selected the recording workspace. Tool-level
settings such as which directory to use are documented in
[Project Configuration](./project.md).

## Command Line Overrides

Use `rec.*` CLI overrides for temporary changes to the resolved recording config:

```bash
omegaflow recording=hello rec.capture.headless=false
omegaflow recording=hello rec.style.typing=false
omegaflow recording=hello rec.audio.enabled=false
```

`rec.*` overrides are merged after the recording's `config` directive, so they
can override both workspace defaults and per-recording settings. They are best
for scalar values and small config maps. Pane and beat structure is authored in
dedicated directives and cannot be overridden. Recording identity and generated
fields such as `id`, `title`, `script`, `panes`, and `beats` cannot be overridden
with `rec.*`.

## Composition And Interpolation

OmegaFlow uses OmegaConf syntax for interpolations:

```yaml
outputs:
  dir: recordings/.omegaflow/videos
  asset_dir: ${outputs.dir}/${id}
```

Interpolations are evaluated lazily when the composed config is accessed, not
when an individual file or directive block is first parsed. This lets schema
defaults, workspace defaults, the `config` directive, and generated values refer
to the final composed recording object.

All fenced `studio-directive` blocks in the recording Markdown body are parsed
as typed fragments and folded into the same recording object. The singleton
`config` directive contributes per-recording settings, while pane and beat
directives contribute authored structure. Because directive blocks are combined
before interpolation resolution, references can use values from the final
recording config rather than only values from the local block.

## Workspace Defaults

The workspace `config.yaml` is good for defaults that should apply to many
recordings:

```yaml
capture:
  window_size: 80x20
  headless: true
style:
  color: true
  typing: true
outputs:
  dir: recordings/.omegaflow/videos
audio:
  enabled: false
  provider: openai
  env: OPENAI_OMEGAFLOW_API_KEY
  model: gpt-4o-mini-tts
  voice: marin
  format: mp3
```

`capture.timeout` limits how long OmegaFlow waits for one terminal request,
including a command or check. The default is 30 seconds; increase it for an
intentional realtime action that runs longer.

Enabling narration requires FFmpeg tools and OpenAI API access when generating
new audio. Put the local key in `.omegaflow/omegaflow-secret.env`; CI may
provide the same name in the parent process environment. `env_file` remains an
explicit advanced override and is loaded without modifying the process
environment.

The initial Reploy capture path rejects enabled narration with authored takes
before staging. Supplying narration credentials or prebuilt narration artifacts
to the protected controller requires a separate approved delegation contract.

## Recording Frontmatter

Each `<id>/index.md` recording starts with YAML frontmatter:

```yaml
---
kind: video
title: Hello Video
description: A small narrated hello-world recording.
---
```

Video frontmatter accepts only:

- `kind`, `title`, and `description`

`title` is required. `kind` defaults to `video`, and `description` is optional.
The recording id comes from the directory path.

Collections use frontmatter only and accept `kind`, `title`, and `members`.
They cannot contain studio directives.

## Per-recording Config Directive

Put production settings that differ from workspace defaults in one `config`
directive. It must appear before `panes` or `beat`:

```yaml
config:
  publish:
    default: html
    surfaces:
      html:
        type: standalone_html
        file: ${outputs.asset_dir}/index.html
  audio:
    enabled: false
```

## Migrating Existing Recording Sources

The previous authoring shape is not accepted. Update each video source in
three mechanical steps:

1. Leave only `kind`, `title`, and `description` in frontmatter. Move capture,
   audio, publishing, setup, cleanup, and other production settings into one
   leading `config:` studio directive.
2. Delete the `scene:` directive. The recording directory supplies its id and
   frontmatter `title` supplies its internal scene title.
3. Replace a list-valued `beats:` directive with one singular `beat:` studio
   directive per beat, preserving their order. Keep `panes:` once before the
   first beat when the recording uses multiple panes.

## Structure

OmegaFlow derives each recording id from its directory relative to the
recording workspace. For example, `recordings/tutorial/install/index.md` has id
`tutorial/install`. `id` is available in the resolved config for interpolation,
but is not an accepted frontmatter field.

| Field | Type | Notes |
| --- | --- | --- |
| `kind` | `video` or `collection` | Frontmatter source type. Omitted values default to `video`; declare `kind: collection` for a collection. |
| `title` | string | Required frontmatter title for videos and human-readable collection title. Also supplies the generated internal scene title. |
| `description` | string | Optional video summary used when a collection renders its watch index. Frontmatter only. |
| `members` | list | Ordered recording ids for a collection. Collection frontmatter only. |

The `config` directive and workspace `config.yaml` share these fields:

| Field | Type | Notes |
| --- | --- | --- |
| `parameters` | mapping | Script parameters and defaults for `script_params`. |
| `requirements` | mapping | Required shell commands and tools. |
| `capture` | mapping | Recording settings such as `window_size`, `headless`, and `idle_time_limit`. |
| `style` | mapping | Rendering behavior such as color and typing simulation. |
| `outputs` | mapping | Output paths for the per-recording asset and presentation-bundle directories. |
| `timing` | mapping | Presentation timing and playback controls. |
| `environment` | mapping | Working directory, literal values, declared application secrets, and `path_prepend`. |
| `audio` | mapping | Narration audio configuration. |
| `browser` | mapping or null | Deterministic Playwright capture profile, viewport, context, authentication, timeouts, and redaction targets. Required when any beat has `medium: browser`. |
| `presentation` | mapping | Recording-wide browser window, chrome, transition, pointer, and typing presentation policy. |
| `publish` | mapping | Publish surfaces such as Docusaurus MDX and standalone HTML. |
| `failure_summary` | mapping | Presentation cleanup for expected failure output. |
| `narration` | mapping | Recording-wide narration stream settings. |
| `setup` | list | Commands that run before beats. See [Recording schema](../recording-files/schema.md). |
| `cleanup` | list | Commands that run after recording. See [Recording schema](../recording-files/schema.md). |

A collection replaces the video-specific fields with an ordered `members`
list of full recording ids. Collection members must be videos; nested
collections are not supported. Each member's `title` and `description` appear
in the collection watch index.

Publishing surface details are covered in
[Publishing And Runtime](../output/index.md).

## Recorded command environment

OmegaFlow constructs a recording environment instead of copying the process
environment from the machine running the build. This keeps terminal commands,
setup, cleanup, checks, and browser capture independent from unrelated host
settings.

Use the typed `environment` fields to provide deliberate application inputs:

```yaml
environment:
  working_directory: examples/demo
  path_prepend:
  - tools/bin
  variables:
    DEMO_MODE: tutorial
```

- `working_directory` selects the directory used by recorded commands.
- `path_prepend` adds project-relative command locations ahead of OmegaFlow's
  deterministic command path.
- `variables` supplies literal, non-secret application settings.

### Application secrets

Declare each secret application input by name:

```yaml
environment:
  secrets:
  - DEMO_API_TOKEN
```

For local recording, put the value in `app.secret.env` beside that recording's
`index.md`:

```dotenv
DEMO_API_TOKEN=local-token
```

For CI, omit the file and set the declared name in OmegaFlow's parent process
environment instead. Exactly one source must provide each declared name. A
missing value, a value present in both sources, or an undeclared entry in
`app.secret.env` stops the build.

Inside a Git or Sapling repository, OmegaFlow requires a local
`app.secret.env` to be ignored and untracked. It also rejects a symbolic link.
Bootstrap adds `**/app.secret.env` to `recordings/.gitignore`. Secret values
are available to recording setup, actions, checks, cleanup, and browser
processes, but are registered for output redaction and publication validation.
A secret-bearing recording is always captured again rather than reusing a
previous capture.

Application secrets are inputs for the software being recorded. OmegaFlow
service credentials such as `OPENAI_OMEGAFLOW_API_KEY` belong in
`.omegaflow/omegaflow-secret.env` and cannot be declared here. Other host
environment variables are not inherited by recorded commands.

OmegaFlow also sets `OMEGAFLOW_VERSION` to the version performing the
recording. Values configured under `environment.variables` are not printed by
OmegaFlow, but this mapping is **not secret storage**: recorded applications
can read and display them.

## Browser configuration

Browser capture parameters are recording-wide because every browser beat uses
one persistent page and deterministic viewport:

```yaml
environment:
  variables:
    DEMO_STORAGE_STATE: .private/demo-storage-state.json
browser:
  base_url: http://127.0.0.1:3000
  viewport:
    width: 1280
    height: 720
    device_scale_factor: 1
  context:
    locale: en-US
    timezone: UTC
    color_scheme: light
    reduced_motion: reduce
  auth:
    storage_state_env: DEMO_STORAGE_STATE
  timeouts:
    action_ms: 10000
    readiness_ms: 15000
```

`storage_state_env` names an explicitly configured `environment.variables`
entry whose value is a private Playwright storage-state path. Use
`storage_state_path` instead when the path is safe to keep directly in browser
config. The file content remains private and its hash, not its secrets,
participates in capture freshness.

The initial Reploy capture path rejects both browser storage-state options
before staging. Authenticated browser capture requires a separate approved asset
or secret-delegation contract for the protected controller.

The initial Reploy capture path also rejects path-based command `inputs`. Named
producer-output references remain supported, but host files and directories
require a separate controller-asset staging contract before they can participate
in controller-side compilation.

Presentation framing in workspace defaults or the recording's `config`
directive supplies defaults for every browser beat:

```yaml
presentation:
  guided: true
  pane_chrome:
    style: framed
  browser:
    window:
      mode: framed
      theme: kde-breeze
      title: Demo application
      opening_transition: window-open
    chrome:
      mode: minimal
    transitions:
      default: fade
```

The captured viewport never changes during playback. The renderer scales and
letterboxes it inside any selected window frame. Set `guided: true` to start
the player in guided mode; beats with `guide` content then pause before the
following beat is rendered. See [Recording schema](../recording-files/schema.md#guide) for checkpoint and
toolbar-highlight authoring. Multi-pane layouts use
`pane_chrome.style: framed` by default, which adds pane labels, borders, and
renderer-colored accents. Set the style to `none` for an undecorated layout.
Pane declarations and titles are structural directives rather than production
configuration; see [Multi-pane Beats](../recording-files/schema.md#multi-pane-beats). Individual
browser beats can override the window and browser chrome modes; see
[Browser beats](../recording-files/schema.md#browser-beats).

## Config Schema

This schema block is generated from `src/omegaflow/studio_config.py`
during the website build. Beat, command, and publish detail types are documented
on the [Recording schema](../recording-files/schema.md) and
[Publishing And Runtime](../output/index.md) pages.

<details>
<summary>Config schema</summary>

<!-- recording-config-schema:start -->

```python
@dataclass
class RecordingCaptureConfig:
    window_size: str = "100x28"
    headless: bool = True
    idle_time_limit: float | None = None
    timeout: float = 30.0


@dataclass
class RecordingStyleConfig:
    color: bool = True
    typing: bool = True
    typing_min_delay: float = 0.012
    typing_max_delay: float = 0.045
    typing_space_delay: float = 0.025
    typing_punctuation_delay: float = 0.05
    typing_newline_delay: float = 0.16
    typing_seed: int = 17


@dataclass
class RecordingOutputsConfig:
    dir: str = "recordings/.omegaflow/videos"
    asset_dir: str = "${outputs.dir}/${id}"


@dataclass
class RecordingTimingConfig:
    typing_char_delay: float = 0.035
    typing_space_delay: float = 0.02
    typing_punctuation_delay: float = 0.05
    typing_newline_delay: float = 0.0
    post_enter_pause: float = 0.35
    post_command_pause: float = 0.85
    minimum_section_spacing: float = 0.0


@dataclass
class RecordingEnvironmentConfig:
    working_directory: str = "."
    path_prepend: list[str] = field(default_factory=list)
    variables: dict[str, str] = field(default_factory=dict)
    secrets: list[str] = field(default_factory=list)


@dataclass
class RecordingAudioBillingConfig:
    tts_usd_per_1m_characters: float = 15.0
    transcription_usd_per_minute: float = 0.006


@dataclass
class RecordingAudioTranscriptionConfig:
    model: str = "whisper-1"
    timestamp_granularities: list[str] = field(default_factory=lambda: ["word"])


@dataclass
class RecordingAudioConfig:
    enabled: bool = False
    provider: str = "openai"
    env: str = "OPENAI_OMEGAFLOW_API_KEY"
    model: str = "gpt-4o-mini-tts"
    voice: str = "marin"
    format: str = "mp3"
    cache_dir: str = "recordings/.omegaflow/cache/audio"
    env_file: str | None = None
    env_override: bool = False
    instructions: str | None = None
    billing: RecordingAudioBillingConfig = field(
        default_factory=RecordingAudioBillingConfig
    )
    transcription: RecordingAudioTranscriptionConfig = field(
        default_factory=RecordingAudioTranscriptionConfig
    )


@dataclass
class BrowserViewportConfig:
    width: int | None = None
    height: int | None = None
    device_scale_factor: float | None = None


@dataclass
class BrowserContextConfig:
    locale: str | None = None
    timezone: str | None = None
    color_scheme: str | None = None
    reduced_motion: str | None = None
    permissions: list[str] | None = None


@dataclass
class BrowserAuthConfig:
    storage_state_env: str | None = None
    storage_state_path: str | None = None


@dataclass
class BrowserTimeoutsConfig:
    action_ms: int = 10_000
    readiness_ms: int = 15_000


@dataclass
class BrowserRedactionConfig:
    target: BrowserTargetConfig = field(default_factory=BrowserTargetConfig)


@dataclass
class BrowserRecordingConfig:
    profile: str = "desktop-v1"
    base_url: str | None = None
    endpoint_id: str | None = None
    viewport: BrowserViewportConfig | None = None
    context: BrowserContextConfig | None = None
    auth: BrowserAuthConfig = field(default_factory=BrowserAuthConfig)
    timeouts: BrowserTimeoutsConfig = field(default_factory=BrowserTimeoutsConfig)
    redactions: list[BrowserRedactionConfig] = field(default_factory=list)


@dataclass
class BrowserWindowModeConfig:
    mode: str = "none"


@dataclass
class BrowserWindowPresentationConfig(BrowserWindowModeConfig):
    theme: str = "kde-breeze"
    title: str | None = None
    opening_transition: str = "cut"


@dataclass
class BrowserChromePresentationConfig:
    mode: str = "hidden"


@dataclass
class BrowserTransitionsPresentationConfig:
    default: str = "cut"


@dataclass
class BrowserPointerPresentationConfig:
    visible: bool = True


@dataclass
class BrowserTypingPresentationConfig:
    policy: str = "natural-v1"


@dataclass
class BrowserPresentationConfig:
    window: BrowserWindowPresentationConfig = field(
        default_factory=BrowserWindowPresentationConfig
    )
    chrome: BrowserChromePresentationConfig = field(
        default_factory=BrowserChromePresentationConfig
    )
    transitions: BrowserTransitionsPresentationConfig = field(
        default_factory=BrowserTransitionsPresentationConfig
    )
    pointer: BrowserPointerPresentationConfig = field(
        default_factory=BrowserPointerPresentationConfig
    )
    typing: BrowserTypingPresentationConfig = field(
        default_factory=BrowserTypingPresentationConfig
    )


class PaneChromeStyle(str, Enum):
    none = "none"
    framed = "framed"


@dataclass
class RecordingPaneChromeConfig:
    style: PaneChromeStyle = PaneChromeStyle.framed


@dataclass
class RecordingPresentationConfig:
    guided: bool = False
    pane_chrome: RecordingPaneChromeConfig = field(
        default_factory=RecordingPaneChromeConfig
    )
    browser: BrowserPresentationConfig = field(default_factory=BrowserPresentationConfig)


@dataclass
class RecordingFailureAnimationConfig:
    regex: str = ""
    replacement: str = ""


@dataclass
class RecordingFailureSummaryConfig:
    terminal_animations: list[RecordingFailureAnimationConfig] = field(
        default_factory=list
    )


@dataclass
class RecordingRequirementsConfig:
    commands: list[str] = field(default_factory=list)


@dataclass
class RecordingNarrationConfig:
    id: str = "voiceover"


@dataclass
class RecordingDefaults:
    parameters: dict[
        str,
        str | int | float | bool | dict[str, str | int | float | bool],
    ] = field(default_factory=dict)
    requirements: RecordingRequirementsConfig = field(
        default_factory=RecordingRequirementsConfig
    )
    capture: RecordingCaptureConfig = field(default_factory=RecordingCaptureConfig)
    style: RecordingStyleConfig = field(default_factory=RecordingStyleConfig)
    outputs: RecordingOutputsConfig = field(default_factory=RecordingOutputsConfig)
    timing: RecordingTimingConfig = field(default_factory=RecordingTimingConfig)
    environment: RecordingEnvironmentConfig = field(
        default_factory=RecordingEnvironmentConfig
    )
    audio: RecordingAudioConfig = field(default_factory=RecordingAudioConfig)
    browser: BrowserRecordingConfig | None = None
    presentation: RecordingPresentationConfig = field(
        default_factory=RecordingPresentationConfig
    )
    publish: RecordingPublishConfig = field(default_factory=RecordingPublishConfig)
    failure_summary: RecordingFailureSummaryConfig = field(
        default_factory=RecordingFailureSummaryConfig
    )
    narration: RecordingNarrationConfig = field(
        default_factory=RecordingNarrationConfig
    )
    setup: list[RecordingStepConfig] = field(default_factory=list)
    cleanup: list[RecordingStepConfig] = field(default_factory=list)


@dataclass
class RecordingSourceSpec:
    kind: RecordingSourceKind = RecordingSourceKind.video
    title: str | None = None
    description: str | None = None


@dataclass
class RecordingCollectionSourceSpec:
    kind: RecordingSourceKind = RecordingSourceKind.collection
    title: str | None = None
    members: list[str] = field(default_factory=list)
```

<!-- recording-config-schema:end -->

</details>
