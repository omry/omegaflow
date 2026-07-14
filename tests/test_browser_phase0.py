from __future__ import annotations

import json
from pathlib import Path

from omegaflow.browser_runtime import (
    CHROMIUM_BROWSER_VERSION,
    CHROMIUM_REVISION,
    PLAYWRIGHT_PACKAGE_VERSION,
)


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "tests" / "fixtures" / "browser_phase0" / "policy-v1.json"
MEASUREMENTS_PATH = ROOT / "docs" / "future" / "browser-recording-phase0-measurements.json"
DESIGN_PATH = ROOT / "docs" / "future" / "browser-recording-design.md"


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_phase0_policy_matches_pinned_runtime_and_design() -> None:
    policy = load_json(POLICY_PATH)
    design = DESIGN_PATH.read_text(encoding="utf-8")

    assert policy["runtime"] == {
        "playwright": PLAYWRIGHT_PACKAGE_VERSION,
        "chromium_revision": CHROMIUM_REVISION,
        "chromium_version": CHROMIUM_BROWSER_VERSION,
    }
    for policy_name in (
        "stable-v1",
        "input-overlay-v1",
        "scroll-v1",
        "playwright-video-v1",
        "redaction-v1",
    ):
        assert policy_name in design
    assert policy["dynamic_fragment"]["decoded_asset_memory_budget_status"] == (
        "provisional-until-real-device-release-gate"
    )
    assert policy["renderer"]["real_device_validation"] == "phase-1-release-gate"
    assert "real-device playback" in design.lower()
    assert "Phase 1 release gate" in design


def test_phase0_measurements_satisfy_constrained_policy() -> None:
    policy = load_json(POLICY_PATH)
    measurements = load_json(MEASUREMENTS_PATH)

    stable = measurements["stable_state"]
    assert stable["passed"] and stable["runs"] == 10 and stable["unique_hashes"] == 1
    assert all(
        result["passed"]
        and result["runs"] == 10
        and result["unique_final_hashes"] == 1
        for result in stable["rebuild_matrix"].values()
    )
    assert stable["strategy_comparison"]["selected"] == (
        "explicit-ready-then-rendered-frame-convergence"
    )
    assert stable["strategy_comparison"]["navigation_lifecycle_only"] != (
        stable["strategy_comparison"]["explicit_ready"]
    )
    assert stable["cases"]["static"]["classification"] == "stable"
    assert stable["cases"]["finite_transition"]["classification"] == "stable"
    assert stable["cases"]["async_content"]["classification"] == "stable"
    assert stable["cases"]["polling"]["classification"] == "dynamic"
    assert stable["cases"]["continuous_motion"]["classification"] == "dynamic"

    text = measurements["text_entry"]
    assert text["literal_overlay_candidate"]
    assert text["literal_overlay_ssim"] >= 0.995
    assert all(case["same_presentation_value"] for case in text["cases"].values())
    assert all(
        case["per_character_frames"]["count"] > 0
        for case in text["cases"].values()
    )
    assert text["cases"]["project-name"]["overlay"] == "input-overlay-v1"
    assert text["cases"]["controlled"]["overlay"] == "input-overlay-v1"
    assert all(
        text["cases"][name]["facts"]["caret"]["complete"]
        and text["cases"][name]["facts"]["caret"]["within_clipping_rect"]
        for name in ("project-name", "controlled")
    )
    assert all(
        text["cases"][name]["overlay"] == "captured-state-or-clip"
        for name in ("notes", "formatted", "editable")
    )

    scroll = measurements["scroll"]["cases"]
    assert scroll["nested_static"]["classification"] == "reconstruct"
    assert scroll["nested_static"]["visual_replay_passed"]
    assert scroll["nested_static"]["replay_ssim"] >= 0.999
    assert all(
        scroll[name]["classification"] == "clip"
        for name in (
            "sticky",
            "fixed",
            "virtualized",
            "scroll_linked",
            "dynamic_fragment",
            "document",
        )
    )

    dynamic = measurements["dynamic_fragment"]
    dynamic_policy = policy["dynamic_fragment"]
    assert dynamic["passed"] and dynamic["midpoint_seek"]
    assert dynamic["sensitive_fixture_sentinels_absent"]
    assert all(dynamic["cases"].values())
    assert dynamic["codec"] == dynamic_policy["codec"]
    assert dynamic["duration_seconds"] * 1000 <= dynamic_policy["max_duration_ms"]
    assert dynamic["bytes"] <= dynamic_policy["max_encoded_bytes"]
    assert dynamic["trim"]["method"] == dynamic_policy["trim"]
    assert dynamic["trim"]["crf"] == dynamic_policy["crf"]
    assert (
        dynamic["trim"]["target_bitrate_bps"]
        == dynamic_policy["target_bitrate_bps"]
    )
    assert (
        dynamic["trim"]["duration_error_ms"]
        <= dynamic_policy["max_trim_duration_error_ms"]
    )
    assert dynamic["trim"]["selected"]["audio_streams"] == 0
    assert (
        dynamic["frame_timing"]["large_frame_gaps"]
        <= dynamic_policy["max_large_frame_gaps"]
    )
    assert dynamic["frame_timing"]["playback_smoothness_passed"]
    assert dynamic["screencast"]["bytes"] > dynamic["bytes"]

    redaction = measurements["redaction"]
    assert redaction["static_mask_passed"]
    assert redaction["created_after_action_mask_passed"]
    assert redaction["dynamic_created_after_action_sample_mask_passed"]
    assert redaction["moving_target_exercised"]
    assert redaction["resizing_target_exercised"]
    assert redaction["disappeared_target_detected"]
    assert not redaction["sampled_per_frame_masks_passed"]
    assert not redaction["static_postprocess_tracks_movement"]
    assert not redaction["dynamic_redaction_supported"]
    assert redaction["fail_closed"]


def test_phase0_composition_and_public_data() -> None:
    measurements = load_json(MEASUREMENTS_PATH)
    composition = measurements["composition"]
    assert composition["modalities"] == ["terminal", "browser", "terminal"]
    assert composition["narration_takes"][0]["members"] == [
        "terminal-start",
        "browser-create",
    ]
    for field in (
        "cleanup_attempted",
        "failure_observed",
        "fixed_capture_surface_passed",
        "loading_hide_after_ready",
        "loading_hide_first_public_state",
        "loading_show_visible",
        "mobile_controls_visible",
        "private_artifact_isolation",
        "boundary_seek_passed",
        "after_binding_passed",
        "browser_cleanup_attempted",
        "browser_session_persisted",
        "fade_transition",
        "hidden_chrome",
        "responsive_only_not_real_device",
        "renderer_switch_passed",
        "shared_state_consumed",
        "terminal_cleanup_attempted",
        "terminal_session_persisted",
        "touch_targets_passed",
        "uniform_scale_passed",
        "windowless",
        "windowless_viewport_passed",
    ):
        assert composition[field]
    assert composition["seek_results"]["900"] == {
        "id": "browser-create",
        "renderer": "browser",
        "local_ms": 0,
    }
    assert composition["seek_results"]["2200"] == {
        "id": "terminal-verify",
        "renderer": "terminal",
        "local_ms": 0,
    }

    public_bytes = b"".join(
        path.read_bytes()
        for path in [POLICY_PATH, MEASUREMENTS_PATH]
        if path.is_file()
    )
    for sentinel in (
        b"http://127.0.0.1/private-capture",
        b"token-fixture-value",
        b"data-testid=account-email",
    ):
        assert sentinel not in public_bytes
