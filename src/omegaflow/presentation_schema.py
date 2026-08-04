"""Versioned OmegaConf dataclasses for generated presentation artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PlayerToolbarControl(str, Enum):
    previous = "previous"
    play = "play"
    restart = "restart"
    next = "next"
    guided = "guided"
    speed = "speed"
    mute = "mute"


class PresentationPaneInitial(str, Enum):
    first = "first"
    hidden = "hidden"


class PresentationPaneTransitionKind(str, Enum):
    cut = "cut"
    fade = "fade"


class VisualizationTokenKind(str, Enum):
    key = "key"
    string = "string"
    number = "number"
    boolean = "boolean"
    comment = "comment"
    keyword = "keyword"
    operator = "operator"
    punctuation = "punctuation"


@dataclass
class BrowserViewportV1:
    width: int = 0
    height: int = 0
    device_scale_factor: float = 1.0


@dataclass
class BrowserPointV1:
    x: float = 0.0
    y: float = 0.0


@dataclass
class BrowserPointerStateV1(BrowserPointV1):
    visible: bool = True


@dataclass
class BrowserBoundsV1(BrowserPointV1):
    width: float = 0.0
    height: float = 0.0


@dataclass
class BrowserCurveV1:
    x1: float = 0.0
    y1: float = 0.0
    x2: float = 0.0
    y2: float = 0.0


@dataclass
class BrowserTextStyleV1:
    font_family: str = ""
    font_size: float = 0.0
    font_weight: str = "normal"
    font_style: str = "normal"
    line_height: float = 0.0
    letter_spacing: float = 0.0
    color: str = ""
    text_align: str = "start"
    padding_top: float = 0.0
    padding_right: float = 0.0
    padding_bottom: float = 0.0
    padding_left: float = 0.0
    clipping_rect: BrowserBoundsV1 = field(default_factory=BrowserBoundsV1)
    selection_start: int | None = None
    selection_end: int | None = None
    caret_visible: bool = False


@dataclass
class BrowserEventV1:
    kind: str = ""
    action_id: str = ""
    at_ms: int = 0
    end_ms: int = 0


@dataclass
class BrowserStateEventV1(BrowserEventV1):
    asset: str = ""
    transition: str = "cut"


@dataclass
class BrowserPointerMoveEventV1(BrowserEventV1):
    start: BrowserPointV1 = field(default_factory=BrowserPointV1)
    end: BrowserPointV1 = field(default_factory=BrowserPointV1)
    curve: BrowserCurveV1 = field(default_factory=BrowserCurveV1)


@dataclass
class BrowserDragEventV1(BrowserPointerMoveEventV1):
    button: str = "left"


@dataclass
class BrowserClickEventV1(BrowserEventV1):
    point: BrowserPointV1 = field(default_factory=BrowserPointV1)
    button: str = "left"


@dataclass
class BrowserPointerVisibilityEventV1(BrowserEventV1):
    visible: bool = True


@dataclass
class BrowserFocusEventV1(BrowserEventV1):
    target: BrowserBoundsV1 = field(default_factory=BrowserBoundsV1)


@dataclass
class BrowserTextEventV1(BrowserEventV1):
    target: BrowserBoundsV1 = field(default_factory=BrowserBoundsV1)
    initial: str = ""
    final: str = ""
    mode: str = "literal"
    style: BrowserTextStyleV1 = field(default_factory=BrowserTextStyleV1)


@dataclass
class BrowserKeyEventV1(BrowserEventV1):
    key: str = ""
    label: str = ""


@dataclass
class BrowserScrollEventV1(BrowserEventV1):
    container: BrowserBoundsV1 = field(default_factory=BrowserBoundsV1)
    start: BrowserPointV1 = field(default_factory=BrowserPointV1)
    end: BrowserPointV1 = field(default_factory=BrowserPointV1)
    start_asset: str = ""
    end_asset: str = ""


@dataclass
class BrowserClipEventV1(BrowserEventV1):
    asset: str = ""
    trim_start_ms: int = 0
    trim_end_ms: int = 0
    has_audio: bool = False


@dataclass
class BrowserDisplayUrlEventV1(BrowserEventV1):
    value: str = ""


@dataclass
class BrowserCompleteEventV1(BrowserEventV1):
    timing: str = "presentation"


BROWSER_EVENT_SCHEMAS_V1: dict[str, type[BrowserEventV1]] = {
    "state": BrowserStateEventV1,
    "pointer_move": BrowserPointerMoveEventV1,
    "drag": BrowserDragEventV1,
    "pointer_visibility": BrowserPointerVisibilityEventV1,
    "click": BrowserClickEventV1,
    "focus": BrowserFocusEventV1,
    "text": BrowserTextEventV1,
    "key": BrowserKeyEventV1,
    "scroll": BrowserScrollEventV1,
    "clip": BrowserClipEventV1,
    "display_url": BrowserDisplayUrlEventV1,
    "complete": BrowserCompleteEventV1,
}


@dataclass
class BrowserAnimationPoliciesV1:
    pointer: str = "pointer-v1"
    typing: str = "natural-v1"


@dataclass
class BrowserPayloadV1:
    payload_version: int = 1
    beat_id: str = ""
    duration_ms: int = 0
    viewport: BrowserViewportV1 = field(default_factory=BrowserViewportV1)
    initial_state: str = ""
    initial_pointer: BrowserPointerStateV1 = field(
        default_factory=BrowserPointerStateV1
    )
    initial_display_url: str | None = None
    animation_policies: BrowserAnimationPoliciesV1 = field(
        default_factory=BrowserAnimationPoliciesV1
    )
    events: list[Any] = field(default_factory=list)


@dataclass
class VisualizationTokenV1:
    start: int = 0
    end: int = 0
    kind: VisualizationTokenKind = VisualizationTokenKind.keyword


@dataclass
class VisualizationHighlightV1:
    start: int = 0
    end: int = 0
    color: str = "cue"
    start_ms: int = 0
    end_ms: int = 0


@dataclass
class VisualizationPayloadV1:
    payload_version: int = 1
    beat_id: str = ""
    duration_ms: int = 0
    language: str = "text"
    text: str = ""
    highlights: list[VisualizationHighlightV1] = field(default_factory=list)
    tokens: list[VisualizationTokenV1] = field(default_factory=list)


@dataclass
class PresentationRecordingV1:
    id: str = ""
    title: str | None = None
    duration_ms: int = 0


@dataclass
class PresentationRendererV1:
    payload_version: int = 1


@dataclass
class PresentationWindowV1:
    mode: str = "none"
    theme: str = "kde-breeze"
    title: str | None = None
    opening_transition: str = "cut"


@dataclass
class PresentationChromeV1:
    mode: str = "hidden"


@dataclass
class PresentationBrowserHeaderV1:
    window: PresentationWindowV1 = field(default_factory=PresentationWindowV1)
    chrome: PresentationChromeV1 = field(default_factory=PresentationChromeV1)


@dataclass
class PresentationPaneTitleV1:
    visible: bool = True
    text: str | None = None
    alignment_x: str = "right"
    alignment_y: str = "top"
    position_x: str = "0.25rem"
    position_y: str = "0.25rem"


@dataclass
class PresentationPaneChromeV1:
    style: str = "framed"


@dataclass
class PresentationHeaderV1:
    guided: bool = False
    pane_chrome: PresentationPaneChromeV1 = field(
        default_factory=PresentationPaneChromeV1
    )
    browser: PresentationBrowserHeaderV1 | None = None


@dataclass
class PresentationAudioIntervalV1:
    presentation_start_ms: int = 0
    presentation_end_ms: int = 0
    source_start_ms: int = 0
    source_end_ms: int = 0


@dataclass
class PresentationAudioV1:
    metadata: str = ""
    intervals: list[PresentationAudioIntervalV1] = field(default_factory=list)


@dataclass
class PresentationAssetV1:
    path: str = ""
    media_type: str = ""


@dataclass
class PresentationGuideV1:
    commands: list[str] = field(default_factory=list)
    summary: str | None = None
    success_hint: str | None = None


@dataclass
class PresentationPlayerToolbarHighlightV1:
    control: PlayerToolbarControl | None = None
    start_ms: int = 0
    end_ms: int = 0


@dataclass
class PresentationBeatPlayerV1:
    highlight: PresentationPlayerToolbarHighlightV1 | None = None


@dataclass
class PresentationPaneV1:
    id: str = ""
    title: PresentationPaneTitleV1 = field(default_factory=PresentationPaneTitleV1)
    renderer: str = ""


@dataclass
class PresentationPaneLayoutV1:
    areas: list[list[str]] = field(default_factory=list)
    rows: list[float] | None = None


@dataclass
class PresentationPaneTransitionV1:
    kind: PresentationPaneTransitionKind = PresentationPaneTransitionKind.cut
    duration_ms: int = 0


@dataclass
class PresentationPaneBeatV1:
    id: str = ""
    offset_ms: int = 0
    duration_ms: int = 0
    payload: str = ""
    transition: PresentationPaneTransitionV1 = field(
        default_factory=PresentationPaneTransitionV1
    )
    browser: PresentationBrowserHeaderV1 | None = None


@dataclass
class PresentationPaneTrackV1:
    pane_id: str = ""
    initial: PresentationPaneInitial = PresentationPaneInitial.first
    beats: list[PresentationPaneBeatV1] = field(default_factory=list)


@dataclass
class PresentationBeatV1:
    id: str = ""
    heading: str = ""
    offset_ms: int = 0
    duration_ms: int = 0
    layout: PresentationPaneLayoutV1 = field(default_factory=PresentationPaneLayoutV1)
    pane_tracks: list[PresentationPaneTrackV1] = field(default_factory=list)
    guide: PresentationGuideV1 | None = None
    player: PresentationBeatPlayerV1 | None = None
    transition_in: str | None = None


@dataclass
class PresentationManifestV1:
    manifest_version: int = 1
    recording: PresentationRecordingV1 = field(default_factory=PresentationRecordingV1)
    renderers: dict[str, PresentationRendererV1] = field(default_factory=dict)
    presentation: PresentationHeaderV1 = field(default_factory=PresentationHeaderV1)
    audio: PresentationAudioV1 | None = None
    signatures: str = "signatures.json"
    assets: dict[str, PresentationAssetV1] = field(default_factory=dict)
    panes: list[PresentationPaneV1] = field(default_factory=list)
    beats: list[PresentationBeatV1] = field(default_factory=list)


@dataclass
class NarrationTimestampMemberV1:
    beat_id: str = ""
    text_start: int = 0
    text_end: int = 0
    source_start_ms: int = 0
    source_end_ms: int = 0


@dataclass
class NarrationAudioMemberV2:
    beat_id: str = ""
    text: str = ""
    text_start: int = 0
    text_end: int = 0


@dataclass
class NarrationTimestampWordV1:
    text: str = ""
    text_start: int = 0
    text_end: int = 0
    start_ms: int = 0
    end_ms: int = 0
    timing_source: str = ""
    timing_confidence: str = ""
    raw_word_start: int | None = None
    raw_word_end: int | None = None


@dataclass
class NarrationTimestampAnchorV1:
    beat_id: str = ""
    id: str = ""
    text_offset: int = 0
    source_ms: int = 0


@dataclass
class NarrationTimestampWaitV1:
    beat_id: str = ""
    target: str = ""
    text_offset: int = 0
    source_ms: int = 0
    gap_ms: int = 0


@dataclass
class NarrationTimestampSidecarV1:
    version: int = 1
    take_id: str = ""
    duration_ms: int = 0
    members: list[NarrationTimestampMemberV1] = field(default_factory=list)
    words: list[NarrationTimestampWordV1] = field(default_factory=list)
    anchors: list[NarrationTimestampAnchorV1] = field(default_factory=list)
    waits: list[NarrationTimestampWaitV1] = field(default_factory=list)


@dataclass
class NarrationAudioTakeV1:
    id: str = ""
    src: str = ""
    source_start_ms: int = 0
    source_end_ms: int = 0
    playback_src: str | None = None
    playback_start_ms: int | None = None
    playback_end_ms: int | None = None
    timestamps: str = ""
    members: list[NarrationAudioMemberV2] = field(default_factory=list)


@dataclass
class NarrationAudioMetadataV1:
    version: int = 1
    recording: str = ""
    duration_ms: int = 0
    takes: list[NarrationAudioTakeV1] = field(default_factory=list)


@dataclass
class PresentationFileSignatureV1:
    sha256: str = ""
    bytes: int = 0


@dataclass
class PresentationSignaturesV1:
    version: int = 1
    files: dict[str, PresentationFileSignatureV1] = field(default_factory=dict)


@dataclass
class PublishedSourceDependencyV1:
    path: str = ""
    sha256: str = ""


@dataclass
class PublishedRecordingMetadataV1:
    version: int = 1
    recording: str = ""
    capture_fingerprint: str = ""
    presentation_fingerprint: str = ""
    dependencies: list[PublishedSourceDependencyV1] = field(default_factory=list)
    versions: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
