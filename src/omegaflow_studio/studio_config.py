#!/usr/bin/env python3
"""Compose OmegaFlow configuration with Hydra."""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Any, Sequence

from hydra import compose, initialize_config_dir
from hydra.core.config_store import ConfigStore
from hydra.core.hydra_config import HydraConfig
from omegaconf import DictConfig, OmegaConf
from omegaconf.errors import OmegaConfBaseException


@dataclass(frozen=True)
class ProjectLayout:
    root: Path
    data_dir: Path
    config_dir: Path
    recording_script_dir: Path


def discover_project_layout() -> ProjectLayout:
    bundled_config_dir = Path(__file__).resolve().parent / "conf"
    configured = os.environ.get("OMEGAFLOW_STUDIO_PROJECT_ROOT")
    if configured:
        root = Path(configured).expanduser().resolve()
    else:
        cwd = Path.cwd().resolve()
        root = cwd
        for candidate in (cwd, *cwd.parents):
            if (candidate / "recordings" / "config.yaml").exists():
                root = candidate
                break

    config_dir = bundled_config_dir

    data_dir = root / "recordings" / ".omegaflow"

    return ProjectLayout(
        root=root,
        data_dir=data_dir.resolve(),
        config_dir=config_dir.resolve(),
        recording_script_dir=(root / "recordings").resolve(),
    )


def project_root() -> Path:
    return discover_project_layout().root


def relative_project_path(path: Path) -> str:
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


PROJECT_LAYOUT = discover_project_layout()
PROJECT_ROOT = PROJECT_LAYOUT.root
REPO_ROOT = PROJECT_ROOT
PROJECT_DATA_DIR = PROJECT_LAYOUT.data_dir
CONFIG_DIR = PROJECT_LAYOUT.config_dir
STUDIO_CONFIG_NAME = "base-config"
RECORDING_SCRIPT_DIR = PROJECT_LAYOUT.recording_script_dir
GENERATED_DIR = PROJECT_DATA_DIR / "generated"
RECORDING_SOURCE_NAME = "index.md"
RECORDING_ID_COMPONENT_RE = re.compile(r"[a-z0-9][a-z0-9-]*")
MAX_INLINE_RUN_LINES = 10
NARRATION_BEAT_KEYS = {"id", "heading", "narration", "viewer_hold"}
NARRATION_MARKER_RE = re.compile(
    r"@(?:(wait):([A-Za-z][A-Za-z0-9_-]*)(?:\+([0-9]+(?:\.[0-9]+)?)(ms|s))?|([A-Za-z][A-Za-z0-9_-]*))@"
)


class StudioConfigError(RuntimeError):
    pass


def normalize_studio_token(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def project_data_dir_from_value(value: object) -> str:
    data_dir_text = normalize_studio_token(value)
    if not data_dir_text:
        return relative_project_path(PROJECT_DATA_DIR)
    data_dir = Path(data_dir_text).expanduser()
    if data_dir.is_absolute():
        return data_dir.as_posix()
    return data_dir.as_posix()


def recording_script_dir_from_config(config: dict[str, Any] | None) -> Path:
    root = project_root()
    studio = config.get("studio", {}) if config else {}
    recording_dir_value = None
    if isinstance(studio, dict):
        recording_dir_value = studio.get("recording_dir")
    recording_dir_text = normalize_studio_token(recording_dir_value) or "recordings"
    recording_dir = Path(recording_dir_text).expanduser()
    if not recording_dir.is_absolute():
        recording_dir = root / recording_dir
    return recording_dir.resolve()


def studio_data_dir_from_config(config: dict[str, Any] | None) -> Path:
    root = project_root()
    studio = config.get("studio", {}) if config else {}
    data_dir_value = None
    if isinstance(studio, dict):
        data_dir_value = studio.get("data_dir")
    data_dir_text = normalize_studio_token(data_dir_value) or "recordings/.omegaflow"
    data_dir = Path(data_dir_text).expanduser()
    if not data_dir.is_absolute():
        data_dir = root / data_dir
    return data_dir.resolve()


def studio_run_dir(*args: object) -> str:
    if len(args) != 6:
        raise StudioConfigError(
            "studio_run_dir expects data_dir, action, step, dry_run, "
            "recording_id, timestamp"
        )
    data_dir_value, action, step, dry_run, recording_id, timestamp = args
    action_text = normalize_studio_token(action) or "build"
    step_text = normalize_studio_token(step)
    recording_text = normalize_studio_token(recording_id)
    has_recording_id = bool(recording_text)
    if has_recording_id and not is_valid_recording_id(recording_text):
        recording_text = "invalid-recording"
    recording_text = recording_text or "unselected"
    timestamp_text = normalize_studio_token(timestamp)
    dry_run_enabled = str(dry_run).lower() == "true"
    is_recording_run = has_recording_id and not dry_run_enabled and (
        step_text in {"record", "session"}
        or (not step_text and action_text in {"build", "record"})
    )
    data_dir = project_data_dir_from_value(data_dir_value)
    if is_recording_run:
        return f"{data_dir}/runs/{recording_text}/{timestamp_text}"
    job_kind = step_text or action_text
    return f"{data_dir}/runs/.scratch/{job_kind}/{recording_text}/{timestamp_text}"


def register_resolvers() -> None:
    if not OmegaConf.has_resolver("studio_run_dir"):
        OmegaConf.register_resolver("studio_run_dir", studio_run_dir)


class StudioAction(str, Enum):
    bootstrap = "bootstrap"
    build = "build"
    check = "check"
    clean = "clean"
    play = "play"
    watch = "watch"
    inspect = "inspect"
    output = "output"
    runs = "runs"
    list = "list"


class StudioStep(str, Enum):
    record = "record"
    record_check = "record_check"
    record_dry_run = "record_dry_run"
    session = "session"
    dry_run = "dry_run"
    sync_narration = "sync_narration"
    retime = "retime"
    retime_check = "retime_check"
    generate = "generate"
    publish = "publish"
    audio_check = "audio_check"
    audio_dry_run = "audio_dry_run"
    audio_generate = "audio_generate"
    audio_publish = "audio_publish"
    align = "align"
    align_check = "align_check"


@dataclass
class StudioRuntimeConfig:
    recording_dir: str = "recordings"
    data_dir: str = "recordings/.omegaflow"
    keep_output_dir: bool = True


@dataclass
class StudioConfig:
    action: StudioAction = StudioAction.build
    step: StudioStep | None = None
    output_format: str = "text"
    verbose: bool = False
    load_env_file: bool = True
    env_file: str | None = ".env"
    env_override: bool = False
    output: str | None = None
    cast: str | None = None
    timeline: str | None = None
    audio_metadata: str | None = None
    surface: str | None = None
    dry_run: Any = False
    headed: bool = False
    force: bool = False
    timestamps: bool = True
    allow_mismatch: bool = False
    run_id: str | None = None
    runs_since: str | None = None
    runs_limit: int | None = 10
    workspace: str | None = None
    studio: StudioRuntimeConfig = field(default_factory=StudioRuntimeConfig)
    script_params: Any = field(default_factory=dict)
    recording: str | None = None


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
class RecordingPublishSurfaceConfig:
    type: str = ""
    file: str = ""
    placeholder: str | None = None
    component: str | None = None


@dataclass
class RecordingPublishConfig:
    default: str | None = None
    on_build: bool = True
    build_surfaces: list[str] | None = None
    surfaces: dict[str, RecordingPublishSurfaceConfig] = field(default_factory=dict)


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


@dataclass
class StudioDirectiveScene:
    title: str | None = None


@dataclass
class StudioDirectiveCommand(RecordingCommandConfig):
    pass


@dataclass
class StudioDirectiveStep(RecordingStepConfig):
    commands: list[StudioDirectiveCommand] | None = None


@dataclass
class StudioDirectiveGuide(RecordingGuideConfig):
    pass


@dataclass
class StudioDirectiveBeat(RecordingBeatConfig):
    actions: list[StudioDirectiveStep] = field(default_factory=list)
    checks: list[StudioDirectiveStep] = field(default_factory=list)
    guide: StudioDirectiveGuide | None = None


@dataclass
class StudioDirectiveBlock:
    scene: Any = None
    beat: StudioDirectiveBeat | None = None
    beats: list[StudioDirectiveBeat] = field(default_factory=list)


def register_studio_schema() -> None:
    store = ConfigStore.instance()
    store.store(name="studio_schema", node=StudioConfig)
    store.store(name="recording_defaults_schema", node=RecordingDefaults)
    store.store(name="recording_spec_schema", node=RecordingSpec)
    store.store(name="studio_directive_schema", node=StudioDirectiveBlock)


register_resolvers()
register_studio_schema()


def list_recording_ids(recording_dir: Path | None = None) -> list[str]:
    script_dir = recording_dir or RECORDING_SCRIPT_DIR
    if not script_dir.exists():
        return []
    recording_ids: list[str] = []
    for path in script_dir.rglob(RECORDING_SOURCE_NAME):
        if not path.is_file():
            continue
        recording_id = path.parent.relative_to(script_dir).as_posix()
        if is_valid_recording_id(recording_id):
            recording_ids.append(recording_id)
    return sorted(recording_ids)


def is_valid_recording_id(recording_id: object) -> bool:
    if not isinstance(recording_id, str) or not recording_id:
        return False
    if recording_id.startswith("/") or "\\" in recording_id:
        return False
    return all(
        bool(RECORDING_ID_COMPONENT_RE.fullmatch(component))
        for component in recording_id.split("/")
    )


def validate_recording_id(recording_id: str) -> str:
    if not is_valid_recording_id(recording_id):
        raise StudioConfigError(
            "recording id must be a lowercase kebab-case path"
        )
    return recording_id


def normalize_hydra_override(override: str) -> str:
    if override.count("=") <= 1:
        return override
    key, value = override.split("=", 1)
    if value.startswith(("'", '"')):
        return override
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"{key}='{escaped}'"


def compose_studio_config(
    recording_id: str | None,
    overrides: Sequence[str] = (),
) -> dict[str, Any]:
    if not CONFIG_DIR.exists():
        raise StudioConfigError(f"studio config directory not found: {CONFIG_DIR}")

    hydra_overrides = [
        normalize_hydra_override(str(override)) for override in overrides
    ]
    if recording_id is not None:
        hydra_overrides.insert(0, f"recording={recording_id}")
    try:
        with initialize_config_dir(
            version_base=None,
            config_dir=str(CONFIG_DIR),
        ):
            cfg = compose(config_name=STUDIO_CONFIG_NAME, overrides=hydra_overrides)
            data = OmegaConf.to_container(cfg, resolve=True, enum_to_str=True)
    except Exception as exc:
        details = f"recording {recording_id!r}" if recording_id else "default recording"
        raise StudioConfigError(
            f"failed to compose media config for {details}"
        ) from exc
    if not isinstance(data, dict):
        raise StudioConfigError("composed media config must be a mapping")
    return data


def container_from_hydra_cfg(cfg: DictConfig) -> dict[str, Any]:
    data = OmegaConf.to_container(cfg, resolve=True, enum_to_str=True)
    if not isinstance(data, dict):
        raise StudioConfigError("composed Hydra config must be a mapping")
    return data


def resolve_config_path(path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return REPO_ROOT / candidate


def dotenv_entry(line: str, *, path: Path, line_number: int) -> tuple[str, str] | None:
    try:
        tokens = shlex.split(line, comments=True, posix=True)
    except ValueError as exc:
        raise StudioConfigError(
            f"failed to parse env file {path}:{line_number}: {exc}"
        ) from exc
    if not tokens:
        return None
    if tokens[0] == "export":
        tokens = tokens[1:]
    if len(tokens) != 1 or "=" not in tokens[0]:
        raise StudioConfigError(
            f"failed to parse env file {path}:{line_number}: expected KEY=VALUE"
        )
    key, value = tokens[0].split("=", 1)
    if not key.isidentifier():
        raise StudioConfigError(
            f"failed to parse env file {path}:{line_number}: invalid key {key!r}"
        )
    return key, value


def load_env_file(path: Path, *, override: bool = False) -> dict[str, str]:
    if not path.exists():
        return {}
    loaded: dict[str, str] = {}
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        entry = dotenv_entry(line, path=path, line_number=line_number)
        if entry is None:
            continue
        key, value = entry
        if override or key not in os.environ:
            os.environ[key] = value
            loaded[key] = value
    return loaded


def load_configured_env_file(config: dict[str, Any]) -> dict[str, str]:
    enabled = config.get("load_env_file", True)
    if not isinstance(enabled, bool):
        raise StudioConfigError("load_env_file must be a boolean")
    if not enabled:
        return {}

    env_file = config.get("env_file", ".env")
    if env_file is None:
        return {}
    if not isinstance(env_file, str) or not env_file:
        raise StudioConfigError("env_file must be a non-empty string or null")

    override = config.get("env_override", False)
    if not isinstance(override, bool):
        raise StudioConfigError("env_override must be a boolean")

    return load_env_file(resolve_config_path(env_file), override=override)


def merge_mapping(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = merge_mapping(existing, value)
        else:
            merged[key] = value
    return merged


def parse_yaml_mapping(text: str, *, source: str) -> dict[str, Any]:
    try:
        config = OmegaConf.create(text)
        value = OmegaConf.to_container(
            config,
            resolve=False,
            enum_to_str=True,
        )
    except OmegaConfBaseException as exc:
        raise StudioConfigError(f"invalid YAML in {source}: {exc}") from exc
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise StudioConfigError(f"{source} must be a mapping")
    return value


def validate_config_keys(
    data: dict[str, Any],
    *,
    schema: type[Any],
    source: str,
) -> None:
    try:
        OmegaConf.merge(OmegaConf.structured(schema), data)
    except OmegaConfBaseException as exc:
        raise StudioConfigError(f"invalid {source}: {exc}") from exc


def structured_config_mapping(
    data: dict[str, Any],
    *,
    schema: type[Any],
    source: str,
) -> dict[str, Any]:
    try:
        config = OmegaConf.merge(OmegaConf.structured(schema), data)
        value = OmegaConf.to_container(
            config,
            resolve=False,
            enum_to_str=True,
        )
    except OmegaConfBaseException as exc:
        raise StudioConfigError(f"invalid {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise StudioConfigError(f"{source} must be a mapping")
    return value


def recording_defaults_config_path(script_dir: Path) -> Path:
    return script_dir / "config.yaml"


def load_recording_defaults(script_dir: Path) -> dict[str, Any]:
    config_path = recording_defaults_config_path(script_dir)
    if not config_path.exists():
        return {}
    defaults = parse_yaml_mapping(
        config_path.read_text(encoding="utf-8"),
        source=str(config_path),
    )
    identity_keys = sorted({"id", "title"} & set(defaults))
    if identity_keys:
        raise StudioConfigError(
            f"{config_path} cannot define recording identity fields: "
            + ", ".join(identity_keys)
        )
    validate_config_keys(
        defaults,
        schema=RecordingDefaults,
        source=str(config_path),
    )
    return defaults


def split_frontmatter(script_text: str, *, source: Path) -> tuple[dict[str, Any], str]:
    lines = script_text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, script_text
    closing_index: int | None = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            closing_index = index
            break
    if closing_index is None:
        raise StudioConfigError(f"frontmatter in {source} is not closed")
    frontmatter_text = "".join(lines[1:closing_index]).strip()
    body = "".join(lines[closing_index + 1 :])
    if not frontmatter_text:
        return {}, body
    config = parse_yaml_mapping(frontmatter_text, source=f"{source} frontmatter")
    validate_config_keys(
        config,
        schema=RecordingSpec,
        source=f"{source} frontmatter",
    )
    return config, body


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def validate_studio_directive_scene(value: object, *, source: str) -> object:
    if isinstance(value, str) or value is None:
        return value
    if not isinstance(value, dict):
        raise StudioConfigError(f"{source}.scene must be a string or mapping")
    return structured_config_mapping(
        value,
        schema=StudioDirectiveScene,
        source=f"{source}.scene",
    )


def project_schema_values_onto_input(value: object, source: object) -> object:
    if isinstance(value, dict) and isinstance(source, dict):
        return {
            key: project_schema_values_onto_input(value[key], source_value)
            for key, source_value in source.items()
        }
    if isinstance(value, list) and isinstance(source, list):
        return [
            project_schema_values_onto_input(value_item, source_item)
            for value_item, source_item in zip(value, source, strict=False)
        ]
    return value


def validate_studio_directive_block(
    block: dict[str, Any], *, line: int
) -> dict[str, Any]:
    source = f"studio-directive block near line {line}"
    structured = structured_config_mapping(
        block,
        schema=StudioDirectiveBlock,
        source=source,
    )
    validated = project_schema_values_onto_input(structured, block)
    if not isinstance(validated, dict):
        raise StudioConfigError(f"{source} must be a mapping")
    if "scene" in validated:
        validated["scene"] = validate_studio_directive_scene(
            validated["scene"],
            source=source,
        )
    return validated


def studio_directive_blocks(
    script_text: str, *, resolve: bool = True
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    lines = script_text.splitlines()
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped not in {
            "```studio-directive",
            "```studio-directive yaml",
            "```yaml studio-directive",
        }:
            index += 1
            continue
        start_line = index + 1
        index += 1
        block_lines: list[str] = []
        while index < len(lines) and lines[index].strip() != "```":
            block_lines.append(lines[index])
            index += 1
        if index >= len(lines):
            raise StudioConfigError(
                f"studio-directive block starting on line {start_line} is not closed"
            )
        text = "\n".join(block_lines).strip()
        if text:
            try:
                config = OmegaConf.create(text)
                value = OmegaConf.to_container(
                    config,
                    resolve=resolve,
                    enum_to_str=True,
                )
            except OmegaConfBaseException as exc:
                raise StudioConfigError(
                    f"invalid studio-directive config near line {start_line}: {exc}"
                ) from exc
            if not isinstance(value, dict):
                raise StudioConfigError(
                    f"studio-directive block near line {start_line} must be a mapping"
                )
            blocks.append(validate_studio_directive_block(value, line=start_line))
        index += 1
    return blocks


def inline_run_line_count(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip())


def validate_step_inline_run_length(step: dict[str, Any], *, field: str) -> None:
    run = step.get("run")
    if run is None:
        return
    if not isinstance(run, str):
        raise StudioConfigError(f"{field}.run must be a string")
    line_count = inline_run_line_count(run)
    if line_count > MAX_INLINE_RUN_LINES:
        raise StudioConfigError(
            f"{field}.run has {line_count} non-empty lines; "
            f"inline run blocks are limited to {MAX_INLINE_RUN_LINES}. "
            "Move longer shell into an organized run_file."
        )


def validate_step_command_inline_run_lengths(
    step: dict[str, Any], *, field: str
) -> None:
    commands = step.get("commands")
    if commands is None:
        return
    if not isinstance(commands, list):
        raise StudioConfigError(f"{field}.commands must be a list")
    for index, command in enumerate(commands, start=1):
        if not isinstance(command, dict):
            raise StudioConfigError(f"{field}.commands.{index} must be a mapping")
        validate_step_inline_run_length(
            command,
            field=f"{field}.commands.{index}",
        )


def validate_recording_inline_run_lengths(spec: dict[str, Any]) -> None:
    for field in ["setup", "cleanup"]:
        value = spec.get(field)
        if value is None:
            continue
        if not isinstance(value, list):
            raise StudioConfigError(f"recording.{field} must be a list")
        for index, step in enumerate(value, start=1):
            if not isinstance(step, dict):
                raise StudioConfigError(f"recording.{field}.{index} must be a mapping")
            validate_step_inline_run_length(step, field=f"recording.{field}.{index}")
    beats = spec.get("beats")
    if beats is None:
        return
    if not isinstance(beats, list):
        raise StudioConfigError("recording.beats must be a list")
    for beat_index, beat in enumerate(beats, start=1):
        if not isinstance(beat, dict):
            raise StudioConfigError(f"recording.beats.{beat_index} must be a mapping")
        beat_id = beat.get("id", beat_index)
        for field in ["actions", "checks"]:
            value = beat.get(field)
            if value is None:
                continue
            if not isinstance(value, list):
                raise StudioConfigError(
                    f"recording.beats.{beat_id}.{field} must be a list"
                )
            for index, step in enumerate(value, start=1):
                if not isinstance(step, dict):
                    raise StudioConfigError(
                        f"recording.beats.{beat_id}.{field}.{index} must be a mapping"
                    )
                validate_step_inline_run_length(
                    step,
                    field=f"recording.beats.{beat_id}.{field}.{index}",
                )
                validate_step_command_inline_run_lengths(
                    step,
                    field=f"recording.beats.{beat_id}.{field}.{index}",
                )


def validate_recording_audio_timing_requirements(spec: dict[str, Any]) -> None:
    audio_config = spec.get("audio")
    audio_enabled = (
        isinstance(audio_config, dict) and bool(audio_config.get("enabled", False))
    )
    if audio_enabled:
        return

    reasons: list[str] = []
    narration = spec.get("narration")
    narration_beats = narration.get("beats") if isinstance(narration, dict) else []
    if isinstance(narration_beats, list):
        for beat in narration_beats:
            if not isinstance(beat, dict):
                continue
            waits = beat.get("waits")
            if isinstance(waits, list) and waits:
                beat_id = beat.get("id", "<unknown>")
                reasons.append(f"narration wait markers in beat {beat_id!r}")

    beats = spec.get("beats")
    if isinstance(beats, list):
        for beat in beats:
            if not isinstance(beat, dict):
                continue
            beat_id = beat.get("id", "<unknown>")
            actions = beat.get("actions")
            if not isinstance(actions, list):
                continue
            for action_index, action in enumerate(actions, start=1):
                if not isinstance(action, dict):
                    continue
                after = action.get("after")
                if isinstance(after, str) and after:
                    reasons.append(
                        f"action {action_index} after anchor {after!r} "
                        f"in beat {beat_id!r}"
                    )
                commands = action.get("commands")
                if not isinstance(commands, list):
                    continue
                for command_index, command in enumerate(commands, start=1):
                    if not isinstance(command, dict):
                        continue
                    after = command.get("after")
                    if isinstance(after, str) and after:
                        command_label = command.get("id") or f"#{command_index}"
                        reasons.append(
                            f"command {command_label!r} after anchor {after!r} "
                            f"in beat {beat_id!r}"
                        )

    if reasons:
        details = "; ".join(reasons)
        raise StudioConfigError(
            "audio timing markers require audio.enabled: true; found " + details
        )


def normalize_narration_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def narration_marker_gap_seconds(value: str | None, unit: str | None) -> float:
    if value is None:
        return 0.0
    gap = float(value)
    return gap / 1000.0 if unit == "ms" else gap


def narration_text_and_anchors(
    text: str,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    parts: list[str] = []
    anchors: list[dict[str, Any]] = []
    waits: list[dict[str, Any]] = []
    seen: set[str] = set()
    previous = 0
    for match in NARRATION_MARKER_RE.finditer(text):
        before = text[match.start() - 1] if match.start() > 0 else ""
        after = text[match.end()] if match.end() < len(text) else ""
        if before.isalnum() or before == "_" or after.isalnum() or after == "_":
            raise StudioConfigError(
                f"narration marker {match.group(0)} must be separated from words"
            )
        parts.append(text[previous : match.start()])
        marker = match.group(0)
        marker_offset = len(normalize_narration_text("".join(parts)))
        marker_kind = match.group(1)
        if marker_kind == "wait":
            wait_target = match.group(2)
            gap_seconds = narration_marker_gap_seconds(match.group(3), match.group(4))
            waits.append(
                {
                    "target": wait_target,
                    "marker": marker,
                    "text_offset": marker_offset,
                    "gap_seconds": gap_seconds,
                }
            )
            previous = match.end()
            continue
        anchor_id = match.group(5)
        if anchor_id in seen:
            raise StudioConfigError(f"duplicate narration anchor: @{anchor_id}@")
        seen.add(anchor_id)
        anchors.append(
            {
                "id": anchor_id,
                "marker": marker,
                "text_offset": marker_offset,
            }
        )
        previous = match.end()
    parts.append(text[previous:])
    return normalize_narration_text("".join(parts)), anchors, waits


def scene_title_from_directive(value: object) -> str:
    if isinstance(value, str):
        title = value.strip()
    elif isinstance(value, dict):
        title = str(value.get("title") or "").strip()
    else:
        title = ""
    if not title:
        raise StudioConfigError("studio-directive scene must define a non-empty title")
    return title


def beat_values_from_directive(block: dict[str, Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    if block.get("beat") is not None:
        values.append(block["beat"])
    values.extend(block.get("beats") or [])
    return values


def narration_from_script(
    *, recording_id: str, script_path: Path, blocks: list[dict[str, Any]]
) -> dict[str, Any]:
    scene_title = ""
    beats: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for block in blocks:
        if "scene" in block:
            if scene_title:
                raise StudioConfigError("duplicate studio-directive scene")
            scene_title = scene_title_from_directive(block["scene"])
        for value in beat_values_from_directive(block):
            beat_id = value["id"]
            heading = value["heading"]
            narration = value["narration"]
            if not beat_id.strip():
                raise StudioConfigError(
                    "studio-directive beat.id must be a non-empty string"
                )
            if not heading.strip():
                raise StudioConfigError(
                    "studio-directive beat.heading must be a non-empty string"
                )
            if not narration.strip():
                raise StudioConfigError(
                    "studio-directive beat.narration must be a non-empty string"
                )
            normalized_id = beat_id.strip()
            if normalized_id in seen_ids:
                raise StudioConfigError(f"duplicate narration beat id: {normalized_id}")
            seen_ids.add(normalized_id)
            text, anchors, waits = narration_text_and_anchors(narration)
            beat = {
                "id": normalized_id,
                "heading": heading.strip(),
                "text": text,
            }
            if anchors:
                beat["anchors"] = anchors
            if waits:
                beat["waits"] = waits
            viewer_hold = value.get("viewer_hold")
            if viewer_hold is not None:
                if viewer_hold < 0:
                    raise StudioConfigError(
                        f"studio-directive beat {normalized_id}.viewer_hold "
                        "must be a non-negative number"
                    )
                beat["viewer_hold"] = float(viewer_hold)
            beats.append(beat)
    if not scene_title:
        raise StudioConfigError(f"recording script must define a scene: {script_path}")
    if not beats:
        raise StudioConfigError(
            f"recording script must define narrated beats: {script_path}"
        )
    return {
        "source_script": display_path(script_path),
        "source_sha256": sha256(script_path.read_bytes()).hexdigest(),
        "generated": False,
        "scene": {"id": recording_id, "title": scene_title},
        "beats": beats,
    }


def recording_beat_values_from_script(
    blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    recording_beats: list[dict[str, Any]] = []
    for block in blocks:
        for value in beat_values_from_directive(block):
            beat_id = value.get("id")
            if not beat_id.strip():
                continue
            executable_keys = set(value) - NARRATION_BEAT_KEYS
            if not executable_keys:
                continue
            beat = {
                key: data
                for key, data in value.items()
                if key not in {"heading", "narration"}
            }
            beat["id"] = beat_id.strip()
            recording_beats.append(beat)
    return recording_beats


def merge_script_recording_beats(
    spec: dict[str, Any], blocks: list[dict[str, Any]]
) -> None:
    inline_beats = recording_beat_values_from_script(blocks)
    if not inline_beats:
        return
    existing = spec.get("beats")
    if existing is None:
        spec["beats"] = inline_beats
        return
    if not isinstance(existing, list):
        raise StudioConfigError("recording.beats must be a list")
    seen_ids = {
        beat.get("id")
        for beat in existing
        if isinstance(beat, dict) and isinstance(beat.get("id"), str)
    }
    duplicate_ids = sorted(
        beat["id"] for beat in inline_beats if beat["id"] in seen_ids
    )
    if duplicate_ids:
        raise StudioConfigError(
            "recording beat defined in both recording.beats and beat directive: "
            + ", ".join(duplicate_ids)
        )
    spec["beats"] = [*existing, *inline_beats]


def recording_id_from_config(config: dict[str, Any], recording_id: str | None) -> str:
    if recording_id:
        return validate_recording_id(recording_id)
    value = config.get("recording")
    if isinstance(value, str) and value:
        return validate_recording_id(value)
    raise StudioConfigError("recording id must be a non-empty string")


def resolve_recording_spec_interpolations(spec: dict[str, Any]) -> dict[str, Any]:
    try:
        config = OmegaConf.create(spec)
        resolved = OmegaConf.to_container(config, resolve=True, enum_to_str=True)
    except OmegaConfBaseException as exc:
        raise StudioConfigError(
            f"failed to resolve recording script config: {exc}"
        ) from exc
    if not isinstance(resolved, dict):
        raise StudioConfigError("resolved recording script config must be a mapping")
    return resolved


def recording_script_path(
    recording_id: str,
    recording_dir: Path | None = None,
) -> Path:
    script_dir = recording_dir or RECORDING_SCRIPT_DIR
    return script_dir / validate_recording_id(recording_id) / RECORDING_SOURCE_NAME


def recording_from_script(
    recording_id: str,
    recording_dir: Path | None = None,
) -> dict[str, Any]:
    workspace_dir = recording_dir or RECORDING_SCRIPT_DIR
    script_path = recording_script_path(recording_id, recording_dir=workspace_dir)
    if not script_path.exists():
        raise StudioConfigError(f"recording script not found: {script_path}")
    script_text = script_path.read_text(encoding="utf-8")
    frontmatter, script_body = split_frontmatter(script_text, source=script_path)
    blocks = studio_directive_blocks(script_body, resolve=False)
    if any("recording" in block for block in blocks):
        raise StudioConfigError(
            f"recording directives are no longer supported; use frontmatter: "
            f"{script_path}"
        )
    if not frontmatter:
        raise StudioConfigError(
            f"recording script must contain frontmatter config: {script_path}"
        )
    defaults = load_recording_defaults(workspace_dir)
    spec = merge_mapping(defaults, frontmatter)
    spec.setdefault("id", recording_id)
    spec["script"] = display_path(script_path)
    spec = structured_config_mapping(
        spec,
        schema=RecordingSpec,
        source=str(script_path),
    )
    spec["_script_dir"] = display_path(script_path.parent)
    merge_script_recording_beats(spec, blocks)
    spec["narration"] = narration_from_script(
        recording_id=recording_id,
        script_path=script_path,
        blocks=blocks,
    )
    spec = resolve_recording_spec_interpolations(spec)
    validate_recording_inline_run_lengths(spec)
    validate_recording_audio_timing_requirements(spec)
    return spec


def script_parameter_defaults(spec: dict[str, Any]) -> dict[str, Any]:
    parameters = spec.get("parameters", {})
    if parameters is None:
        return {}
    if not isinstance(parameters, dict):
        raise StudioConfigError("recording.parameters must be a mapping")
    defaults: dict[str, Any] = {}
    for key, value in parameters.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise StudioConfigError(
                "recording.parameters keys must be shell-safe names"
            )
        if isinstance(value, dict) and "default" in value:
            defaults[key] = value["default"]
        else:
            defaults[key] = value
    return defaults


def resolved_script_parameters(
    spec: dict[str, Any],
    overrides: object,
) -> dict[str, Any]:
    defaults = script_parameter_defaults(spec)
    if overrides is None:
        return defaults
    if not isinstance(overrides, dict):
        raise StudioConfigError("script_params must be a mapping")
    unknown = sorted(set(overrides) - set(defaults))
    if unknown:
        raise StudioConfigError("unknown script parameter(s): " + ", ".join(unknown))
    return merge_mapping(defaults, overrides)


def recording_spec_from_config(
    config: dict[str, Any],
    *,
    recording_id: str | None,
    overrides: Sequence[str],
    hydra_output_dir: str | None = None,
) -> dict[str, Any]:
    resolved_recording_id = recording_id_from_config(config, recording_id)
    recording_dir = recording_script_dir_from_config(config)
    spec = recording_from_script(resolved_recording_id, recording_dir=recording_dir)

    validate_config_keys(
        {key: value for key, value in spec.items() if not key.startswith("_")},
        schema=RecordingSpec,
        source=f"recording {resolved_recording_id}",
    )
    validate_recording_inline_run_lengths(spec)
    validate_recording_audio_timing_requirements(spec)

    spec["parameters"] = resolved_script_parameters(
        spec,
        config.get("script_params", {}),
    )

    resolved_recording_id = spec.get("id")
    if not isinstance(resolved_recording_id, str) or not resolved_recording_id:
        raise StudioConfigError("recording.id must be a non-empty string")

    script = spec.get("script")
    manifest_path = (
        resolve_config_path(script)
        if isinstance(script, str) and script
        else recording_script_path(resolved_recording_id, recording_dir=recording_dir)
    )
    spec["_manifest_path"] = str(manifest_path)
    spec["_config_dir"] = str(CONFIG_DIR)
    spec["_recording_dir"] = str(recording_dir)
    spec["_recording_id"] = resolved_recording_id
    spec["_overrides"] = list(overrides)
    spec["_studio_config"] = config
    if hydra_output_dir is not None:
        studio = config.get("studio", {})
        keep_output_dir = False
        if isinstance(studio, dict):
            keep_output_dir = bool(studio.get("keep_output_dir", False))
        spec["_hydra_output_dir"] = hydra_output_dir
        spec["_keep_hydra_output_dir"] = keep_output_dir
    return spec


def load_recording_spec(
    recording_id: str | None,
    overrides: Sequence[str] = (),
) -> dict[str, Any]:
    config = compose_studio_config(recording_id, overrides)
    return recording_spec_from_config(
        config,
        recording_id=recording_id,
        overrides=overrides,
    )


def load_recording_spec_from_hydra_cfg(cfg: DictConfig) -> dict[str, Any]:
    hydra_cfg = HydraConfig.get()
    return recording_spec_from_config(
        container_from_hydra_cfg(cfg),
        recording_id=None,
        overrides=list(hydra_cfg.overrides.task),
        hydra_output_dir=str(hydra_cfg.runtime.output_dir),
    )
