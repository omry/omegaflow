"""Serialization and validation for versioned presentation artifacts."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import fields
from pathlib import Path, PurePosixPath
from typing import Any, TypeVar
from urllib.parse import urlsplit

from omegaconf import OmegaConf
from omegaconf.errors import OmegaConfBaseException

from .presentation_schema import (
    BROWSER_EVENT_SCHEMAS_V1,
    BrowserBoundsV1,
    BrowserClipEventV1,
    BrowserEventV1,
    BrowserPayloadV1,
    BrowserPointV1,
    BrowserScrollEventV1,
    BrowserStateEventV1,
    BrowserTextStyleV1,
    PresentationAssetV1,
    PresentationAudioIntervalV1,
    PresentationAudioV1,
    PresentationBeatPlayerV1,
    PresentationBeatV1,
    PresentationBrowserHeaderV1,
    PresentationChromeV1,
    PresentationGuideV1,
    PresentationHeaderV1,
    PresentationManifestV1,
    PresentationPaneBeatV1,
    PresentationPaneInitial,
    PresentationPaneLayoutV1,
    PresentationPaneTrackV1,
    PresentationPaneTransitionKind,
    PresentationPaneTransitionV1,
    PresentationPaneV1,
    PresentationFileSignatureV1,
    PresentationSignaturesV1,
    PlayerToolbarControl,
    PresentationPlayerToolbarHighlightV1,
    PresentationWindowV1,
    VisualizationPayloadV1,
    VisualizationTokenKind,
    VisualizationTokenV1,
)


class PresentationValidationError(ValueError):
    """Raised when a generated presentation artifact violates its contract."""


T = TypeVar("T")

EVENT_KIND_PRIORITY = (
    "state",
    "clip",
    "scroll",
    "focus",
    "text",
    "key",
    "pointer_visibility",
    "pointer_move",
    "click",
    "display_url",
    "complete",
)
EVENT_KIND_INDEX = {kind: index for index, kind in enumerate(EVENT_KIND_PRIORITY)}
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
PRESENTATION_ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*\Z")
PRESENTATION_PANE_LIMIT = 64
PRESENTATION_ITEM_LIMIT = 100_000
VISUALIZATION_LANGUAGE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+.-]{0,31}\Z")
VISUALIZATION_TEXT_LIMIT = 100_000
VISUALIZATION_TOKEN_LIMIT = 10_000
ASSET_MEDIA_TYPES = {
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".mp4": "video/mp4",
    ".png": "image/png",
    ".webp": "image/webp",
}


def _mapping(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PresentationValidationError(f"{field} must be a mapping")
    return value


def _typed(value: dict[str, Any], schema: type[T], *, field: str) -> T:
    try:
        config = OmegaConf.merge(OmegaConf.structured(schema), value)
        result = OmegaConf.to_object(config)
    except (OmegaConfBaseException, TypeError, ValueError) as exc:
        raise PresentationValidationError(f"invalid {field}: {exc}") from exc
    if not isinstance(result, schema):
        raise PresentationValidationError(f"invalid {field}: expected {schema.__name__}")
    return result


def _allowed_fields(schema: type[object]) -> set[str]:
    return {item.name for item in fields(schema)}


def _reject_unknown(
    value: dict[str, Any], schema: type[object], *, field: str
) -> None:
    unknown = sorted(set(value) - _allowed_fields(schema))
    if unknown:
        raise PresentationValidationError(
            f"{field} has unknown fields: {', '.join(unknown)}"
        )


def _require_fields(
    value: dict[str, Any], names: set[str], *, field: str
) -> None:
    missing = sorted(names - set(value))
    if missing:
        raise PresentationValidationError(
            f"{field} is missing fields: {', '.join(missing)}"
        )


def _integer(
    value: object,
    *,
    field: str,
    minimum: int = 0,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PresentationValidationError(f"{field} must be an integer")
    if value < minimum:
        raise PresentationValidationError(f"{field} must be at least {minimum}")
    return value


def _number(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PresentationValidationError(f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise PresentationValidationError(f"{field} must be finite")
    return result


def _non_empty_string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise PresentationValidationError(f"{field} must be a non-empty string")
    return value


def _presentation_id(value: object, *, field: str) -> str:
    result = _non_empty_string(value, field=field)
    if PRESENTATION_ID_RE.fullmatch(result) is None:
        raise PresentationValidationError(f"{field} must be identifier-like")
    return result


def _structured_payload(value: object, *, field: str) -> dict[str, Any]:
    try:
        payload = OmegaConf.to_container(
            OmegaConf.structured(value), resolve=True, enum_to_str=True
        )
    except (OmegaConfBaseException, TypeError, ValueError) as exc:
        raise PresentationValidationError(f"invalid {field}: {exc}") from exc
    if not isinstance(payload, dict):
        raise PresentationValidationError(f"{field} must serialize to a mapping")
    return payload


def _point(value: object, *, field: str) -> BrowserPointV1:
    mapping = _mapping(value, field=field)
    _reject_unknown(mapping, BrowserPointV1, field=field)
    _require_fields(mapping, {"x", "y"}, field=field)
    _number(mapping["x"], field=f"{field}.x")
    _number(mapping["y"], field=f"{field}.y")
    return _typed(mapping, BrowserPointV1, field=field)


def _bounds(value: object, *, field: str) -> BrowserBoundsV1:
    mapping = _mapping(value, field=field)
    _reject_unknown(mapping, BrowserBoundsV1, field=field)
    _require_fields(mapping, {"x", "y", "width", "height"}, field=field)
    for name in ("x", "y", "width", "height"):
        number = _number(mapping[name], field=f"{field}.{name}")
        if name in {"width", "height"} and number < 0:
            raise PresentationValidationError(f"{field}.{name} must be non-negative")
    return _typed(mapping, BrowserBoundsV1, field=field)


def _display_url(value: object, *, field: str) -> str:
    text = _non_empty_string(value, field=field)
    if text == "about:blank":
        return text
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise PresentationValidationError(
            f"{field} must be absolute HTTP(S) or about:blank"
        )
    if parsed.username is not None or parsed.password is not None:
        raise PresentationValidationError(f"{field} must not contain user information")
    return text


def _event_required_fields(kind: str) -> set[str]:
    schema = BROWSER_EVENT_SCHEMAS_V1[kind]
    return _allowed_fields(schema)


def validate_browser_event(
    value: object,
    *,
    index: int,
    duration_ms: int,
) -> BrowserEventV1:
    field = f"browser.events.{index}"
    mapping = _mapping(value, field=field)
    kind = mapping.get("kind")
    if not isinstance(kind, str) or kind not in BROWSER_EVENT_SCHEMAS_V1:
        raise PresentationValidationError(f"{field}.kind is unsupported")
    schema = BROWSER_EVENT_SCHEMAS_V1[kind]
    _reject_unknown(mapping, schema, field=field)
    _require_fields(mapping, _event_required_fields(kind), field=field)
    action_id = _non_empty_string(mapping.get("action_id"), field=f"{field}.action_id")
    at_ms = _integer(mapping.get("at_ms"), field=f"{field}.at_ms")
    end_ms = _integer(mapping.get("end_ms"), field=f"{field}.end_ms")
    if end_ms < at_ms or end_ms > duration_ms:
        raise PresentationValidationError(
            f"{field} interval must be ordered within the browser beat"
        )

    if kind == "state":
        _non_empty_string(mapping["asset"], field=f"{field}.asset")
        if mapping["transition"] not in {"cut", "fade"}:
            raise PresentationValidationError(f"{field}.transition is invalid")
    elif kind == "pointer_move":
        _point(mapping["start"], field=f"{field}.start")
        _point(mapping["end"], field=f"{field}.end")
        curve = _mapping(mapping["curve"], field=f"{field}.curve")
        if set(curve) != {"x1", "y1", "x2", "y2"}:
            raise PresentationValidationError(
                f"{field}.curve must contain x1, y1, x2, and y2"
            )
        for name, coordinate in curve.items():
            _number(coordinate, field=f"{field}.curve.{name}")
    elif kind == "pointer_visibility":
        if not isinstance(mapping["visible"], bool):
            raise PresentationValidationError(f"{field}.visible must be boolean")
    elif kind == "click":
        _point(mapping["point"], field=f"{field}.point")
        if mapping["button"] not in {"left", "middle", "right"}:
            raise PresentationValidationError(f"{field}.button is invalid")
    elif kind == "focus":
        _bounds(mapping["target"], field=f"{field}.target")
    elif kind == "text":
        _bounds(mapping["target"], field=f"{field}.target")
        _non_empty_string(mapping["mode"], field=f"{field}.mode")
        if mapping["mode"] not in {"literal", "masked", "placeholder"}:
            raise PresentationValidationError(f"{field}.mode is invalid")
        for name in ("initial", "final"):
            if not isinstance(mapping[name], str):
                raise PresentationValidationError(f"{field}.{name} must be a string")
        style = _mapping(mapping["style"], field=f"{field}.style")
        _reject_unknown(style, BrowserTextStyleV1, field=f"{field}.style")
        _require_fields(
            style,
            _allowed_fields(BrowserTextStyleV1),
            field=f"{field}.style",
        )
        for name in (
            "font_family",
            "font_weight",
            "font_style",
            "color",
            "text_align",
        ):
            _non_empty_string(style[name], field=f"{field}.style.{name}")
        for name in (
            "font_size",
            "line_height",
            "letter_spacing",
            "padding_top",
            "padding_right",
            "padding_bottom",
            "padding_left",
        ):
            _number(style[name], field=f"{field}.style.{name}")
        _bounds(style["clipping_rect"], field=f"{field}.style.clipping_rect")
        for name in ("selection_start", "selection_end"):
            if style[name] is not None:
                _integer(style[name], field=f"{field}.style.{name}")
        if (
            style["selection_start"] is not None
            and style["selection_end"] is not None
            and style["selection_end"] < style["selection_start"]
        ):
            raise PresentationValidationError(f"{field}.style selection is invalid")
        if not isinstance(style["caret_visible"], bool):
            raise PresentationValidationError(
                f"{field}.style.caret_visible must be boolean"
            )
    elif kind == "key":
        _non_empty_string(mapping["key"], field=f"{field}.key")
        _non_empty_string(mapping["label"], field=f"{field}.label")
    elif kind == "scroll":
        _bounds(mapping["container"], field=f"{field}.container")
        _point(mapping["start"], field=f"{field}.start")
        _point(mapping["end"], field=f"{field}.end")
        _non_empty_string(mapping["start_asset"], field=f"{field}.start_asset")
        _non_empty_string(mapping["end_asset"], field=f"{field}.end_asset")
    elif kind == "clip":
        _non_empty_string(mapping["asset"], field=f"{field}.asset")
        trim_start = _integer(
            mapping["trim_start_ms"], field=f"{field}.trim_start_ms"
        )
        trim_end = _integer(mapping["trim_end_ms"], field=f"{field}.trim_end_ms")
        if trim_end < trim_start:
            raise PresentationValidationError(f"{field} trim interval is invalid")
    elif kind == "display_url":
        _display_url(mapping["value"], field=f"{field}.value")

    result = _typed(mapping, schema, field=field)
    if result.action_id != action_id:
        raise PresentationValidationError(f"{field}.action_id changed during parsing")
    return result


def _event_sort_key(
    event: dict[str, Any],
    *,
    action_order: dict[str, int],
) -> tuple[int, int, int]:
    action_id = event.get("action_id")
    if not isinstance(action_id, str) or action_id not in action_order:
        raise PresentationValidationError(
            f"browser event references unknown action {action_id!r}"
        )
    kind = event.get("kind")
    if not isinstance(kind, str) or kind not in EVENT_KIND_INDEX:
        raise PresentationValidationError(f"unsupported browser event kind {kind!r}")
    at_ms = _integer(event.get("at_ms"), field="browser event at_ms")
    return at_ms, action_order[action_id], EVENT_KIND_INDEX[kind]


def sort_browser_events(
    events: list[dict[str, Any]],
    *,
    action_ids: list[str],
) -> list[dict[str, Any]]:
    """Return events in the fixed deterministic serialization order."""

    if len(set(action_ids)) != len(action_ids):
        raise PresentationValidationError("browser action order contains duplicates")
    action_order = {action_id: index for index, action_id in enumerate(action_ids)}
    return sorted(events, key=lambda event: _event_sort_key(event, action_order=action_order))


def validate_browser_payload(
    value: object,
    *,
    action_ids: list[str] | None = None,
) -> BrowserPayloadV1:
    mapping = _mapping(value, field="browser payload")
    _reject_unknown(mapping, BrowserPayloadV1, field="browser payload")
    _require_fields(mapping, _allowed_fields(BrowserPayloadV1), field="browser payload")
    if mapping.get("payload_version") != 1:
        raise PresentationValidationError("browser payload_version must be 1")
    _non_empty_string(mapping.get("beat_id"), field="browser payload beat_id")
    duration_ms = _integer(
        mapping.get("duration_ms"), field="browser payload duration_ms"
    )
    viewport = _mapping(mapping.get("viewport"), field="browser payload viewport")
    if set(viewport) != {"width", "height", "device_scale_factor"}:
        raise PresentationValidationError("browser viewport fields are invalid")
    _integer(viewport["width"], field="browser viewport width", minimum=1)
    _integer(viewport["height"], field="browser viewport height", minimum=1)
    scale = _number(
        viewport["device_scale_factor"], field="browser viewport device_scale_factor"
    )
    if scale <= 0:
        raise PresentationValidationError("browser device_scale_factor must be positive")
    _non_empty_string(mapping.get("initial_state"), field="browser initial_state")
    pointer = _mapping(mapping.get("initial_pointer"), field="browser initial_pointer")
    if set(pointer) != {"x", "y", "visible"}:
        raise PresentationValidationError("browser initial_pointer fields are invalid")
    _number(pointer["x"], field="browser initial_pointer.x")
    _number(pointer["y"], field="browser initial_pointer.y")
    if not isinstance(pointer["visible"], bool):
        raise PresentationValidationError("browser initial_pointer.visible must be boolean")
    display_url = mapping.get("initial_display_url")
    if display_url is not None:
        _display_url(display_url, field="browser initial_display_url")
    policies = _mapping(
        mapping.get("animation_policies"), field="browser animation_policies"
    )
    if policies != {"pointer": "pointer-v1", "typing": "natural-v1"}:
        raise PresentationValidationError("browser animation policies are unsupported")
    raw_events = mapping.get("events")
    if not isinstance(raw_events, list):
        raise PresentationValidationError("browser events must be a list")
    if len(raw_events) > PRESENTATION_ITEM_LIMIT:
        raise PresentationValidationError(
            f"browser events exceeds {PRESENTATION_ITEM_LIMIT} entries"
        )
    for index, event in enumerate(raw_events):
        validate_browser_event(event, index=index, duration_ms=duration_ms)
    if action_ids is not None:
        sorted_events = sort_browser_events(raw_events, action_ids=action_ids)
        if raw_events != sorted_events:
            raise PresentationValidationError("browser events are not deterministically sorted")
    return _typed(mapping, BrowserPayloadV1, field="browser payload")


def serialize_browser_payload(
    payload: BrowserPayloadV1,
    *,
    action_ids: list[str],
) -> dict[str, Any]:
    result = _structured_payload(payload, field="browser payload")
    events = result.get("events")
    if not isinstance(events, list) or not all(isinstance(item, dict) for item in events):
        raise PresentationValidationError("browser events must serialize as mappings")
    result["events"] = sort_browser_events(events, action_ids=action_ids)
    validate_browser_payload(result, action_ids=action_ids)
    return result


def validate_visualization_payload(value: object) -> VisualizationPayloadV1:
    field = "visualization payload"
    mapping = _mapping(value, field=field)
    _reject_unknown(mapping, VisualizationPayloadV1, field=field)
    _require_fields(mapping, _allowed_fields(VisualizationPayloadV1), field=field)
    if mapping.get("payload_version") != 1:
        raise PresentationValidationError(f"{field} payload_version must be 1")
    _presentation_id(mapping.get("beat_id"), field=f"{field} beat_id")
    _integer(mapping.get("duration_ms"), field=f"{field} duration_ms")
    language = _non_empty_string(mapping.get("language"), field=f"{field} language")
    if VISUALIZATION_LANGUAGE_RE.fullmatch(language) is None:
        raise PresentationValidationError(f"{field} language is invalid")
    text = _non_empty_string(mapping.get("text"), field=f"{field} text")
    if len(text) > VISUALIZATION_TEXT_LIMIT:
        raise PresentationValidationError(
            f"{field} text exceeds {VISUALIZATION_TEXT_LIMIT} characters"
        )
    tokens = mapping.get("tokens")
    if not isinstance(tokens, list):
        raise PresentationValidationError(f"{field} tokens must be a list")
    if len(tokens) > VISUALIZATION_TOKEN_LIMIT:
        raise PresentationValidationError(
            f"{field} tokens exceed {VISUALIZATION_TOKEN_LIMIT} entries"
        )
    previous_end = 0
    for index, value in enumerate(tokens):
        token_field = f"{field} tokens.{index}"
        token = _mapping(value, field=token_field)
        _reject_unknown(token, VisualizationTokenV1, field=token_field)
        _require_fields(token, _allowed_fields(VisualizationTokenV1), field=token_field)
        start = _integer(token.get("start"), field=f"{token_field}.start")
        end = _integer(token.get("end"), field=f"{token_field}.end")
        if start < previous_end or end <= start or end > len(text):
            raise PresentationValidationError(
                f"{token_field} range must be ordered, non-overlapping, and within text"
            )
        try:
            VisualizationTokenKind(token.get("kind"))
        except (TypeError, ValueError) as exc:
            raise PresentationValidationError(
                f"{token_field}.kind is unsupported"
            ) from exc
        previous_end = end
    return _typed(mapping, VisualizationPayloadV1, field=field)


def serialize_visualization_payload(
    payload: VisualizationPayloadV1,
) -> dict[str, Any]:
    result = _structured_payload(payload, field="visualization payload")
    validate_visualization_payload(result)
    return result


def validate_relative_presentation_path(value: object, *, field: str) -> str:
    path = _non_empty_string(value, field=field)
    if "\\" in path:
        raise PresentationValidationError(f"{field} must use POSIX separators")
    parsed = PurePosixPath(path)
    if (
        parsed.is_absolute()
        or parsed.as_posix() != path
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise PresentationValidationError(
            f"{field} must be a normalized relative path beneath the manifest"
        )
    if urlsplit(path).scheme:
        raise PresentationValidationError(f"{field} must not be a URL")
    return path


def _resolved_manifest_file(root: Path, relative: str, *, field: str) -> Path:
    root = root.resolve()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise PresentationValidationError(f"{field} escapes the manifest directory")
    if not candidate.is_file():
        raise PresentationValidationError(f"{field} does not exist: {relative}")
    return candidate


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_presentation_signatures(root: Path, *, relative: str = "signatures.json") -> Path:
    """Write the canonical integrity index for every other file in a bundle."""
    target = root / validate_relative_presentation_path(
        relative, field="presentation signatures"
    )
    files = {
        path.relative_to(root).as_posix(): {
            "sha256": _file_sha256(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and path != target
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"version": 1, "files": files}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


def _validate_asset(
    asset_id: str,
    value: object,
    *,
    root: Path | None,
    signatures: dict[str, PresentationFileSignatureV1] | None,
) -> tuple[PresentationAssetV1, str]:
    field = f"manifest assets.{asset_id}"
    _non_empty_string(asset_id, field="manifest asset id")
    mapping = _mapping(value, field=field)
    _reject_unknown(mapping, PresentationAssetV1, field=field)
    _require_fields(mapping, _allowed_fields(PresentationAssetV1), field=field)
    path = validate_relative_presentation_path(mapping.get("path"), field=f"{field}.path")
    media_type = _non_empty_string(mapping.get("media_type"), field=f"{field}.media_type")
    expected_media_type = ASSET_MEDIA_TYPES.get(PurePosixPath(path).suffix.lower())
    if expected_media_type is None or expected_media_type != media_type:
        raise PresentationValidationError(
            f"{field}.media_type does not match a supported asset extension"
        )
    if root is not None:
        _resolved_manifest_file(root, path, field=f"{field}.path")
        if signatures is None or path not in signatures:
            raise PresentationValidationError(f"{field}.path is not signed")
    return _typed(mapping, PresentationAssetV1, field=field), path


def _load_presentation_signatures(
    root: Path,
    relative: object,
) -> dict[str, PresentationFileSignatureV1]:
    field = "manifest signatures"
    signature_path = validate_relative_presentation_path(relative, field=field)
    signature_file = _resolved_manifest_file(root, signature_path, field=field)
    mapping = _load_json(signature_file, field=field)
    _reject_unknown(mapping, PresentationSignaturesV1, field=field)
    _require_fields(mapping, _allowed_fields(PresentationSignaturesV1), field=field)
    if mapping.get("version") != 1:
        raise PresentationValidationError(f"{field}.version must be 1")
    values = _mapping(mapping.get("files"), field=f"{field}.files")
    signatures: dict[str, PresentationFileSignatureV1] = {}
    for relative_path, value in values.items():
        path_field = f"{field}.files.{relative_path}"
        path = validate_relative_presentation_path(relative_path, field=path_field)
        if path == signature_path:
            raise PresentationValidationError(f"{path_field} cannot sign itself")
        signature = _mapping(value, field=path_field)
        _reject_unknown(signature, PresentationFileSignatureV1, field=path_field)
        _require_fields(
            signature,
            _allowed_fields(PresentationFileSignatureV1),
            field=path_field,
        )
        digest = _non_empty_string(signature.get("sha256"), field=f"{path_field}.sha256")
        if SHA256_RE.fullmatch(digest) is None:
            raise PresentationValidationError(
                f"{path_field}.sha256 must be a full lowercase digest"
            )
        byte_count = _integer(signature.get("bytes"), field=f"{path_field}.bytes")
        file_path = _resolved_manifest_file(root, path, field=path_field)
        if file_path.stat().st_size != byte_count:
            raise PresentationValidationError(f"{path_field}.bytes does not match")
        if _file_sha256(file_path) != digest:
            raise PresentationValidationError(f"{path_field}.sha256 does not match")
        signatures[path] = _typed(
            signature, PresentationFileSignatureV1, field=path_field
        )
    return signatures


def _browser_asset_references(payload: BrowserPayloadV1) -> set[str]:
    references = {payload.initial_state}
    for event in payload.events:
        if isinstance(event, dict):
            kind = event.get("kind")
            if kind in {"state", "clip"} and isinstance(event.get("asset"), str):
                references.add(event["asset"])
            elif kind == "scroll":
                for name in ("start_asset", "end_asset"):
                    if isinstance(event.get(name), str):
                        references.add(event[name])
        elif isinstance(event, BrowserStateEventV1):
            references.add(event.asset)
        elif isinstance(event, BrowserClipEventV1):
            references.add(event.asset)
        elif isinstance(event, BrowserScrollEventV1):
            references.update((event.start_asset, event.end_asset))
    return references


def _load_json(path: Path, *, field: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PresentationValidationError(f"could not read {field}: {exc}") from exc
    return _mapping(value, field=field)


def _validate_terminal_cast(path: Path, *, duration_ms: int, field: str) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        header = json.loads(lines[0])
    except (OSError, UnicodeDecodeError, IndexError, json.JSONDecodeError) as exc:
        raise PresentationValidationError(f"could not read {field}: {exc}") from exc
    if not isinstance(header, dict) or header.get("version") not in {2, 3}:
        raise PresentationValidationError(f"{field} must be an asciinema v2 or v3 cast")
    if len(lines) - 1 > PRESENTATION_ITEM_LIMIT:
        raise PresentationValidationError(
            f"{field} events exceeds {PRESENTATION_ITEM_LIMIT} entries"
        )
    elapsed = 0.0
    last_absolute = 0.0
    for index, line in enumerate(lines[1:], start=2):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PresentationValidationError(f"invalid {field} event at line {index}") from exc
        if (
            not isinstance(event, list)
            or len(event) < 3
            or isinstance(event[0], bool)
            or not isinstance(event[0], (int, float))
            or not math.isfinite(float(event[0]))
            or event[0] < 0
        ):
            raise PresentationValidationError(f"invalid {field} event at line {index}")
        if header["version"] == 3:
            elapsed += float(event[0])
        else:
            if float(event[0]) < last_absolute:
                raise PresentationValidationError(f"unordered {field} event at line {index}")
            last_absolute = float(event[0])
    actual_ms = round((elapsed if header["version"] == 3 else last_absolute) * 1000)
    if actual_ms != duration_ms:
        raise PresentationValidationError(
            f"{field} duration {actual_ms}ms does not match manifest {duration_ms}ms"
        )


def _validate_audio(
    value: object,
    *,
    recording_duration_ms: int,
    root: Path | None,
    signatures: dict[str, PresentationFileSignatureV1] | None,
) -> PresentationAudioV1:
    field = "manifest audio"
    mapping = _mapping(value, field=field)
    _reject_unknown(mapping, PresentationAudioV1, field=field)
    _require_fields(mapping, _allowed_fields(PresentationAudioV1), field=field)
    metadata_path = validate_relative_presentation_path(
        mapping.get("metadata"), field=f"{field}.metadata"
    )
    intervals = mapping.get("intervals")
    if not isinstance(intervals, list) or not intervals:
        raise PresentationValidationError(f"{field}.intervals must be a non-empty list")

    source_duration_ms: int | None = None
    playback_end_ms: int | None = None
    if root is not None:
        metadata_file = _resolved_manifest_file(
            root, metadata_path, field=f"{field}.metadata"
        )
        if signatures is None or metadata_path not in signatures:
            raise PresentationValidationError(f"{field}.metadata is not signed")
        metadata = _load_json(metadata_file, field=f"{field}.metadata")
        if metadata.get("version") != 1:
            raise PresentationValidationError(f"{field}.metadata must use version 1")
        source_duration_ms = _integer(
            metadata.get("duration_ms"), field=f"{field}.metadata.duration_ms"
        )
        takes = metadata.get("takes")
        if not isinstance(takes, list) or not takes:
            raise PresentationValidationError(f"{field}.metadata.takes must be non-empty")
        expected_source_start = 0
        expected_playback_start = 0
        take_ids: set[str] = set()
        for index, take_value in enumerate(takes):
            take_field = f"{field}.metadata.takes.{index}"
            take = _mapping(take_value, field=take_field)
            take_id = take.get("id")
            if not isinstance(take_id, str) or not take_id or take_id in take_ids:
                raise PresentationValidationError(f"{take_field}.id is invalid")
            take_ids.add(take_id)
            source_start = _integer(
                take.get("source_start_ms"), field=f"{take_field}.source_start_ms"
            )
            source_end = _integer(
                take.get("source_end_ms"), field=f"{take_field}.source_end_ms"
            )
            if source_start != expected_source_start or source_end <= source_start:
                raise PresentationValidationError(f"{take_field} boundaries are invalid")
            expected_source_start = source_end
            take_source = validate_relative_presentation_path(
                take.get("src"), field=f"{take_field}.src"
            )
            source_file = _resolved_manifest_file(
                root, take_source, field=f"{take_field}.src"
            )
            if signatures is None or take_source not in signatures:
                raise PresentationValidationError(f"{take_field}.src is not signed")
            playback_values = (
                take.get("playback_src"),
                take.get("playback_start_ms"),
                take.get("playback_end_ms"),
            )
            if any(value is not None for value in playback_values):
                if any(value is None for value in playback_values):
                    raise PresentationValidationError(
                        f"{take_field} playback metadata is incomplete"
                    )
                playback_source = validate_relative_presentation_path(
                    playback_values[0], field=f"{take_field}.playback_src"
                )
                playback_start = _integer(
                    playback_values[1], field=f"{take_field}.playback_start_ms"
                )
                playback_end = _integer(
                    playback_values[2], field=f"{take_field}.playback_end_ms"
                )
                if (
                    playback_start != expected_playback_start
                    or playback_end <= playback_start
                    or playback_end > recording_duration_ms
                ):
                    raise PresentationValidationError(
                        f"{take_field} playback metadata is invalid"
                    )
                playback_file = _resolved_manifest_file(
                    root, playback_source, field=f"{take_field}.playback_src"
                )
                if signatures is None or playback_source not in signatures:
                    raise PresentationValidationError(
                        f"{take_field}.playback_src is not signed"
                    )
                expected_playback_start = playback_end
                playback_end_ms = playback_end
        if expected_source_start != source_duration_ms:
            raise PresentationValidationError(
                f"{field}.metadata takes do not cover narration source time"
            )

    previous_presentation_end = 0
    previous_source_end = 0
    for index, item in enumerate(intervals):
        interval_field = f"{field}.intervals.{index}"
        interval = _mapping(item, field=interval_field)
        _reject_unknown(interval, PresentationAudioIntervalV1, field=interval_field)
        _require_fields(
            interval,
            _allowed_fields(PresentationAudioIntervalV1),
            field=interval_field,
        )
        presentation_start = _integer(
            interval["presentation_start_ms"],
            field=f"{interval_field}.presentation_start_ms",
        )
        presentation_end = _integer(
            interval["presentation_end_ms"],
            field=f"{interval_field}.presentation_end_ms",
        )
        source_start = _integer(
            interval["source_start_ms"], field=f"{interval_field}.source_start_ms"
        )
        source_end = _integer(
            interval["source_end_ms"], field=f"{interval_field}.source_end_ms"
        )
        if presentation_end <= presentation_start:
            raise PresentationValidationError(
                f"{interval_field} presentation duration must be positive"
            )
        if source_end <= source_start:
            raise PresentationValidationError(
                f"{interval_field} source duration must be positive"
            )
        if presentation_start < previous_presentation_end:
            raise PresentationValidationError(
                f"{interval_field} presentation time overlaps the previous interval"
            )
        if source_start < previous_source_end:
            raise PresentationValidationError(
                f"{interval_field} source time overlaps the previous interval"
            )
        if presentation_end > recording_duration_ms:
            raise PresentationValidationError(
                f"{interval_field} presentation end exceeds the recording duration"
            )
        presentation_duration = presentation_end - presentation_start
        source_duration = source_end - source_start
        if presentation_duration != source_duration:
            raise PresentationValidationError(
                f"{interval_field} presentation duration is {presentation_duration}ms "
                f"but source duration is {source_duration}ms"
            )
        presentation_gap = presentation_start - previous_presentation_end
        source_gap = source_start - previous_source_end
        if source_gap > presentation_gap:
            raise PresentationValidationError(
                f"{interval_field} source gap is {source_gap}ms but presentation "
                f"gap is only {presentation_gap}ms"
            )
        previous_presentation_end = presentation_end
        previous_source_end = source_end
    if source_duration_ms is not None and previous_source_end != source_duration_ms:
        raise PresentationValidationError(
            f"{field}.intervals end at {previous_source_end}ms but narration source "
            f"duration is {source_duration_ms}ms"
        )
    if playback_end_ms is not None and playback_end_ms != previous_presentation_end:
        raise PresentationValidationError(
            f"{field} playback audio does not cover its presentation intervals"
        )
    return _typed(mapping, PresentationAudioV1, field=field)


def _validate_browser_presentation_header(
    value: object,
    *,
    field: str,
) -> PresentationBrowserHeaderV1:
    browser_mapping = _mapping(value, field=field)
    _reject_unknown(browser_mapping, PresentationBrowserHeaderV1, field=field)
    _require_fields(
        browser_mapping,
        _allowed_fields(PresentationBrowserHeaderV1),
        field=field,
    )
    window = _mapping(browser_mapping["window"], field=f"{field}.window")
    _reject_unknown(window, PresentationWindowV1, field=f"{field}.window")
    if window.get("mode") not in {"none", "framed"}:
        raise PresentationValidationError(f"{field}.window.mode is invalid")
    _non_empty_string(window.get("theme"), field=f"{field}.window.theme")
    title = window.get("title")
    if title is not None and not isinstance(title, str):
        raise PresentationValidationError(f"{field}.window.title must be a string")
    chrome = _mapping(browser_mapping["chrome"], field=f"{field}.chrome")
    _reject_unknown(chrome, PresentationChromeV1, field=f"{field}.chrome")
    if chrome.get("mode") not in {"hidden", "minimal", "full"}:
        raise PresentationValidationError(f"{field}.chrome.mode is invalid")
    return _typed(browser_mapping, PresentationBrowserHeaderV1, field=field)


def _validate_presentation_header(value: object) -> PresentationHeaderV1:
    field = "manifest presentation"
    mapping = _mapping(value, field=field)
    _reject_unknown(mapping, PresentationHeaderV1, field=field)
    if not isinstance(mapping.get("guided", False), bool):
        raise PresentationValidationError(f"{field}.guided must be a boolean")
    browser = mapping.get("browser")
    if browser is not None:
        _validate_browser_presentation_header(browser, field=f"{field}.browser")
    return _typed(mapping, PresentationHeaderV1, field=field)


def validate_presentation_manifest(
    value: object,
    *,
    manifest_dir: Path | None = None,
) -> PresentationManifestV1:
    mapping = _mapping(value, field="presentation manifest")
    _reject_unknown(mapping, PresentationManifestV1, field="presentation manifest")
    _require_fields(
        mapping,
        _allowed_fields(PresentationManifestV1) - {"audio"},
        field="presentation manifest",
    )
    if mapping.get("manifest_version") != 1:
        raise PresentationValidationError("manifest_version must be 1")
    signatures = (
        None
        if manifest_dir is None
        else _load_presentation_signatures(
            manifest_dir,
            mapping.get("signatures"),
        )
    )
    recording = _mapping(mapping.get("recording"), field="manifest recording")
    _non_empty_string(recording.get("id"), field="manifest recording.id")
    recording_duration_ms = _integer(
        recording.get("duration_ms"), field="manifest recording.duration_ms"
    )
    title = recording.get("title")
    if title is not None and not isinstance(title, str):
        raise PresentationValidationError("manifest recording.title must be a string")
    renderers = _mapping(mapping.get("renderers"), field="manifest renderers")
    for name, renderer in renderers.items():
        if name not in {"visualization", "terminal", "browser"}:
            raise PresentationValidationError(f"unsupported renderer {name!r}")
        renderer_mapping = _mapping(renderer, field=f"manifest renderers.{name}")
        if renderer_mapping != {"payload_version": 1}:
            raise PresentationValidationError(
                f"manifest renderer {name!r} must use payload_version 1"
            )
    panes = mapping.get("panes")
    if not isinstance(panes, list) or not panes:
        raise PresentationValidationError("manifest panes must be a non-empty list")
    if len(panes) > PRESENTATION_PANE_LIMIT:
        raise PresentationValidationError(
            f"manifest panes exceeds {PRESENTATION_PANE_LIMIT} entries"
        )
    pane_renderers: dict[str, str] = {}
    for index, value in enumerate(panes):
        field = f"manifest panes.{index}"
        pane = _mapping(value, field=field)
        _reject_unknown(pane, PresentationPaneV1, field=field)
        _require_fields(pane, {"id", "renderer"}, field=field)
        pane_id = _presentation_id(pane.get("id"), field=f"{field}.id")
        if pane_id in pane_renderers:
            raise PresentationValidationError(f"duplicate manifest pane id {pane_id!r}")
        renderer = pane.get("renderer")
        if renderer not in {"visualization", "terminal", "browser"}:
            raise PresentationValidationError(f"{field}.renderer is unsupported")
        if renderer not in renderers:
            raise PresentationValidationError(
                f"{field}.renderer {renderer!r} is not declared"
            )
        pane_renderers[pane_id] = renderer
    beats = mapping.get("beats")
    if not isinstance(beats, list) or not beats:
        raise PresentationValidationError("manifest beats must be a non-empty list")
    structure_count = len(beats)
    if structure_count > PRESENTATION_ITEM_LIMIT:
        raise PresentationValidationError(
            f"manifest aggregate structure exceeds {PRESENTATION_ITEM_LIMIT} entries"
        )
    assets = _mapping(mapping.get("assets"), field="manifest assets")
    asset_paths: set[str] = set()
    for asset_id, asset in assets.items():
        _, path = _validate_asset(
            asset_id,
            asset,
            root=manifest_dir,
            signatures=signatures,
        )
        if path in asset_paths:
            raise PresentationValidationError(f"duplicate manifest asset path {path!r}")
        asset_paths.add(path)

    expected_offset = 0
    beat_ids: set[str] = set()
    used_panes: set[str] = set()
    payload_paths: set[str] = set()
    pane_beat_ids_by_pane: dict[str, set[str]] = {
        pane_id: set() for pane_id in pane_renderers
    }
    for index, value in enumerate(beats):
        field = f"manifest beats.{index}"
        beat = _mapping(value, field=field)
        _reject_unknown(beat, PresentationBeatV1, field=field)
        _require_fields(
            beat,
            {
                "id",
                "heading",
                "offset_ms",
                "duration_ms",
                "layout",
                "pane_tracks",
            },
            field=field,
        )
        beat_id = _presentation_id(beat.get("id"), field=f"{field}.id")
        if beat_id in beat_ids:
            raise PresentationValidationError(f"duplicate manifest beat id {beat_id!r}")
        beat_ids.add(beat_id)
        if not isinstance(beat.get("heading"), str):
            raise PresentationValidationError(f"{field}.heading must be a string")
        offset_ms = _integer(beat.get("offset_ms"), field=f"{field}.offset_ms")
        duration_ms = _integer(beat.get("duration_ms"), field=f"{field}.duration_ms")
        if offset_ms != expected_offset:
            raise PresentationValidationError(
                f"{field}.offset_ms must equal the preceding beat end {expected_offset}"
            )
        expected_offset = offset_ms + duration_ms

        layout = _mapping(beat.get("layout"), field=f"{field}.layout")
        _reject_unknown(layout, PresentationPaneLayoutV1, field=f"{field}.layout")
        _require_fields(layout, {"areas"}, field=f"{field}.layout")
        areas = layout.get("areas")
        if not isinstance(areas, list) or not areas:
            raise PresentationValidationError(
                f"{field}.layout.areas must be a non-empty list"
            )
        layout_panes: set[str] = set()
        column_count: int | None = None
        for row_index, row in enumerate(areas):
            row_field = f"{field}.layout.areas.{row_index}"
            if not isinstance(row, list) or not row:
                raise PresentationValidationError(
                    f"{row_field} must be a non-empty list"
                )
            if column_count is None:
                column_count = len(row)
            elif len(row) != column_count:
                raise PresentationValidationError(
                    f"{field}.layout.areas must be rectangular"
                )
            structure_count += len(row)
            if structure_count > PRESENTATION_ITEM_LIMIT:
                raise PresentationValidationError(
                    "manifest aggregate structure exceeds "
                    f"{PRESENTATION_ITEM_LIMIT} entries"
                )
            for column_index, pane_value in enumerate(row):
                pane_id = _presentation_id(
                    pane_value,
                    field=f"{row_field}.{column_index}",
                )
                if pane_id not in pane_renderers:
                    raise PresentationValidationError(
                        f"{row_field}.{column_index} references unknown pane "
                        f"{pane_id!r}"
                    )
                layout_panes.add(pane_id)
        for pane_id in layout_panes:
            positions = [
                (row_index, column_index)
                for row_index, row in enumerate(areas)
                for column_index, value in enumerate(row)
                if value == pane_id
            ]
            row_indexes = [position[0] for position in positions]
            column_indexes = [position[1] for position in positions]
            for row_index in range(min(row_indexes), max(row_indexes) + 1):
                for column_index in range(
                    min(column_indexes), max(column_indexes) + 1
                ):
                    if areas[row_index][column_index] != pane_id:
                        raise PresentationValidationError(
                            f"{field}.layout area {pane_id!r} must form a "
                            "contiguous rectangle"
                        )

        pane_tracks = beat.get("pane_tracks")
        if not isinstance(pane_tracks, list) or not pane_tracks:
            raise PresentationValidationError(
                f"{field}.pane_tracks must be a non-empty list"
            )
        structure_count += len(pane_tracks)
        if structure_count > PRESENTATION_ITEM_LIMIT:
            raise PresentationValidationError(
                f"manifest aggregate structure exceeds {PRESENTATION_ITEM_LIMIT} entries"
            )
        track_panes: set[str] = set()
        beat_has_browser = False
        for track_index, track_value in enumerate(pane_tracks):
            track_field = f"{field}.pane_tracks.{track_index}"
            track = _mapping(track_value, field=track_field)
            _reject_unknown(track, PresentationPaneTrackV1, field=track_field)
            _require_fields(
                track,
                {"pane_id", "initial", "beats"},
                field=track_field,
            )
            pane_id = _presentation_id(
                track.get("pane_id"), field=f"{track_field}.pane_id"
            )
            if pane_id not in pane_renderers:
                raise PresentationValidationError(
                    f"{track_field}.pane_id references unknown pane {pane_id!r}"
                )
            if pane_id in track_panes:
                raise PresentationValidationError(
                    f"{field} has duplicate pane track {pane_id!r}"
                )
            track_panes.add(pane_id)
            used_panes.add(pane_id)
            renderer = pane_renderers[pane_id]
            beat_has_browser = beat_has_browser or renderer == "browser"
            try:
                PresentationPaneInitial(track.get("initial"))
            except (TypeError, ValueError) as exc:
                raise PresentationValidationError(
                    f"{track_field}.initial is invalid"
                ) from exc

            pane_beats = track.get("beats")
            if not isinstance(pane_beats, list) or not pane_beats:
                raise PresentationValidationError(
                    f"{track_field}.beats must be a non-empty list"
                )
            structure_count += len(pane_beats)
            if structure_count > PRESENTATION_ITEM_LIMIT:
                raise PresentationValidationError(
                    "manifest aggregate structure exceeds "
                    f"{PRESENTATION_ITEM_LIMIT} entries"
                )
            pane_beat_ids = pane_beat_ids_by_pane[pane_id]
            preceding_end = 0
            for pane_beat_index, pane_beat_value in enumerate(pane_beats):
                pane_beat_field = f"{track_field}.beats.{pane_beat_index}"
                pane_beat = _mapping(pane_beat_value, field=pane_beat_field)
                _reject_unknown(
                    pane_beat, PresentationPaneBeatV1, field=pane_beat_field
                )
                _require_fields(
                    pane_beat,
                    {
                        "id",
                        "offset_ms",
                        "duration_ms",
                        "payload",
                        "transition",
                    },
                    field=pane_beat_field,
                )
                pane_beat_id = _presentation_id(
                    pane_beat.get("id"), field=f"{pane_beat_field}.id"
                )
                if pane_beat_id in pane_beat_ids:
                    raise PresentationValidationError(
                        f"duplicate pane beat id {pane_beat_id!r} in pane {pane_id!r}"
                    )
                pane_beat_ids.add(pane_beat_id)
                pane_offset_ms = _integer(
                    pane_beat.get("offset_ms"),
                    field=f"{pane_beat_field}.offset_ms",
                )
                pane_duration_ms = _integer(
                    pane_beat.get("duration_ms"),
                    field=f"{pane_beat_field}.duration_ms",
                )
                if pane_offset_ms < preceding_end:
                    raise PresentationValidationError(
                        f"{pane_beat_field} overlaps the preceding pane beat "
                        f"ending at {preceding_end}ms"
                    )
                pane_end = pane_offset_ms + pane_duration_ms
                if pane_end > duration_ms:
                    raise PresentationValidationError(
                        f"{pane_beat_field} ends after outer beat {beat_id!r}"
                    )
                preceding_end = pane_end

                transition = _mapping(
                    pane_beat.get("transition"),
                    field=f"{pane_beat_field}.transition",
                )
                _reject_unknown(
                    transition,
                    PresentationPaneTransitionV1,
                    field=f"{pane_beat_field}.transition",
                )
                _require_fields(
                    transition,
                    {"kind", "duration_ms"},
                    field=f"{pane_beat_field}.transition",
                )
                try:
                    transition_kind = PresentationPaneTransitionKind(
                        transition.get("kind")
                    )
                except (TypeError, ValueError) as exc:
                    raise PresentationValidationError(
                        f"{pane_beat_field}.transition.kind is invalid"
                    ) from exc
                transition_duration_ms = _integer(
                    transition.get("duration_ms"),
                    field=f"{pane_beat_field}.transition.duration_ms",
                )
                if (
                    transition_kind is PresentationPaneTransitionKind.cut
                    and transition_duration_ms != 0
                ):
                    raise PresentationValidationError(
                        f"{pane_beat_field} cut transition duration must be 0"
                    )
                if transition_duration_ms > pane_duration_ms:
                    raise PresentationValidationError(
                        f"{pane_beat_field} transition duration exceeds pane beat "
                        "duration"
                    )

                payload_path = validate_relative_presentation_path(
                    pane_beat.get("payload"),
                    field=f"{pane_beat_field}.payload",
                )
                if payload_path in payload_paths:
                    raise PresentationValidationError(
                        f"duplicate manifest pane beat payload path {payload_path!r}"
                    )
                payload_paths.add(payload_path)

                browser = pane_beat.get("browser")
                if browser is not None:
                    if renderer != "browser":
                        raise PresentationValidationError(
                            f"{pane_beat_field}.browser is invalid for terminal panes"
                        )
                    _validate_browser_presentation_header(
                        browser,
                        field=f"{pane_beat_field}.browser",
                    )
                if manifest_dir is not None:
                    payload_file = _resolved_manifest_file(
                        manifest_dir,
                        payload_path,
                        field=f"{pane_beat_field}.payload",
                    )
                    if signatures is None or payload_path not in signatures:
                        raise PresentationValidationError(
                            f"{pane_beat_field}.payload is not signed"
                        )
                    payload_duration_ms = (
                        pane_duration_ms - transition_duration_ms
                    )
                    if renderer == "browser":
                        payload_mapping = _load_json(
                            payload_file, field=f"{pane_beat_field}.payload"
                        )
                        payload = validate_browser_payload(payload_mapping)
                        if (
                            payload.beat_id != pane_beat_id
                            or payload.duration_ms != payload_duration_ms
                        ):
                            raise PresentationValidationError(
                                f"{pane_beat_field}.payload identity or duration "
                                "does not match the manifest"
                            )
                        missing_assets = _browser_asset_references(payload) - set(
                            assets
                        )
                        if missing_assets:
                            missing = ", ".join(sorted(missing_assets))
                            raise PresentationValidationError(
                                f"{pane_beat_field}.payload references unknown "
                                f"assets: {missing}"
                            )
                    elif renderer == "visualization":
                        payload_mapping = _load_json(
                            payload_file, field=f"{pane_beat_field}.payload"
                        )
                        payload = validate_visualization_payload(payload_mapping)
                        if (
                            payload.beat_id != pane_beat_id
                            or payload.duration_ms != payload_duration_ms
                        ):
                            raise PresentationValidationError(
                                f"{pane_beat_field}.payload identity or duration "
                                "does not match the manifest"
                            )
                    else:
                        _validate_terminal_cast(
                            payload_file,
                            duration_ms=payload_duration_ms,
                            field=f"{pane_beat_field}.payload",
                        )
        if layout_panes != track_panes:
            raise PresentationValidationError(
                f"{field} layout and pane tracks must reference the same panes"
            )

        guide = beat.get("guide")
        if guide is not None:
            guide_mapping = _mapping(guide, field=f"{field}.guide")
            _reject_unknown(guide_mapping, PresentationGuideV1, field=f"{field}.guide")
            commands = guide_mapping.get("commands", [])
            if not isinstance(commands, list):
                raise PresentationValidationError(
                    f"{field}.guide.commands must be a list"
                )
            for command_index, command in enumerate(commands):
                _non_empty_string(
                    command,
                    field=f"{field}.guide.commands.{command_index}",
                )
            summary = guide_mapping.get("summary")
            if summary is not None:
                _non_empty_string(summary, field=f"{field}.guide.summary")
            hint = guide_mapping.get("success_hint")
            if beat_has_browser:
                _non_empty_string(hint, field=f"{field}.guide.success_hint")
            elif hint is not None and not isinstance(hint, str):
                raise PresentationValidationError(
                    f"{field}.guide.success_hint must be a string"
                )
        player = beat.get("player")
        if player is not None:
            player_mapping = _mapping(player, field=f"{field}.player")
            _reject_unknown(
                player_mapping,
                PresentationBeatPlayerV1,
                field=f"{field}.player",
            )
            highlight = _mapping(
                player_mapping.get("highlight"),
                field=f"{field}.player.highlight",
            )
            _reject_unknown(
                highlight,
                PresentationPlayerToolbarHighlightV1,
                field=f"{field}.player.highlight",
            )
            try:
                PlayerToolbarControl(highlight.get("control"))
            except (TypeError, ValueError) as exc:
                raise PresentationValidationError(
                    f"{field}.player.highlight.control is invalid"
                ) from exc
            start_ms = _integer(
                highlight.get("start_ms"),
                field=f"{field}.player.highlight.start_ms",
            )
            end_ms = _integer(
                highlight.get("end_ms"),
                field=f"{field}.player.highlight.end_ms",
            )
            if end_ms <= start_ms or end_ms > duration_ms:
                raise PresentationValidationError(
                    f"{field}.player.highlight timing is invalid"
                )
        transition = beat.get("transition_in")
        if transition not in {None, "cut", "fade", "window-open"}:
            raise PresentationValidationError(f"{field}.transition_in is invalid")
    if expected_offset != recording_duration_ms:
        raise PresentationValidationError(
            "manifest final beat end does not match recording duration"
        )
    if set(pane_renderers) != used_panes:
        raise PresentationValidationError(
            "manifest panes must all be used by outer beats"
        )
    if set(renderers) != set(pane_renderers.values()):
        raise PresentationValidationError(
            "manifest renderer header must exactly match renderers used by panes"
        )
    presentation = _validate_presentation_header(mapping.get("presentation"))
    if "browser" in pane_renderers.values() and presentation.browser is None:
        raise PresentationValidationError(
            "manifest presentation.browser is required for browser panes"
        )
    audio = mapping.get("audio")
    if audio is not None:
        _validate_audio(
            audio,
            recording_duration_ms=recording_duration_ms,
            root=manifest_dir,
            signatures=signatures,
        )
    return _typed(mapping, PresentationManifestV1, field="presentation manifest")


def serialize_presentation_manifest(
    manifest: PresentationManifestV1,
) -> dict[str, Any]:
    result = _structured_payload(manifest, field="presentation manifest")
    if result.get("audio") is None:
        result.pop("audio", None)
    validate_presentation_manifest(result)
    return result
