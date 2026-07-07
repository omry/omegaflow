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

Later layers override earlier layers. `id` and `title` are recording identity
fields; they belong in frontmatter and are rejected in workspace `config.yaml`.

This page starts after OmegaFlow has selected the recording workspace. Tool-level
settings such as which directory to use are documented in
[OmegaFlow Configuration](../configuration.md).

## Composition And Interpolation

OmegaFlow uses OmegaConf syntax for interpolations:

```yaml
outputs:
  dir: recordings/.omegaflow/videos
  cast: ${outputs.dir}/${id}.cast
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
      file: ${outputs.dir}/${id}.html
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
| `outputs` | mapping | Output paths for cast, audio, and related generated files. |
| `retime` | mapping | Timing and playback retime controls. |
| `environment` | mapping | Working directory, environment values, and `path_prepend`. |
| `audio` | mapping | Narration audio configuration. |
| `publish` | mapping | Publish surfaces such as Docusaurus MDX and standalone HTML. |
| `setup` | list | Commands that run before beats. See [Beat](./beat.md). |
| `cleanup` | list | Commands that run after recording. See [Beat](./beat.md). |
| `beats` | list | Optional configured beats. See [Beat](./beat.md). |

Publishing surface details are covered in
[Publishing And Runtime](./publishing-runtime.md).

## Config Schema

This schema block is generated from `src/omegaflow_studio/studio_config.py`
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
    cast: str = "${outputs.dir}/${id}.cast"
    retimed_cast: str | None = None
    audio: str | None = None
    audio_metadata: str | None = None


@dataclass
class RecordingRetimeConfig:
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
    retime: RecordingRetimeConfig = field(default_factory=RecordingRetimeConfig)
    environment: RecordingEnvironmentConfig = field(
        default_factory=RecordingEnvironmentConfig
    )
    audio: RecordingAudioConfig = field(default_factory=RecordingAudioConfig)
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
