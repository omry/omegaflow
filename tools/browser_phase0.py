#!/usr/bin/env python3
"""Run isolated browser-recording Phase 0 experiments against a local fixture."""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import json
import math
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "browser_phase0" / "index.html"
FIXTURE_DIR = FIXTURE.parent
STABLE_CSS = """
*, *::before, *::after {
  animation: none !important;
  transition: none !important;
  caret-color: transparent !important;
}
"""


class FixtureHandler(BaseHTTPRequestHandler):
    video_path: Path | None = None
    project_created = False

    def _send(self, content: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:  # noqa: N802
        request_path = self.path.split("?", 1)[0]
        if request_path in {"/", "/index.html"}:
            self._send(FIXTURE.read_bytes(), "text/html; charset=utf-8")
            return
        if request_path == "/prototype-player.html":
            self._send(
                (FIXTURE_DIR / "prototype-player.html").read_bytes(),
                "text/html; charset=utf-8",
            )
            return
        if request_path == "/fixture-video.webm" and self.video_path is not None:
            self._send(self.video_path.read_bytes(), "video/webm")
            return
        if self.path == "/api/project":
            self._send(
                json.dumps(
                    {
                        "id": "fixture-project",
                        "created": type(self).project_created,
                    }
                ).encode("utf-8"),
                "application/json",
            )
            return
        self._send(b"not found", "text/plain", HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/api/project":
            type(self).project_created = True
            self._send(b'{"id":"fixture-project"}', "application/json")
            return
        self._send(b"not found", "text/plain", HTTPStatus.NOT_FOUND)

    def log_message(self, _format: str, *_args: object) -> None:
        pass


@contextlib.contextmanager
def fixture_server() -> Iterator[str]:
    FixtureHandler.project_created = False
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def wait_for_stable_page(page: Any) -> None:
    page.wait_for_load_state("domcontentloaded")
    page.evaluate("document.fonts && document.fonts.ready")
    page.evaluate(
        "() => new Promise(resolve => requestAnimationFrame(() => "
        "requestAnimationFrame(resolve)))"
    )


def new_context(browser: Any, *, video_dir: Path | None = None) -> Any:
    options: dict[str, Any] = {
        "viewport": {"width": 1440, "height": 900},
        "screen": {"width": 1440, "height": 900},
        "device_scale_factor": 1,
        "locale": "en-US",
        "timezone_id": "UTC",
        "color_scheme": "light",
        "reduced_motion": "reduce",
        "permissions": [],
        "service_workers": "block",
    }
    if video_dir is not None:
        video_dir.mkdir(parents=True, exist_ok=True)
        options["record_video_dir"] = str(video_dir)
        options["record_video_size"] = {"width": 1440, "height": 900}
    return browser.new_context(**options)


def stable_state_experiment(
    browser: Any, base_url: str, output: Path, runs: int
) -> dict[str, Any]:
    def convergence(
        page: Any,
        selector: str,
        *,
        timeout_ms: int = 1200,
        consecutive: int = 3,
        interval_ms: int = 60,
    ) -> dict[str, Any]:
        locator = page.locator(selector)
        started = time.monotonic()
        last_hash: str | None = None
        matching = 0
        samples = 0
        frame_hashes: list[str] = []
        while (time.monotonic() - started) * 1000 < timeout_ms:
            digest = hashlib.sha256(locator.screenshot(animations="allow")).hexdigest()
            frame_hashes.append(digest)
            samples += 1
            matching = matching + 1 if digest == last_hash else 1
            last_hash = digest
            if matching >= consecutive:
                return {
                    "classification": "stable",
                    "elapsed_ms": round((time.monotonic() - started) * 1000),
                    "samples": samples,
                    "diagnostic_frame_hashes": list(dict.fromkeys(frame_hashes)),
                }
            page.wait_for_timeout(interval_ms)
        return {
            "classification": "dynamic",
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "samples": samples,
            "diagnostic_frame_hashes": list(dict.fromkeys(frame_hashes)),
        }

    matrix: dict[str, list[dict[str, Any]]] = {
        "static": [],
        "finite_transition": [],
        "async_dialog": [],
    }
    stable_dir = output / "stable"
    stable_dir.mkdir(parents=True, exist_ok=True)
    for index in range(runs):
        context = new_context(browser)
        page = context.new_page()
        page.goto(base_url, wait_until="domcontentloaded")
        wait_for_stable_page(page)

        static_result = convergence(page, "#static-target")
        static_bytes = page.locator("#static-target").screenshot(
            animations="disabled"
        )
        matrix["static"].append(
            {**static_result, "final_hash": hashlib.sha256(static_bytes).hexdigest()}
        )

        page.get_by_role("button", name="Start finite transition").click()
        transition_result = convergence(page, "#finite-box")
        transition_bytes = page.locator("#finite-box").screenshot(
            animations="disabled"
        )
        matrix["finite_transition"].append(
            {
                **transition_result,
                "final_hash": hashlib.sha256(transition_bytes).hexdigest(),
            }
        )

        page.get_by_role("button", name="Open dialog").click()
        page.wait_for_function(
            "document.querySelector('#dialog-message').textContent === "
            "'Async dialog ready'"
        )
        async_result = convergence(page, "#dialog")
        dialog_bytes = page.locator("#dialog").screenshot(animations="disabled")
        matrix["async_dialog"].append(
            {**async_result, "final_hash": hashlib.sha256(dialog_bytes).hexdigest()}
        )
        if index == 0:
            (stable_dir / "stable-state.png").write_bytes(static_bytes)
        context.close()

    context = new_context(browser)
    page = context.new_page()
    page.goto(base_url, wait_until="domcontentloaded")
    lifecycle_value = page.locator("#async-content").inner_text()
    page.goto(base_url, wait_until="domcontentloaded")
    page.wait_for_timeout(250)
    fixed_delay_value = page.locator("#async-content").inner_text()
    page.goto(base_url, wait_until="domcontentloaded")
    page.get_by_role("button", name="Open dialog").click()
    page.wait_for_function(
        "document.querySelector('#dialog-message').textContent === "
        "'Async dialog ready'"
    )
    explicit_ready_value = page.locator("#dialog-message").inner_text()
    ready_then_convergence = convergence(page, "#dialog")
    page.goto(base_url)
    polling_result = convergence(page, "#polling", timeout_ms=700)
    dynamic_result = convergence(page, "#dynamic-fragment", timeout_ms=700)
    context.close()

    def matrix_summary(values: list[dict[str, Any]]) -> dict[str, Any]:
        hashes = [str(value["final_hash"]) for value in values]
        return {
            "runs": len(values),
            "unique_final_hashes": len(set(hashes)),
            "passed": all(value["classification"] == "stable" for value in values)
            and len(set(hashes)) == 1,
            "results": values,
        }

    rebuild_matrix = {name: matrix_summary(values) for name, values in matrix.items()}

    def representative(name: str) -> dict[str, Any]:
        values = matrix[name]
        return {
            "classification": (
                "stable"
                if all(value["classification"] == "stable" for value in values)
                else "dynamic"
            ),
            "elapsed_ms": max(int(value["elapsed_ms"]) for value in values),
            "samples": max(int(value["samples"]) for value in values),
        }

    hashes = [str(value["final_hash"]) for value in matrix["static"]]
    return {
        "runs": runs,
        "unique_hashes": len(set(hashes)),
        "hashes": hashes,
        "passed": all(value["passed"] for value in rebuild_matrix.values()),
        "policy": {
            "consecutive_equal_frames": 3,
            "sample_interval_ms": 60,
            "timeout_ms": 1200,
            "ready_condition_precedes_sampling": True,
            "diagnostics": "first-last-and-differing-frame-hashes",
        },
        "strategy_comparison": {
            "navigation_lifecycle_only": lifecycle_value,
            "fixed_settling_delay_250ms": fixed_delay_value,
            "explicit_ready": explicit_ready_value,
            "explicit_ready_then_convergence": ready_then_convergence,
            "selected": "explicit-ready-then-rendered-frame-convergence",
        },
        "rebuild_matrix": rebuild_matrix,
        "cases": {
            "static": representative("static"),
            "finite_transition": representative("finite_transition"),
            "async_content": representative("async_dialog"),
            "polling": polling_result,
            "continuous_motion": dynamic_result,
        },
    }


def create_fixture_video(output: Path) -> Path:
    path = output / "fixture-video.webm"
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("Phase 0 requires ffmpeg to build the video fixture")
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=120x68:rate=24:duration=2",
            "-c:v",
            "libvpx",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


def _ssim(left: Path, right: Path) -> float | None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return None
    result = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-i",
            str(left),
            "-i",
            str(right),
            "-lavfi",
            "ssim",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    match = re.search(r"All:([0-9.]+)", result.stderr)
    return float(match.group(1)) if match else None


def _masked_region_is_black(path: Path, box: dict[str, float] | None) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None or box is None:
        return False
    inset = 2
    width = max(1, math.floor(box["width"]) - inset * 2)
    height = max(1, math.floor(box["height"]) - inset * 2)
    crop = "crop={}:{}:{}:{}".format(
        width,
        height,
        math.ceil(box["x"]) + inset,
        math.ceil(box["y"]) + inset,
    )
    result = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(path),
            "-vf",
            crop,
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0 and set(result.stdout) <= {0}


def _contains_rgb(path: Path, rgb: tuple[int, int, int]) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return True
    result = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(path),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        capture_output=True,
        check=False,
    )
    return result.returncode != 0 or bytes(rgb) in result.stdout


def text_entry_experiment(browser: Any, base_url: str, output: Path) -> dict[str, Any]:
    context = new_context(browser)
    page = context.new_page()
    page.goto(base_url)
    page.add_style_tag(content=STABLE_CSS)
    wait_for_stable_page(page)
    locator = page.get_by_test_id("project-name")
    locator.fill("OmegaFlow Project")
    locator.blur()
    text_dir = output / "text"
    text_dir.mkdir(parents=True, exist_ok=True)
    original = text_dir / "captured.png"
    overlay = text_dir / "overlay.png"
    locator.screenshot(path=str(original), animations="disabled")
    style = locator.evaluate(
        """element => {
          const style = getComputedStyle(element);
          const box = element.getBoundingClientRect();
          return {
            box: {x: box.x, y: box.y, width: box.width, height: box.height},
            font_family: style.fontFamily,
            font_size: parseFloat(style.fontSize),
            font_weight: style.fontWeight,
            font_style: style.fontStyle,
            line_height: parseFloat(style.lineHeight),
            letter_spacing: parseFloat(style.letterSpacing) || 0,
            color: style.color,
            text_align: style.textAlign,
            padding_top: parseFloat(style.paddingTop),
            padding_right: parseFloat(style.paddingRight),
            padding_bottom: parseFloat(style.paddingBottom),
            padding_left: parseFloat(style.paddingLeft),
          };
        }"""
    )
    locator.evaluate(
        """(element, text) => {
          const box = element.getBoundingClientRect();
          element.style.color = 'transparent';
          const overlay = element.cloneNode(true);
          overlay.id = 'phase0-text-overlay';
          overlay.value = text;
          overlay.readOnly = true;
          overlay.removeAttribute('data-testid');
          Object.assign(overlay.style, {
            position: 'fixed', left: `${box.x}px`, top: `${box.y}px`,
            width: `${box.width}px`, height: `${box.height}px`,
            margin: '0', color: 'rgb(24, 32, 51)', pointerEvents: 'none',
            zIndex: '1000',
          });
          document.body.appendChild(overlay);
        }""",
        "OmegaFlow Project",
    )
    locator.screenshot(path=str(overlay), animations="disabled")
    password = page.get_by_test_id("password")
    password.fill("phase0-secret")
    password_style = password.evaluate("element => getComputedStyle(element).fontFamily")
    score = _ssim(original, overlay)
    cases: dict[str, Any] = {}
    for test_id, value in (
        ("project-name", "OmegaFlow Project"),
        ("notes", "A wrapped note for the fixture."),
        ("controlled", "mixedCase"),
        ("formatted", "1234"),
        ("editable", "Rich fixture text"),
    ):
        page.goto(base_url)
        wait_for_stable_page(page)
        field = page.get_by_test_id(test_id)
        field.fill(value)
        field.blur()
        fill_value = field.input_value() if test_id != "editable" else field.inner_text()
        page.goto(base_url)
        wait_for_stable_page(page)
        field = page.get_by_test_id(test_id)
        per_character_hashes: list[str] = []
        per_character_bytes = 0
        field.fill("")
        field.focus()
        for character in value:
            field.press_sequentially(character, delay=0)
            frame = field.screenshot(animations="disabled")
            per_character_hashes.append(hashlib.sha256(frame).hexdigest())
            per_character_bytes += len(frame)
        field.blur()
        type_value = field.input_value() if test_id != "editable" else field.inner_text()
        facts = field.evaluate(
            """element => {
              const style = getComputedStyle(element);
              const tag = element.tagName.toLowerCase();
              const input = tag === 'input';
              const selection = input || tag === 'textarea';
              const selectionEnd = selection ? element.selectionEnd : null;
              let caret = null;
              if (input && selectionEnd !== null) {
                const canvas = document.createElement('canvas');
                const context = canvas.getContext('2d');
                context.font = style.font;
                const prefix = element.value.slice(0, selectionEnd);
                const x = parseFloat(style.paddingLeft) +
                  context.measureText(prefix).width - element.scrollLeft;
                caret = {
                  x,
                  top: parseFloat(style.paddingTop),
                  height: parseFloat(style.lineHeight),
                  within_clipping_rect: x >= 0 && x <= element.clientWidth,
                  complete: Number.isFinite(x) &&
                    Number.isFinite(parseFloat(style.lineHeight)),
                };
              }
              return {
                tag,
                input_type: input ? element.type : null,
                contenteditable: element.isContentEditable,
                multiline: tag === 'textarea' || element.isContentEditable,
                clipped: element.scrollWidth > element.clientWidth ||
                  element.scrollHeight > element.clientHeight,
                wrapping: style.whiteSpace,
                selection_start: selection ? element.selectionStart : null,
                selection_end: selectionEnd,
                caret,
                font: style.font,
                line_height: style.lineHeight,
                letter_spacing: style.letterSpacing,
                text_align: style.textAlign,
                overflow: style.overflow,
              };
            }"""
        )
        single_line = facts["tag"] == "input" and facts["input_type"] != "password"
        complete_style = all(
            facts[field_name] not in {None, ""}
            for field_name in (
                "font",
                "line_height",
                "letter_spacing",
                "text_align",
                "overflow",
            )
        )
        overlay_eligible = (
            test_id in {"project-name", "controlled"}
            and single_line
            and complete_style
            and not facts["clipped"]
            and fill_value == type_value
            and facts["selection_start"] == len(type_value)
            and facts["selection_end"] == len(type_value)
            and facts["caret"] is not None
            and facts["caret"]["complete"]
            and facts["caret"]["within_clipping_rect"]
        )
        cases[test_id] = {
            "fill_final": fill_value,
            "type_keys_final": type_value,
            "same_presentation_value": fill_value == type_value,
            "facts": facts,
            "per_character_frames": {
                "count": len(per_character_hashes),
                "unique_hashes": len(set(per_character_hashes)),
                "encoded_bytes": per_character_bytes,
            },
            "overlay": "input-overlay-v1" if overlay_eligible else "captured-state-or-clip",
            "fallback_reason": (
                None
                if overlay_eligible
                else "multiline-formatted-contenteditable-or-incomplete-fidelity"
            ),
        }
    context.close()
    return {
        "literal_overlay_ssim": score,
        "literal_overlay_candidate": score is not None and score >= 0.995,
        "style": style,
        "secret_presentation": "masked",
        "secret_overlay_text": "••••••••",
        "password_font_family": password_style,
        "capture_operation": "fill",
        "presentation_operation": "retimed-overlay",
        "cases": cases,
    }


def scroll_experiment(browser: Any, base_url: str, output: Path) -> dict[str, Any]:
    context = new_context(browser)
    page = context.new_page()
    page.goto(base_url)
    wait_for_stable_page(page)
    target = page.get_by_test_id("static-scroll")
    dynamic = page.get_by_test_id("dynamic-fragment")
    dynamic_safe = dynamic.evaluate(
        """element => [...element.querySelectorAll('*')].every(child =>
          getComputedStyle(child).animationName === 'none')"""
    )
    def classify(test_id: str) -> dict[str, Any]:
        locator = page.get_by_test_id(test_id)
        facts = locator.evaluate(
            """element => ({
              virtualized: element.dataset.virtualized === 'true',
              scrollLinked: element.dataset.scrollLinked === 'true',
              media: Boolean(element.querySelector('video, canvas')),
              stickyOrFixed: [...element.querySelectorAll('*')].some(child => {
                const position = getComputedStyle(child).position;
                return position === 'sticky' || position === 'fixed';
              }),
              animated: [...element.querySelectorAll('*')].some(child =>
                getComputedStyle(child).animationName !== 'none'),
            })"""
        )
        safe = not any(facts.values())
        return {"classification": "reconstruct" if safe else "clip", "facts": facts}

    cases = {
        "nested_static": classify("static-scroll"),
        "sticky": classify("sticky-scroll"),
        "fixed": classify("fixed-scroll"),
        "virtualized": classify("virtual-scroll"),
        "scroll_linked": classify("scroll-linked"),
        "dynamic_fragment": {
            "classification": "reconstruct" if dynamic_safe else "clip",
            "facts": {"animated": not dynamic_safe},
        },
        "document": {
            "classification": "clip",
            "facts": {"dynamic_descendants": True},
        },
    }
    scroll_dir = output / "scroll"
    scroll_dir.mkdir(parents=True, exist_ok=True)
    target.screenshot(path=str(scroll_dir / "before.png"), animations="disabled")
    target.evaluate("element => { element.scrollTop = 180; }")
    actual_after = scroll_dir / "actual-after.png"
    target.screenshot(path=str(actual_after), animations="disabled")

    def checkpoint_hashes(test_id: str) -> list[str]:
        locator = page.get_by_test_id(test_id)
        hashes: list[str] = []
        for position in (0, 60, 120):
            locator.evaluate("(element, value) => { element.scrollTop = value; }", position)
            frame = locator.screenshot(animations="allow")
            hashes.append(hashlib.sha256(frame).hexdigest())
        return hashes

    for test_id, name in (
        ("static-scroll", "nested_static"),
        ("sticky-scroll", "sticky"),
        ("fixed-scroll", "fixed"),
        ("virtual-scroll", "virtualized"),
        ("scroll-linked", "scroll_linked"),
    ):
        cases[name]["checkpoint_hashes"] = checkpoint_hashes(test_id)
    document_hashes: list[str] = []
    for position in (0, 120, 240):
        page.evaluate("value => window.scrollTo(0, value)", position)
        frame = page.screenshot(animations="allow")
        document_hashes.append(hashlib.sha256(frame).hexdigest())
    cases["document"]["checkpoint_hashes"] = document_hashes

    page.goto(base_url)
    wait_for_stable_page(page)
    replay_target = page.get_by_test_id("static-scroll")
    replay_target.evaluate("element => { element.scrollTop = 180; }")
    replay_after = scroll_dir / "replayed-after.png"
    replay_target.screenshot(path=str(replay_after), animations="disabled")
    replay_score = _ssim(actual_after, replay_after)
    cases["nested_static"]["replay_ssim"] = replay_score
    cases["nested_static"]["visual_replay_passed"] = (
        replay_score is not None and replay_score >= 0.999
    )
    context.close()
    return {
        "static_scroll": cases["nested_static"]["classification"],
        "dynamic_scroll": "reconstruct" if dynamic_safe else "clip",
        "static_reconstructable": bool(
            cases["nested_static"]["visual_replay_passed"]
        ),
        "dynamic_reconstructable": bool(dynamic_safe),
        "cases": cases,
    }


def dynamic_fragment_experiment(
    browser: Any, base_url: str, output: Path
) -> dict[str, Any]:
    video_dir = output / "dynamic" / "raw"
    context = new_context(browser, video_dir=video_dir)
    recording_started = time.monotonic()
    page = context.new_page()
    page.goto(base_url)
    wait_for_stable_page(page)
    public_page_text = page.locator("body").inner_text()
    sensitive_fixture_sentinels_absent = all(
        sentinel not in public_page_text
        for sentinel in ("token-fixture-value", "demo.user@example.test")
    )
    session = context.new_cdp_session(page)
    screencast_frames: list[dict[str, Any]] = []
    screencast_accepting = True

    def on_screencast_frame(params: dict[str, Any]) -> None:
        if not screencast_accepting:
            return
        data = base64.b64decode(params["data"])
        metadata = params.get("metadata") or {}
        screencast_frames.append(
            {
                "bytes": len(data),
                "data": data,
                "timestamp": metadata.get("timestamp"),
            }
        )
        try:
            session.send(
                "Page.screencastFrameAck", {"sessionId": params["sessionId"]}
            )
        except Exception:
            # A final frame may race with stop/detach. It is not evidence.
            pass

    session.on("Page.screencastFrame", on_screencast_frame)
    session.send(
        "Page.startScreencast",
        {"format": "jpeg", "quality": 80, "maxWidth": 1440, "maxHeight": 900},
    )
    video = page.video
    started = time.monotonic()
    action_offset_ms = round((started - recording_started) * 1000)
    page.wait_for_timeout(1400)
    elapsed_capture_ms = round((time.monotonic() - started) * 1000)
    screencast_accepting = False
    session.send("Page.stopScreencast")
    session.detach()
    screencast_dir = output / "dynamic" / "screencast"
    screencast_dir.mkdir(parents=True, exist_ok=True)
    if screencast_frames:
        (screencast_dir / "first.jpg").write_bytes(screencast_frames[0]["data"])
        (screencast_dir / "last.jpg").write_bytes(screencast_frames[-1]["data"])
    dynamic_cases = {
        "css_animation": page.locator("#dynamic-orb").is_visible(),
        "canvas": page.locator("#motion-canvas").is_visible(),
        "embedded_video": page.locator("#fixture-video").evaluate(
            "element => element.readyState >= 2 && !element.paused"
        ),
        "complex_scroll": page.locator("#complex-scroll").evaluate(
            "element => element.scrollTop > 0"
        ),
    }
    page.close()
    context.close()
    if video is None:
        return {"passed": False, "reason": "Playwright did not provide video"}
    source = Path(video.path())
    raw_destination = output / "dynamic" / "playwright-video-raw.webm"
    destination = output / "dynamic" / "playwright-video.webm"
    raw_destination.parent.mkdir(parents=True, exist_ok=True)
    if source != raw_destination:
        shutil.copy2(source, raw_destination)
    ffprobe = shutil.which("ffprobe")
    ffmpeg = shutil.which("ffmpeg")
    if ffprobe is None or ffmpeg is None:
        return {
            "passed": False,
            "reason": "ffmpeg or ffprobe is unavailable",
            "bytes": raw_destination.stat().st_size,
        }

    trim = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{action_offset_ms / 1000:.3f}",
            "-i",
            str(raw_destination),
            "-t",
            f"{elapsed_capture_ms / 1000:.3f}",
            "-an",
            "-c:v",
            "libvpx",
            "-deadline",
            "good",
            "-cpu-used",
            "4",
            "-crf",
            "10",
            "-b:v",
            "2M",
            str(destination),
        ],
        check=False,
    )
    if trim.returncode != 0 or not destination.is_file():
        return {"passed": False, "reason": "frame-accurate video trim failed"}

    def probe_video(path: Path) -> dict[str, Any]:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-count_frames",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name,width,height,duration,avg_frame_rate,nb_read_frames:format=duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(result.stdout)
        stream = data["streams"][0]
        duration = float(
            stream.get("duration") or data.get("format", {}).get("duration") or 0
        )
        rate_parts = str(stream.get("avg_frame_rate") or "0/1").split("/", 1)
        rate = (
            float(rate_parts[0]) / float(rate_parts[1])
            if len(rate_parts) == 2 and float(rate_parts[1])
            else 0
        )
        audio_result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=index",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return {
            "codec": stream.get("codec_name"),
            "width": stream.get("width"),
            "height": stream.get("height"),
            "duration_seconds": duration,
            "average_fps": rate,
            "decoded_frames": int(stream.get("nb_read_frames") or 0),
            "audio_streams": len(json.loads(audio_result.stdout).get("streams", [])),
            "bytes": path.stat().st_size,
        }

    raw_probe = probe_video(raw_destination)
    selected_probe = probe_video(destination)
    frame_probe = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "frame=best_effort_timestamp_time",
            "-of",
            "json",
            str(destination),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    timestamps = [
        float(frame["best_effort_timestamp_time"])
        for frame in json.loads(frame_probe.stdout).get("frames", [])
        if frame.get("best_effort_timestamp_time") is not None
    ]
    intervals_ms = [
        (right - left) * 1000 for left, right in zip(timestamps, timestamps[1:])
    ]
    sorted_intervals = sorted(intervals_ms)
    median_interval_ms = (
        sorted_intervals[len(sorted_intervals) // 2] if sorted_intervals else 0
    )
    large_frame_gaps = sum(
        interval > max(50, median_interval_ms * 1.75) for interval in intervals_ms
    )
    duration = float(selected_probe["duration_seconds"])
    seek_frame = output / "dynamic" / "seek-frame.png"
    seek_passed = False
    if duration > 0:
        result = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                str(duration / 2),
                "-i",
                str(destination),
                "-frames:v",
                "1",
                str(seek_frame),
            ],
            check=False,
        )
        seek_passed = result.returncode == 0 and seek_frame.is_file()
    screencast_span_ms = (
        round(
            (
                float(screencast_frames[-1]["timestamp"])
                - float(screencast_frames[0]["timestamp"])
            )
            * 1000
        )
        if len(screencast_frames) >= 2
        and screencast_frames[0]["timestamp"] is not None
        and screencast_frames[-1]["timestamp"] is not None
        else None
    )
    trim_error_ms = abs(duration * 1000 - elapsed_capture_ms)
    return {
        "passed": (
            seek_passed
            and selected_probe["codec"] == "vp8"
            and selected_probe["audio_streams"] == 0
            and trim_error_ms <= 120
            and large_frame_gaps == 0
            and sensitive_fixture_sentinels_absent
        ),
        "source": "playwright-video",
        "codec": selected_probe["codec"],
        "width": selected_probe["width"],
        "height": selected_probe["height"],
        "duration_seconds": duration,
        "bytes": selected_probe["bytes"],
        "midpoint_seek": seek_passed,
        "capture_elapsed_ms": elapsed_capture_ms,
        "action_offset_ms": action_offset_ms,
        "timing_map": "context-video-origin-plus-action-window-relative-ms",
        "sensitive_fixture_sentinels_absent": sensitive_fixture_sentinels_absent,
        "trim": {
            "method": "frame-accurate-keyframe-safe-ffmpeg-vp8-reencode",
            "crf": 10,
            "target_bitrate_bps": 2_000_000,
            "source": raw_probe,
            "selected": selected_probe,
            "target_duration_ms": elapsed_capture_ms,
            "duration_error_ms": round(trim_error_ms),
        },
        "frame_timing": {
            "decoded_frames": selected_probe["decoded_frames"],
            "median_interval_ms": round(median_interval_ms, 3),
            "max_interval_ms": round(max(intervals_ms), 3) if intervals_ms else None,
            "large_frame_gaps": large_frame_gaps,
            "playback_smoothness_passed": large_frame_gaps == 0,
        },
        "cases": dynamic_cases,
        "screencast": {
            "format": "jpeg-frames",
            "frame_count": len(screencast_frames),
            "bytes": sum(frame["bytes"] for frame in screencast_frames),
            "span_ms": screencast_span_ms,
            "capture_alignment_error_ms": (
                abs(screencast_span_ms - elapsed_capture_ms)
                if screencast_span_ms is not None
                else None
            ),
            "first_timestamp": (
                screencast_frames[0]["timestamp"] if screencast_frames else None
            ),
            "last_timestamp": (
                screencast_frames[-1]["timestamp"] if screencast_frames else None
            ),
        },
        "selected_policy": "playwright-video-v1",
    }


def redaction_experiment(browser: Any, base_url: str, output: Path) -> dict[str, Any]:
    context = new_context(browser)
    page = context.new_page()
    page.goto(base_url)
    moving_target = page.get_by_test_id("dynamic-secret")
    moving_target.evaluate(
        "(element, value) => { element.textContent = value; }",
        "token-fixture-value",
    )
    page.get_by_test_id("account-email").evaluate(
        "(element, value) => { element.textContent = value; }",
        "demo.user@example.test",
    )
    redaction_dir = output / "redaction"
    redaction_dir.mkdir(parents=True, exist_ok=True)
    dynamic_samples: list[dict[str, Any]] = []
    unmasked_last = redaction_dir / "moving-unmasked-last.png"
    for index in range(8):
        box = moving_target.bounding_box()
        masked_path = redaction_dir / f"moving-masked-{index:02d}.png"
        page.screenshot(
            path=str(masked_path),
            animations="allow",
            mask=[moving_target],
            mask_color="#000000",
        )
        dynamic_samples.append(
            {
                "box": box,
                "mask_passed": not _contains_rgb(masked_path, (181, 45, 83)),
            }
        )
        if index == 7:
            page.screenshot(path=str(unmasked_last), animations="allow")
        page.wait_for_timeout(60)
    moving_box_start = dynamic_samples[0]["box"]
    moving_box_end = dynamic_samples[-1]["box"]
    moving_target_changed = moving_box_start != moving_box_end
    moving_target_resized = (
        moving_box_start is not None
        and moving_box_end is not None
        and (
            round(moving_box_start["width"]) != round(moving_box_end["width"])
            or round(moving_box_start["height"]) != round(moving_box_end["height"])
        )
    )

    postprocessed = redaction_dir / "moving-static-postprocess.png"
    postprocess_static_mask_passed = False
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is not None and moving_box_start is not None and moving_box_end is not None:
        box_filter = "drawbox=x={}:y={}:w={}:h={}:color=black:t=fill".format(
            round(moving_box_start["x"]),
            round(moving_box_start["y"]),
            round(moving_box_start["width"]),
            round(moving_box_start["height"]),
        )
        result = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(unmasked_last),
                "-vf",
                box_filter,
                "-frames:v",
                "1",
                str(postprocessed),
            ],
            check=False,
        )
        postprocess_static_mask_passed = result.returncode == 0 and _masked_region_is_black(
            postprocessed, moving_box_end
        )
    page.get_by_role("button", name="Create sensitive content").click()
    delayed = page.get_by_test_id("delayed-secret")
    dynamic_delayed_box = delayed.bounding_box()
    dynamic_delayed_path = redaction_dir / "delayed-dynamic-redacted.png"
    delayed.screenshot(
        path=str(dynamic_delayed_path),
        animations="allow",
        mask=[delayed],
        mask_color="#000000",
    )
    dynamic_delayed_mask_passed = _masked_region_is_black(
        dynamic_delayed_path,
        (
            {
                "x": 0,
                "y": 0,
                "width": dynamic_delayed_box["width"],
                "height": dynamic_delayed_box["height"],
            }
            if dynamic_delayed_box is not None
            else None
        ),
    )
    page.add_style_tag(content=STABLE_CSS)
    wait_for_stable_page(page)
    target = page.get_by_test_id("account-email")
    target.scroll_into_view_if_needed()
    box = target.bounding_box()
    path = output / "redaction" / "static-redacted.png"
    page.screenshot(
        path=str(path),
        animations="disabled",
        mask=[target],
        mask_color="#000000",
    )
    static_mask_passed = _masked_region_is_black(path, box)
    delayed_box = delayed.bounding_box()
    delayed_path = output / "redaction" / "delayed-redacted.png"
    delayed.screenshot(
        path=str(delayed_path),
        animations="disabled",
        mask=[delayed],
        mask_color="#000000",
    )
    delayed_mask_passed = _masked_region_is_black(
        delayed_path,
        (
            {
                "x": 0,
                "y": 0,
                "width": delayed_box["width"],
                "height": delayed_box["height"],
            }
            if delayed_box is not None
            else None
        ),
    )
    moving_target.evaluate("element => element.remove()")
    disappeared_target_detected = moving_target.count() == 0
    context.close()
    return {
        "static_mask_passed": static_mask_passed,
        "created_after_action_mask_passed": delayed_mask_passed,
        "dynamic_created_after_action_sample_mask_passed": (
            dynamic_delayed_mask_passed
        ),
        "moving_target_exercised": moving_target_changed,
        "resizing_target_exercised": moving_target_resized,
        "sampled_per_frame_masks_passed": all(
            sample["mask_passed"] for sample in dynamic_samples
        ),
        "sampled_dynamic_frames": len(dynamic_samples),
        "dynamic_samples": dynamic_samples,
        "static_postprocess_tracks_movement": postprocess_static_mask_passed,
        "disappeared_target_detected": disappeared_target_detected,
        "capture_time_overlay_candidate": "stable-states-only",
        "per_frame_mask_candidate": "sampled-only-not-continuous-coverage",
        "post_processing_candidate": "static-mask-fails-moving-target",
        "dynamic_redaction_supported": False,
        "dynamic_required_redaction_result": "BROWSER_REDACTION_UNSAFE",
        "fail_closed": True,
    }


def composition_experiment(browser: Any, base_url: str, output: Path) -> dict[str, Any]:
    from omegaflow.recording_plan import normalize_recording_plan

    plan = normalize_recording_plan(
        {
            "id": "phase0-mixed",
            "browser": {"base_url": base_url},
            "presentation": {
                "browser": {
                    "window": {
                        "mode": "framed",
                        "opening_transition": "window-open",
                    },
                    "chrome": {"mode": "full"},
                }
            },
            "beats": [
                {
                    "id": "terminal-start",
                    "narration_take": "mixed-intro",
                    "narration": "Start the local service,",
                    "actions": [{"run": "true"}],
                },
                {
                    "id": "browser-create",
                    "medium": "browser",
                    "narration_take": "mixed-intro",
                    "narration": "then @project_ready@ create the project. "
                    "@wait:ready+200ms@",
                    "actions": [
                        {
                            "id": "open",
                            "open_page": {
                                "url": "/?loading=show",
                                "display_url": "https://app.example.test/projects",
                                "loading": "show",
                            },
                        },
                        {
                            "id": "ready",
                            "wait_for": {"visible": {"text": "Create a project"}},
                            "after": "@project_ready@",
                        },
                    ],
                },
                {
                    "id": "terminal-verify",
                    "narration": "Finally, verify the result.",
                    "actions": [{"run": "true"}],
                },
            ],
        }
    )
    demo_dir = output / "composition"
    demo_dir.mkdir(parents=True, exist_ok=True)
    def player_variant(
        *,
        name: str,
        width: int,
        height: int,
        query: str = "",
        emulate_touch: bool = False,
    ) -> dict[str, Any]:
        context = browser.new_context(
            viewport={"width": width, "height": height},
            screen={"width": width, "height": height},
            device_scale_factor=1,
            locale="en-US",
            timezone_id="UTC",
            color_scheme="light",
            reduced_motion="reduce",
            has_touch=emulate_touch,
            is_mobile=emulate_touch,
        )
        page = context.new_page()
        suffix = f"&{query}" if query else ""
        page.goto(f"{base_url}prototype-player.html?scene=browser{suffix}")
        wait_for_stable_page(page)
        page.screenshot(path=str(demo_dir / f"{name}.png"))
        result = {
            "viewport": page.locator("#viewport").bounding_box(),
            "layout_bounds": {"width": width, "height": height},
            "capture_surface": page.locator("#viewport iframe").evaluate(
                "element => ({width: element.offsetWidth, height: element.offsetHeight})"
            ),
            "controls_visible": page.locator("#controls").is_visible(),
            "control_boxes": page.locator("#controls button").evaluate_all(
                "elements => elements.map(element => { "
                "const box = element.getBoundingClientRect(); "
                "return {width: box.width, height: box.height}; })"
            ),
            "chrome_visible": page.locator("#chrome").is_visible(),
            "titlebar_visible": page.locator("#titlebar").is_visible(),
            "animation_name": page.locator("#window").evaluate(
                "element => getComputedStyle(element).animationName"
            ),
        }
        context.close()
        return result

    desktop = player_variant(
        name="framed-desktop", width=1440, height=900
    )
    hidden_chrome = player_variant(
        name="hidden-chrome", width=1440, height=900, query="chrome=hidden"
    )
    windowless = player_variant(
        name="windowless", width=1440, height=900, query="window=none"
    )
    fade = player_variant(
        name="fade-transition", width=1440, height=900, query="transition=fade"
    )
    portrait = player_variant(
        name="framed-mobile", width=390, height=844, emulate_touch=True
    )
    landscape = player_variant(
        name="framed-landscape", width=844, height=390, emulate_touch=True
    )

    loading = new_context(browser)
    loading_page = loading.new_page()
    loading_page.goto(f"{base_url}?loading=show", wait_until="domcontentloaded")
    loading_visible = loading_page.locator("#loading").is_visible()
    loading_page.screenshot(path=str(demo_dir / "loading-shown.png"))
    loading_page.locator("#loading").wait_for(state="hidden")
    loading_hidden_after_ready = not loading_page.locator("#loading").is_visible()
    loading_page.screenshot(path=str(demo_dir / "loading-retained-ready.png"))
    loading.close()

    hidden_loading = new_context(browser)
    hidden_loading_page = hidden_loading.new_page()
    hidden_loading_page.goto(
        f"{base_url}?loading=show", wait_until="domcontentloaded"
    )
    hidden_loading_page.locator("#loading").wait_for(state="hidden")
    hide_first_public_state = not hidden_loading_page.locator("#loading").is_visible()
    hidden_loading_page.screenshot(path=str(demo_dir / "loading-hidden-first.png"))
    hidden_loading.close()

    terminal = subprocess.Popen(
        ["/bin/sh"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    def terminal_action(command: str, marker: str) -> str:
        if terminal.stdin is None or terminal.stdout is None:
            raise RuntimeError("persistent terminal pipes are unavailable")
        terminal.stdin.write(f"{command}\nprintf '%s\\n' {shlex.quote(marker)}\n")
        terminal.stdin.flush()
        lines: list[str] = []
        while True:
            line = terminal.stdout.readline()
            if line == "":
                raise RuntimeError("persistent terminal exited before marker")
            if line.rstrip("\n") == marker:
                return "".join(lines)
            lines.append(line)

    persistent_browser = new_context(browser)
    persistent_page = persistent_browser.new_page()
    terminal_cleaned = False
    browser_cleaned = False
    failure_observed = False
    shared_state_consumed = False
    browser_session_persisted = False
    terminal_session_persisted = False
    terminal_start_output = ""
    terminal_verify_output = ""
    try:
        post_script = (
            "import urllib.request; "
            f"urllib.request.urlopen({(base_url + 'api/project')!r}, data=b'{{}}').read()"
        )
        terminal_start_output = terminal_action(
            "export PHASE0_SHARED=created; "
            f"{shlex.quote(sys.executable)} -c {shlex.quote(post_script)}; "
            "printf 'terminal-created:%s\\n' \"$PHASE0_SHARED\"",
            "__PHASE0_TERMINAL_START__",
        )
        persistent_page.goto(base_url)
        project = persistent_page.evaluate(
            "async () => (await (await fetch('/api/project')).json())"
        )
        shared_state_consumed = bool(project.get("created"))
        persistent_page.evaluate("window.phase0Session = 'persistent-browser'")
        terminal_verify_output = terminal_action(
            "printf 'terminal-verified:%s\\n' \"$PHASE0_SHARED\"",
            "__PHASE0_TERMINAL_VERIFY__",
        )
        terminal_session_persisted = "terminal-verified:created" in terminal_verify_output
        browser_session_persisted = (
            persistent_page.evaluate("window.phase0Session") == "persistent-browser"
        )
        persistent_page.evaluate("() => { throw new Error('phase0 failure'); }")
    except Exception:
        failure_observed = True
    finally:
        persistent_browser.close()
        browser_cleaned = True
        if terminal.stdin is not None:
            terminal.stdin.close()
        terminal.terminate()
        terminal.wait(timeout=5)
        terminal_cleaned = True

    beat_timing = [
        {"id": "terminal-start", "offset_ms": 0, "duration_ms": 900},
        {"id": "browser-create", "offset_ms": 900, "duration_ms": 1300},
        {"id": "terminal-verify", "offset_ms": 2200, "duration_ms": 800},
    ]

    timeline_context = new_context(browser)
    timeline_page = timeline_context.new_page()
    timeline_page.goto(f"{base_url}prototype-player.html?scene=browser")
    wait_for_stable_page(timeline_page)
    seek_results = {
        str(value): timeline_page.evaluate(
            "value => window.phase0RenderAt(value)", value
        )
        for value in (0, 899, 900, 2199, 2200, 3000)
    }
    timeline_page.evaluate("value => window.phase0RenderAt(value)", 0)
    timeline_page.screenshot(path=str(demo_dir / "timeline-terminal-start.png"))
    timeline_page.evaluate("value => window.phase0RenderAt(value)", 900)
    timeline_page.screenshot(path=str(demo_dir / "timeline-browser.png"))
    timeline_page.evaluate("value => window.phase0RenderAt(value)", 2200)
    timeline_page.screenshot(path=str(demo_dir / "timeline-terminal-end.png"))
    timeline_context.close()
    seek_matrix = {
        value: str(result["id"]) for value, result in seek_results.items()
    }
    renderer_switch_passed = [
        seek_results[str(value)]["renderer"] for value in (0, 900, 2200)
    ] == ["terminal", "browser", "terminal"]

    private_dir = demo_dir / "private"
    public_dir = demo_dir / "public"
    private_dir.mkdir(exist_ok=True)
    public_dir.mkdir(exist_ok=True)
    private_sentinels = [
        "http://127.0.0.1/private-capture",
        "token-fixture-value",
        "data-testid=account-email",
    ]
    (private_dir / "capture.log.json").write_text(
        json.dumps({"private": private_sentinels}), encoding="utf-8"
    )
    public_manifest = {
        "manifest_version": 1,
        "recording": {"id": "phase0-mixed", "duration_ms": 3000},
        "beats": [
            {
                **beat,
                "renderer": "browser" if beat["id"] == "browser-create" else "terminal",
            }
            for beat in beat_timing
        ],
        "display_url": "https://app.example.test/projects",
    }
    (public_dir / "recording.presentation.json").write_text(
        json.dumps(public_manifest), encoding="utf-8"
    )
    public_bytes = b"".join(
        path.read_bytes() for path in public_dir.rglob("*") if path.is_file()
    )
    isolation_passed = all(
        sentinel.encode("utf-8") not in public_bytes for sentinel in private_sentinels
    )
    expected_aspect = 1440 / 900
    viewport_boxes = [desktop["viewport"], portrait["viewport"], landscape["viewport"]]
    uniform_scale_passed = all(
        box is not None
        and abs((box["width"] / box["height"]) - expected_aspect) < 0.01
        and box["x"] >= 0
        and box["y"] >= 0
        and box["x"] + box["width"] <= variant["layout_bounds"]["width"] + 0.5
        and box["y"] + box["height"] <= variant["layout_bounds"]["height"] + 0.5
        for box, variant in zip(
            viewport_boxes,
            (desktop, portrait, landscape),
        )
    )
    fixed_capture_surface_passed = all(
        variant["capture_surface"] == {"width": 1440, "height": 900}
        for variant in (desktop, portrait, landscape)
    )
    browser_beat = plan.beats[1]
    browser_action_ids = [action.id for action in browser_beat.actions]
    narration_anchor_ids = [anchor.id for anchor in browser_beat.anchors]
    narration_waits = [
        {"target": wait.target, "gap_ms": wait.gap_ms}
        for wait in browser_beat.waits
    ]
    after_binding_passed = (
        browser_action_ids == ["open", "ready"]
        and narration_anchor_ids == ["project_ready"]
        and narration_waits == [{"target": "ready", "gap_ms": 200}]
        and browser_beat.actions[1].config.get("after") == "@project_ready@"
    )
    return {
        "beat_order": [beat.id for beat in plan.beats],
        "modalities": [beat.medium.value for beat in plan.beats],
        "narration_takes": [
            {"id": take.id, "members": [member.beat_id for member in take.members]}
            for take in plan.narration_takes
        ],
        "browser_action_ids": browser_action_ids,
        "narration_anchor_ids": narration_anchor_ids,
        "narration_waits": narration_waits,
        "after_binding_passed": after_binding_passed,
        "loading_show_visible": loading_visible,
        "loading_hide_after_ready": loading_hidden_after_ready,
        "loading_hide_first_public_state": hide_first_public_state,
        "window": "framed",
        "browser_chrome": "full",
        "entry_transition": "window-open",
        "fade_transition": fade["animation_name"] == "fade-in",
        "hidden_chrome": not hidden_chrome["chrome_visible"],
        "windowless": not windowless["titlebar_visible"],
        "windowless_viewport_passed": (
            windowless["viewport"] is not None
            and abs(
                windowless["viewport"]["width"]
                / windowless["viewport"]["height"]
                - expected_aspect
            )
            < 0.01
            and windowless["viewport"]["height"] > 600
        ),
        "desktop_viewport_box": desktop["viewport"],
        "mobile_viewport_box": portrait["viewport"],
        "landscape_viewport_box": landscape["viewport"],
        "uniform_scale_passed": uniform_scale_passed and fixed_capture_surface_passed,
        "fixed_capture_surface_passed": fixed_capture_surface_passed,
        "mobile_controls_visible": portrait["controls_visible"]
        and landscape["controls_visible"],
        "touch_targets_passed": all(
            box["width"] >= 44 and box["height"] >= 44
            for box in [*portrait["control_boxes"], *landscape["control_boxes"]]
        ),
        "responsive_only_not_real_device": True,
        "beat_timing": beat_timing,
        "seek_matrix": seek_matrix,
        "seek_results": seek_results,
        "renderer_switch_passed": renderer_switch_passed,
        "boundary_seek_passed": seek_matrix
        == {
            "0": "terminal-start",
            "899": "terminal-start",
            "900": "browser-create",
            "2199": "browser-create",
            "2200": "terminal-verify",
            "3000": "terminal-verify",
        },
        "shared_state_consumed": shared_state_consumed,
        "terminal_session_persisted": terminal_session_persisted,
        "browser_session_persisted": browser_session_persisted,
        "terminal_start_output": terminal_start_output.strip(),
        "terminal_verify_output": terminal_verify_output.strip(),
        "failure_observed": failure_observed,
        "terminal_cleanup_attempted": terminal_cleaned,
        "browser_cleanup_attempted": browser_cleaned,
        "cleanup_attempted": terminal_cleaned and browser_cleaned,
        "private_artifact_isolation": isolation_passed,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / ".artifacts" / "browser-phase0",
    )
    parser.add_argument("--runs", type=int, default=10)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright

    from omegaflow.browser_runtime import pinned_browser_runtime

    runtime = pinned_browser_runtime()
    FixtureHandler.video_path = create_fixture_video(args.output)
    with fixture_server() as base_url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--mute-audio"])
        try:
            report = {
                "runtime": {
                    "playwright": runtime.playwright_version,
                    "chromium_revision": runtime.chromium_revision,
                    "chromium_version": runtime.chromium_version,
                },
                "profile": {
                    "name": "desktop-v1",
                    "viewport": {"width": 1440, "height": 900},
                    "device_scale_factor": 1,
                    "locale": "en-US",
                    "timezone": "UTC",
                    "color_scheme": "light",
                    "reduced_motion": "reduce",
                    "page_audio": "muted",
                },
                "stable_state": stable_state_experiment(
                    browser, base_url, args.output, args.runs
                ),
                "text_entry": text_entry_experiment(browser, base_url, args.output),
                "scroll": scroll_experiment(browser, base_url, args.output),
                "dynamic_fragment": dynamic_fragment_experiment(
                    browser, base_url, args.output
                ),
                "redaction": redaction_experiment(browser, base_url, args.output),
                "composition": composition_experiment(
                    browser, base_url, args.output
                ),
            }
        finally:
            browser.close()
    report_path = args.output / "report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
