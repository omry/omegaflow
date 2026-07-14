---
sidebar_position: 3
sidebar_label: Recording Configuration
---

# Recording Configuration

Every recording gets its config from three layers. The same schema is used for
shared workspace defaults and for the per-recording frontmatter block.

## Override Order

OmegaFlow builds the final recording config in this order:

1. Schema default values.
2. `<recording-dir>/config.yaml`, the workspace defaults for recordings.
3. The per-recording config block in `<recording-dir>/<id>/index.md` frontmatter.
4. CLI `rec.*` overrides, such as `rec.capture.headless=false`.

Later layers override earlier layers. `id` and `title` are recording identity
fields; they belong in frontmatter and are rejected in workspace `config.yaml`.

This page starts after OmegaFlow has selected the recording workspace. Tool-level
settings such as which directory to use are documented in
[Project Configuration](../configuration.md).

## Command Line Overrides

Use `rec.*` CLI overrides for temporary changes to the resolved recording config:

```bash
omegaflow recording=hello rec.capture.headless=false
omegaflow recording=hello rec.style.typing=false
omegaflow recording=hello rec.audio.enabled=false
```

`rec.*` overrides are merged after frontmatter, so they can override values from
both `config.yaml` and the recording header. They are best for scalar values and
small config maps. For larger recording structure such as beats, commands, and
narration, edit the recording Markdown file instead. Recording identity and
generated fields such as `id`, `title`, and `script` cannot be overridden with
`rec.*`.

## Composition And Interpolation

OmegaFlow uses OmegaConf syntax for interpolations:

```yaml
outputs:
  dir: recordings/.omegaflow/videos
  asset_dir: ${outputs.dir}/${id}
  cast: ${outputs.asset_dir}/recording.cast
```

Interpolations are evaluated lazily when the composed config is accessed, not
when an individual file or directive block is first parsed. This lets schema
defaults, workspace defaults, frontmatter, and directive-derived values refer to
the final composed recording object.

All fenced `studio-directive` blocks in the recording Markdown body are parsed
as config fragments and folded into the same recording object. For example, beat
directives contribute to `beats`, and the scene/narration directives contribute
to generated narration config. Because directive blocks are combined before
interpolation resolution, references can use values from the final recording
config rather than only values from the local block.

## Workspace Defaults

The workspace `config.yaml` is good for defaults that should apply to many
recordings:

```yaml
capture:
  window_size: 80x20
  headless: true
  baseline_compressed: true
style:
  color: true
  typing: true
outputs:
  dir: recordings/.omegaflow/videos
audio:
  enabled: false
  provider: openai
  env_file: .env
  env: OPENAI_OMEGAFLOW_API_KEY
  model: gpt-4o-mini-tts
  voice: marin
  format: mp3
```

Enabling narration requires FFmpeg tools and OpenAI API access when generating
new audio. See [Runtime Dependencies](../runtime-dependencies.md).

## Recording Frontmatter

Each `<id>/index.md` recording starts with YAML frontmatter:

```yaml
---
id: hello
title: Hello Video
publish:
  default: html
  surfaces:
    html:
      type: standalone_html
      file: ${outputs.asset_dir}/index.html
audio:
  enabled: false
---
```

The frontmatter header is the right place for recording-specific config:

- `id` and `title`
- one-off output overrides
- one-off audio settings
- recording-local setup, cleanup, or configured beats
- publish target choices for that recording

## Structure

| Field | Type | Notes |
| --- | --- | --- |
| `id` | string | Required per recording. Used by `omegaflow recording=<id>`. Nested ids such as `tutorial/install` are supported. Frontmatter only. |
| `title` | string | Human-readable title for players and publish surfaces. Frontmatter only. |
| `parameters` | mapping | Script parameters and defaults for `script_params`. |
| `requirements` | mapping | Required shell commands and tools. |
| `capture` | mapping | Terminal recording settings such as `window_size`, `headless`, and `baseline_compressed`. |
| `style` | mapping | Rendering behavior such as color and typing simulation. |
| `outputs` | mapping | Output paths for the per-video asset directory, cast, audio, and related generated files. |
| `timing` | mapping | Presentation timing and playback controls. |
| `environment` | mapping | Working directory, environment values, and `path_prepend`. |
| `audio` | mapping | Narration audio configuration. |
| `browser` | mapping or null | Deterministic Playwright capture profile, viewport, context, authentication, timeouts, and redaction targets. Required when any beat has `medium: browser`. |
| `presentation` | mapping | Recording-wide browser window, chrome, transition, pointer, and typing presentation policy. |
| `publish` | mapping | Publish surfaces such as Docusaurus MDX and standalone HTML. |
| `setup` | list | Commands that run before beats. See [Beat](./beat.md). |
| `cleanup` | list | Commands that run after recording. See [Beat](./beat.md). |
| `beats` | list | Optional configured beats. See [Beat](./beat.md). |

Publishing surface details are covered in
[Publishing And Runtime](./publishing-runtime.md).

## Browser header configuration

Browser capture parameters are recording-wide because every browser beat uses
one persistent page and deterministic viewport:

```yaml
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

`storage_state_env` names an environment variable whose value is a private
Playwright storage-state path. Use `storage_state_path` instead when the path is
safe to keep in recording config. The file content remains private and its hash,
not its secrets, participates in capture freshness.

Presentation framing is also a recording header concern, not a beat setting:

```yaml
presentation:
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
letterboxes it inside any selected window frame.

## Config Schema

This schema block is generated from `src/omegaflow/studio_config.py`
during the website build. Beat, command, and publish detail types are documented
on the [Beat](./beat.md) and
[Publishing And Runtime](./publishing-runtime.md) pages.

<details>
<summary>Config schema</summary>

<!-- recording-config-schema:start -->

```python
@dataclass
class RecordingCaptureConfig:
    window_size: str = "100x28"
    headless: bool = True
    baseline_compressed: bool = False
    idle_time_limit: float | None = None


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
    cast: str = "${outputs.asset_dir}/recording.cast"
    retimed_cast: str | None = None
    audio: str | None = "${outputs.asset_dir}/audio.${audio.format}"
    audio_metadata: str | None = "${outputs.asset_dir}/audio.json"


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
    env: str = "OPENAI_API_KEY"
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
    viewport: BrowserViewportConfig | None = None
    context: BrowserContextConfig | None = None
    auth: BrowserAuthConfig = field(default_factory=BrowserAuthConfig)
    timeouts: BrowserTimeoutsConfig = field(default_factory=BrowserTimeoutsConfig)
    redactions: list[BrowserRedactionConfig] = field(default_factory=list)


@dataclass
class BrowserWindowPresentationConfig:
    mode: str = "none"
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


@dataclass
class RecordingPresentationConfig:
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
class RecordingDefaults:
    studio: dict[str, Any] = field(default_factory=dict)
    parameters: dict[str, Any] = field(default_factory=dict)
    requirements: dict[str, Any] = field(default_factory=dict)
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
    narration: dict[str, Any] = field(default_factory=dict)
    publish: RecordingPublishConfig = field(default_factory=RecordingPublishConfig)
    failure_summary: RecordingFailureSummaryConfig = field(
        default_factory=RecordingFailureSummaryConfig
    )
    setup: list[RecordingStepConfig] = field(default_factory=list)
    cleanup: list[RecordingStepConfig] = field(default_factory=list)
    beats: list[RecordingBeatConfig] = field(default_factory=list)


@dataclass
class RecordingSpec(RecordingDefaults):
    id: str = ""
    title: str | None = None
    script: str | None = None
```

<!-- recording-config-schema:end -->

</details>
