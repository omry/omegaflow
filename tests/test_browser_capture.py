from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import omegaflow.browser_visuals as browser_visuals
from omegaflow.browser_capture import (
    BrowserCaptureError,
    PersistentBrowserRunner,
    resolve_browser_authentication,
)
from omegaflow.browser_visuals import (
    BrowserVisualCapture,
    BrowserVisualError,
    DynamicFragmentRequest,
)
from omegaflow.capture import CaptureContext
from omegaflow.presentation_compiler import (
    compile_browser_beat,
    load_browser_capture_log,
)
from omegaflow.recording_plan import normalize_recording_plan


FIXTURE_HTML = b"""<!doctype html>
<html><body>
  <main>Browser fixture</main>
  <p id="loading" hidden>Loading</p>
  <button id="open">Open dialog</button>
  <button id="noop">Noop</button>
  <button id="delayed-completion">Start delayed completion</button>
  <p id="delayed-status">Ready</p>
  <div role="dialog" aria-label="Create project" hidden>
    <label>Project name <input id="project"></label>
    <label>Password <input id="password" type="password"></label>
  </div>
  <input placeholder="Search">
  <p data-testid="status">Idle</p>
  <div class="item">One</div><div class="item">Two</div>
  <div data-testid="scrollbox" style="height:80px;overflow:auto">
    <div style="height:300px"></div><span>Bottom</span>
  </div>
  <script>
    fetch('/api/create?phase=old', {method: 'POST'});
    document.querySelector('#open').addEventListener('click', () => {
      fetch('/api/create?phase=new', {method: 'POST'}).then(() => {
        document.querySelector('[role=dialog]').hidden = false;
        document.querySelector('[data-testid=status]').textContent = 'Created';
      });
    });
    document.querySelector('#delayed-completion').addEventListener('click', () => {
      setTimeout(() => {
        document.querySelector('#delayed-status').textContent = 'Complete';
      }, 3600);
    });
    addEventListener('keydown', (event) => {
      if (event.ctrlKey && event.key.toLowerCase() === 'k') {
        document.body.dataset.shortcut = 'yes';
      }
    });
  </script>
</body></html>"""

ASSET_HTML = b"""<!doctype html>
<html><head>
  <style>
    @font-face { font-family: DelayedFixture; src: url('/delayed.woff2'); }
    body { font-family: DelayedFixture, sans-serif; }
  </style>
</head><body>
  <main>Delayed assets</main>
  <img id="asset" src="/delayed.png" width="24" height="24">
  <script>document.fonts.ready.then(() => document.body.dataset.fontsReady = 'yes')</script>
</body></html>"""

BROKEN_ASSET_HTML = b"""<!doctype html>
<html><body><img src="/missing.png" width="24" height="24"></body></html>"""

ANIMATED_CAPTURE_HTML = b"""<!doctype html>
<html><head><style>
html, body { margin: 0; width: 100%; height: 100%; background: rgb(255, 0, 0); }
</style></head><body>
  <button id="animate">Animate</button>
  <script>
    document.querySelector('#animate').addEventListener('click', () => {
      document.body.style.background = 'rgb(0, 255, 0)';
      let value = '';
      const target = 'for n in 1 2 3 4 5';
      const timer = setInterval(() => {
        value += target[value.length];
        document.querySelector('#animate').textContent = value;
        if (value.length === target.length) clearInterval(timer);
      }, 20);
    });
  </script>
</body></html>"""

PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010804000000b51c0c02"
    "0000000b4944415478da6364f80f00010501012718e3660000000049454e44ae426082"
)


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(FIXTURE_HTML)))
            self.end_headers()
            self.wfile.write(FIXTURE_HTML)
            return
        if self.path == "/assets":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(ASSET_HTML)))
            self.end_headers()
            self.wfile.write(ASSET_HTML)
            return
        if self.path == "/broken-assets":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(BROKEN_ASSET_HTML)))
            self.end_headers()
            self.wfile.write(BROKEN_ASSET_HTML)
            return
        if self.path == "/animated-capture":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(ANIMATED_CAPTURE_HTML)))
            self.end_headers()
            self.wfile.write(ANIMATED_CAPTURE_HTML)
            return
        if self.path == "/delayed.png":
            time.sleep(0.15)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(PNG_1X1)))
            self.end_headers()
            self.wfile.write(PNG_1X1)
            return
        if self.path == "/delayed.woff2":
            time.sleep(0.15)
            self.send_response(200)
            self.send_header("Content-Type", "font/woff2")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        if self.path.startswith("/api/create"):
            payload = b"{}"
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_error(404)

    def log_message(self, _format: str, *args: object) -> None:
        return


@contextmanager
def fixture_site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_open_page_consumes_recorder_owned_handoff_url_once() -> None:
    calls: list[tuple[str, str, int]] = []

    class FakePage:
        def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
            calls.append((url, wait_until, timeout))

    runner = PersistentBrowserRunner({})
    runner.page = FakePage()
    runner.set_handoff_url(
        "watch_command",
        "http://127.0.0.1:43123/cast-player.html?manifest=demo",
    )

    completion = runner._open_page(  # noqa: SLF001
        {"handoff": "watch_command"},
        beat_id="browser",
        action_id="open",
    )

    assert calls == [
        (
            "http://127.0.0.1:43123/cast-player.html?manifest=demo",
            "domcontentloaded",
            15_000,
        )
    ]
    assert completion["url"] == calls[0][0]
    with pytest.raises(BrowserCaptureError, match="no captured URL"):
        runner._open_page(  # noqa: SLF001
            {"handoff": "watch_command"},
            beat_id="browser",
            action_id="open-again",
        )


def test_visible_wait_allows_target_to_match_later(tmp_path: Path) -> None:
    runner = PersistentBrowserRunner(
        {"timeouts": {"action_ms": 100, "readiness_ms": 1500}}
    )
    runner.start(capture_context(tmp_path))
    try:
        runner.page.set_content(
            "<button aria-label='Pause'>Pause</button>"
            "<script>setTimeout(() => {"
            "document.querySelector('button').setAttribute('aria-label', 'Play again');"
            "}, 750)</script>"
        )

        completion = runner._wait_condition(  # noqa: SLF001
            {
                "visible": {
                    "role": "button",
                    "name": "Play again",
                    "exact": True,
                },
                "timeout_ms": 1500,
            },
            response_start=0,
            beat_id="browser",
            action_id="wait-for-play-again",
        )

        assert completion == {"kind": "visible"}
    finally:
        runner.close()


def test_initial_capture_is_not_constrained_by_action_timeout(tmp_path: Path) -> None:
    runner = PersistentBrowserRunner(
        {"timeouts": {"action_ms": 1, "readiness_ms": 1500}}
    )

    runner.start(capture_context(tmp_path))
    try:
        assert runner.initial_visual_state is not None
    finally:
        runner.close()


def browser_plan(config: dict | None = None):
    return normalize_recording_plan(
        {
            "id": "browser-runtime",
            "browser": config or {},
            "beats": [
                {
                    "id": "open",
                    "medium": "browser",
                    "actions": [
                        {
                            "id": "open",
                            "open_page": {"url": "about:blank"},
                        }
                    ],
                }
            ],
        }
    )


def capture_context(tmp_path: Path, environment: dict[str, str] | None = None):
    return CaptureContext.create(
        tmp_path / "run",
        workspace=tmp_path,
        environment=environment,
    )


def capture_records(tmp_path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (tmp_path / "run" / "capture" / "browser.capture.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]


def test_resolves_auth_path_from_environment_without_retaining_path(
    tmp_path: Path,
) -> None:
    state = {
        "cookies": [
            {
                "name": "session",
                "value": "private-value",
                "domain": "example.test",
                "path": "/",
                "expires": -1,
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            }
        ],
        "origins": [],
    }
    content = json.dumps(state).encode("utf-8")
    path = tmp_path / "auth.json"
    path.write_bytes(content)
    context = capture_context(tmp_path, {"BROWSER_STATE": "auth.json"})

    resolved = resolve_browser_authentication(
        {"auth": {"storage_state_env": "BROWSER_STATE"}}, context
    )

    assert resolved.storage_state == state
    assert resolved.content_sha256 == hashlib.sha256(content).hexdigest()
    assert not hasattr(resolved, "path")


def test_missing_auth_environment_variable_fails_before_browser_launch(
    tmp_path: Path,
) -> None:
    context = capture_context(tmp_path)

    with pytest.raises(BrowserCaptureError, match="is not set") as caught:
        resolve_browser_authentication(
            {"auth": {"storage_state_env": "NOT_CONFIGURED"}}, context
        )

    assert caught.value.code == "BROWSER_SCHEMA"


def test_persistent_browser_materializes_desktop_profile_and_auth(
    tmp_path: Path,
) -> None:
    state = {
        "cookies": [
            {
                "name": "session",
                "value": "private-value",
                "domain": "example.test",
                "path": "/",
                "expires": -1,
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            }
        ],
        "origins": [],
    }
    content = json.dumps(state).encode("utf-8")
    (tmp_path / "auth.json").write_bytes(content)
    plan = browser_plan(
        {
            "auth": {"storage_state_path": "auth.json"},
            "viewport": {
                "width": 1200,
                "height": 750,
                "device_scale_factor": 1.25,
            },
            "context": {
                "locale": "en-GB",
                "timezone": "Europe/London",
                "color_scheme": "dark",
                "reduced_motion": "reduce",
                "permissions": [],
            },
        }
    )
    context = capture_context(tmp_path)
    runner = PersistentBrowserRunner(plan.browser)

    runner.start(context)
    try:
        original_page = runner.page
        runner.start(context)
        assert runner.page is original_page
        assert runner.profile is not None
        assert runner.profile.viewport_width == 1200
        assert runner.profile.viewport_height == 750
        assert runner.profile.screen_width == 1200
        assert runner.profile.device_scale_factor == 1.25
        assert runner.profile.locale == "en-GB"
        assert runner.profile.timezone == "Europe/London"
        assert runner.profile.color_scheme == "dark"
        assert runner.profile.permissions == ()
        assert runner.profile.audio_muted
        assert not runner.profile.is_mobile and not runner.profile.has_touch
        assert runner.profile.auth_state_sha256 == hashlib.sha256(content).hexdigest()
        assert runner.page.evaluate("window.innerWidth") == 1200
        assert runner.page.evaluate("window.innerHeight") == 750
        assert runner.page.evaluate("window.devicePixelRatio") == 1.25
        assert runner.page.evaluate("navigator.language") == "en-GB"
        assert runner.page.evaluate(
            "matchMedia('(prefers-color-scheme: dark)').matches"
        )
        assert runner.page.evaluate(
            "matchMedia('(prefers-reduced-motion: reduce)').matches"
        )
        cookies = runner.browser_context.cookies("https://example.test")
        assert cookies[0]["value"] == "private-value"
        state = runner.capture_beat(plan.beats[0]).metadata["actions"][0][
            "visual"
        ]["state"]
        assert state["width"] == 1500
        assert state["height"] == 938
    finally:
        runner.close()

    assert runner.page is None
    assert runner.browser_context is None
    assert runner.browser is None
    assert runner.playwright is None
    assert list((tmp_path / "run" / "capture" / "fragments").glob("*.webm"))


def test_executes_browser_actions_checks_and_response_scopes(tmp_path: Path) -> None:
    with fixture_site() as base_url:
        plan = normalize_recording_plan(
            {
                "id": "browser-actions",
                "browser": {"base_url": base_url},
                "beats": [
                    {
                        "id": "create",
                        "medium": "browser",
                        "actions": [
                            {
                                "id": "open",
                                "open_page": {
                                    "url": "/",
                                    "ready": {
                                        "response": {
                                            "contains": "/api/create",
                                            "method": "POST",
                                            "status": 201,
                                        }
                                    },
                                },
                            },
                            {
                                "id": "move-viewport",
                                "move_pointer": {
                                    "viewport": {"x": 0.4, "y": 0.12}
                                },
                            },
                            {
                                "id": "move-target",
                                "move_pointer": {
                                    "target": {
                                        "role": "button",
                                        "name": "Open dialog",
                                    }
                                },
                            },
                            {
                                "id": "open-dialog",
                                "click": {
                                    "target": {
                                        "role": "button",
                                        "name": "Open dialog",
                                    }
                                },
                            },
                            {
                                "id": "created-response",
                                "wait_for": {
                                    "response": {
                                        "contains": "/api/create",
                                        "method": "POST",
                                        "status": 201,
                                    }
                                },
                            },
                            {
                                "id": "dialog-ready",
                                "wait_for": {
                                    "visible": {
                                        "role": "dialog",
                                        "name": "Create project",
                                    }
                                },
                            },
                            {
                                "id": "name",
                                "fill": {
                                    "target": {"label": "Project name"},
                                    "text": "Demo",
                                },
                            },
                            {
                                "id": "search",
                                "type_keys": {
                                    "target": {"placeholder": "Search"},
                                    "text": "query",
                                    "capture_delay_ms": 0,
                                },
                            },
                            {
                                "id": "shortcut",
                                "press": {
                                    "key": "Control+K",
                                    "target": {"placeholder": "Search"},
                                },
                            },
                            {
                                "id": "scroll",
                                "scroll": {
                                    "by": {"x": 0, "y": 100},
                                    "container": {"test_id": "scrollbox"},
                                },
                            },
                            {
                                "id": "scroll-bottom",
                                "scroll": {"target": {"text": "Bottom"}},
                            },
                            {
                                "id": "password",
                                "fill": {
                                    "target": {"label": "Password"},
                                    "secret": {
                                        "env": "DEMO_PASSWORD",
                                        "presentation": "masked",
                                    },
                                },
                            },
                            {
                                "id": "fragile",
                                "click": {"target": {"css": "#noop"}},
                            },
                        ],
                        "checks": [
                            {"name": "url", "url": {"equals": "/"}},
                            {
                                "name": "dialog",
                                "visible": {
                                    "role": "dialog",
                                    "name": "Create project",
                                },
                            },
                            {"name": "loading", "hidden": {"text": "Loading"}},
                            {
                                "name": "status",
                                "text": {
                                    "target": {"test_id": "status"},
                                    "equals": "Created",
                                },
                            },
                            {
                                "name": "name",
                                "value": {
                                    "target": {"label": "Project name"},
                                    "equals": "Demo",
                                },
                            },
                            {
                                "name": "items",
                                "count": {"target": {"css": ".item"}, "equals": 2},
                            },
                            {
                                "name": "response",
                                "response": {
                                    "contains": "/api/create",
                                    "method": "POST",
                                    "status": 201,
                                },
                            },
                        ],
                    }
                ],
            }
        )
        context = capture_context(tmp_path, {"DEMO_PASSWORD": "private-password"})
        runner = PersistentBrowserRunner(plan.browser)
        progress: list[tuple[str, str]] = []
        runner.start(context)
        try:
            capture = runner.capture_beat(
                plan.beats[0],
                on_progress=lambda state, action_id: progress.append(
                    (state, action_id)
                ),
            )
            metadata = capture.metadata
            actions = metadata["actions"]
            actions_by_id = {action["action_id"]: action for action in actions}
            old_seq = actions_by_id["open"]["completion"]["response_seq"]
            created_seq = actions_by_id["created-response"]["completion"]["response_seq"]
            assert created_seq > old_seq
            assert actions_by_id["move-viewport"]["target"]["point"] == {
                "x": pytest.approx(runner.profile.viewport_width * 0.4),
                "y": pytest.approx(runner.profile.viewport_height * 0.12),
            }
            move_target = actions_by_id["move-target"]["target"]
            assert move_target["point"] == {
                "x": pytest.approx(
                    move_target["bounds"]["x"]
                    + move_target["bounds"]["width"] / 2
                ),
                "y": pytest.approx(
                    move_target["bounds"]["y"]
                    + move_target["bounds"]["height"] / 2
                ),
            }
            assert runner.page.locator("body").get_attribute("data-shortcut") == "yes"
            assert progress == [
                event
                for action in plan.beats[0].actions
                for event in (("started", action.id), ("completed", action.id))
            ]
            assert runner.page.get_by_test_id("scrollbox").evaluate(
                "element => element.scrollTop"
            ) > 0
            assert all(check["passed"] for check in metadata["checks"])
            assert runner.secrets.values == {"private-password"}
            assert "private-password" not in json.dumps(dict(metadata))
            assert metadata["runner_initial_state"]["media_type"] == "image/png"
            assert actions_by_id["name"]["target"]["text_overlay"]["eligible"]
            assert actions_by_id["password"]["target"]["text_overlay"]["eligible"]
            assert actions_by_id["scroll"]["target"]["scroll"]["eligible"]
            assert actions_by_id["scroll-bottom"]["visual"]["kind"] == "clip"
            runner.page.evaluate(
                "value => console.log('secret=' + value)", "private-password"
            )
            runner.page.evaluate(
                "value => setTimeout(() => { throw new Error(value); }, 0)",
                str(tmp_path),
            )
            runner.page.wait_for_timeout(50)
            assert any(
                warning.code == "FRAGILE_BROWSER_SELECTOR"
                and warning.action_id == "fragile"
                for warning in runner.warnings
            )
        finally:
            runner.close()

        runner.complete()
        capture_records = [
            json.loads(line)
            for line in (tmp_path / "run" / "capture" / "browser.capture.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert capture_records[0]["type"] == "run_start"
        assert capture_records[-1] == {
            "capture_version": 1,
            "seq": capture_records[-1]["seq"],
            "type": "run_end",
            "status": "completed",
        }
        assert [record["seq"] for record in capture_records] == list(
            range(1, len(capture_records) + 1)
        )
        private_capture = load_browser_capture_log(
            tmp_path / "run" / "capture" / "browser.capture.jsonl"
        )
        compiled = compile_browser_beat(
            plan.id,
            plan.beats[0],
            action_captures=private_capture.actions_by_beat["create"],
            viewport=private_capture.viewport,
            initial_state=private_capture.initial_state,
            clip_assets=private_capture.clip_assets,
        )
        compiled_kinds = {event["kind"] for event in compiled.payload["events"]}
        assert {"text", "scroll", "clip"} <= compiled_kinds
        assert "private-password" not in json.dumps(dict(compiled.payload))
        capture_text = json.dumps(capture_records)
        assert "private-password" not in capture_text
        assert any(
            record["type"] == "diagnostic"
            and record.get("kind") == "dynamic_fragment"
            and record.get("action_id") == "scroll-bottom"
            for record in capture_records
        )
        console_text = (tmp_path / "run" / "diagnostics" / "console.jsonl").read_text(
            encoding="utf-8"
        )
        page_error_text = (
            tmp_path / "run" / "diagnostics" / "page-errors.jsonl"
        ).read_text(encoding="utf-8")
        network_text = (tmp_path / "run" / "diagnostics" / "network.jsonl").read_text(
            encoding="utf-8"
        )
        assert "private-password" not in console_text
        assert "[REDACTED]" in console_text
        assert str(tmp_path) not in page_error_text
        assert "[PRIVATE_PATH]" in page_error_text
        assert "/api/create" not in network_text


def test_response_checks_are_scoped_to_the_current_beat(tmp_path: Path) -> None:
    with fixture_site() as base_url:
        plan = normalize_recording_plan(
            {
                "id": "response-scope",
                "browser": {"base_url": base_url},
                "beats": [
                    {
                        "id": "load",
                        "medium": "browser",
                        "actions": [
                            {"id": "open", "open_page": {"url": "/"}}
                        ],
                    },
                    {
                        "id": "later",
                        "medium": "browser",
                        "actions": [],
                        "checks": [
                            {
                                "name": "no stale response",
                                "response": {
                                    "contains": "/api/create",
                                    "method": "POST",
                                    "status": 201,
                                },
                            }
                        ],
                    },
                ],
            }
        )
        runner = PersistentBrowserRunner(plan.browser)
        runner.start(capture_context(tmp_path))
        try:
            runner.capture_beat(plan.beats[0])
            with pytest.raises(BrowserCaptureError) as caught:
                runner.capture_beat(plan.beats[1])
            assert caught.value.code == "BROWSER_CHECK_FAILED"
        finally:
            runner.close()


def test_waits_for_fonts_and_visible_images_before_capturing_state(
    tmp_path: Path,
) -> None:
    with fixture_site() as base_url:
        plan = normalize_recording_plan(
            {
                "id": "render-assets",
                "browser": {"base_url": base_url},
                "beats": [
                    {
                        "id": "assets",
                        "medium": "browser",
                        "actions": [
                            {"id": "open", "open_page": {"url": "/assets"}}
                        ],
                    }
                ],
            }
        )
        runner = PersistentBrowserRunner(plan.browser)
        runner.start(capture_context(tmp_path))
        try:
            action = runner.capture_beat(plan.beats[0]).metadata["actions"][0]
            assert runner.page.locator("#asset").evaluate(
                "image => image.complete && image.naturalWidth > 0"
            )
            assert runner.page.evaluate("document.fonts.status") == "loaded"
            assert runner.page.locator("body").get_attribute("data-fonts-ready") == "yes"
            assert action["visual"]["kind"] == "state"
        finally:
            runner.close()


def test_broken_visible_image_times_out_before_state_capture(tmp_path: Path) -> None:
    with fixture_site() as base_url:
        plan = normalize_recording_plan(
            {
                "id": "broken-render-asset",
                "browser": {
                    "base_url": base_url,
                    "timeouts": {"action_ms": 500, "readiness_ms": 300},
                },
                "beats": [
                    {
                        "id": "broken",
                        "medium": "browser",
                        "actions": [
                            {
                                "id": "open",
                                "open_page": {"url": "/broken-assets"},
                            }
                        ],
                    }
                ],
            }
        )
        runner = PersistentBrowserRunner(plan.browser)
        runner.start(capture_context(tmp_path))
        try:
            with pytest.raises(BrowserCaptureError) as caught:
                runner.capture_beat(plan.beats[0])
            assert caught.value.code == "BROWSER_READINESS_TIMEOUT"
        finally:
            runner.close()


def test_missing_response_wait_uses_readiness_timeout(tmp_path: Path) -> None:
    plan = normalize_recording_plan(
        {
            "id": "response-timeout",
            "browser": {"timeouts": {"action_ms": 500, "readiness_ms": 300}},
            "beats": [
                {
                    "id": "timeout",
                    "medium": "browser",
                    "actions": [
                        {"id": "open", "open_page": {"url": "about:blank"}},
                        {
                            "id": "missing-response",
                            "wait_for": {
                                "response": {"contains": "/never-arrives"}
                            },
                        },
                    ],
                }
            ],
        }
    )
    runner = PersistentBrowserRunner(plan.browser)
    runner.start(capture_context(tmp_path))
    try:
        with pytest.raises(BrowserCaptureError) as caught:
            runner.capture_beat(plan.beats[0])
        assert caught.value.code == "BROWSER_READINESS_TIMEOUT"
    finally:
        runner.close()


def test_external_origin_warning_and_network_diagnostics_hide_paths(
    tmp_path: Path,
) -> None:
    plan = normalize_recording_plan(
        {
            "id": "external-warning",
            "browser": {},
            "beats": [
                {
                    "id": "external",
                    "medium": "browser",
                    "actions": [
                        {
                            "id": "open",
                            "open_page": {
                                "url": "http://external.test/private/path?token=value"
                            },
                        }
                    ],
                }
            ],
        }
    )
    runner = PersistentBrowserRunner(plan.browser)
    runner.start(capture_context(tmp_path))
    runner.browser_context.route(
        "http://external.test/**",
        lambda route: route.fulfill(
            status=200,
            content_type="text/html",
            body="<main>External fixture</main>",
        ),
    )
    try:
        runner.capture_beat(plan.beats[0])
    finally:
        runner.close()
    runner.complete()

    assert [warning.code for warning in runner.warnings] == [
        "EXTERNAL_NETWORK_CAPTURE"
    ]
    network = (tmp_path / "run" / "diagnostics" / "network.jsonl").read_text(
        encoding="utf-8"
    )
    assert "http://external.test" in network
    assert "private/path" not in network
    assert "token=value" not in network


def test_stable_states_are_content_addressed_and_deduplicated(
    tmp_path: Path,
) -> None:
    with fixture_site() as base_url:
        plan = normalize_recording_plan(
            {
                "id": "stable-states",
                "browser": {"base_url": base_url},
                "beats": [
                    {
                        "id": "stable",
                        "medium": "browser",
                        "actions": [
                            {"id": "open", "open_page": {"url": "/"}},
                            {
                                "id": "wait-one",
                                "wait_for": {"hidden": {"text": "Loading"}},
                            },
                            {
                                "id": "wait-two",
                                "wait_for": {"hidden": {"text": "Loading"}},
                            },
                        ],
                    },
                    {
                        "id": "stable-again",
                        "medium": "browser",
                        "actions": [
                            {
                                "id": "wait-three",
                                "wait_for": {"hidden": {"text": "Loading"}},
                            }
                        ],
                    },
                ],
            }
        )
        runner = PersistentBrowserRunner(plan.browser)
        runner.start(capture_context(tmp_path))
        try:
            actions = runner.capture_beat(plan.beats[0]).metadata["actions"]
            later_actions = runner.capture_beat(plan.beats[1]).metadata["actions"]
        finally:
            runner.close()

    assert actions[0]["execution"]["start_ms"] <= 50
    assert later_actions[0]["execution"]["start_ms"] <= 50
    states = [
        action["visual"]["state"] for action in (*actions, *later_actions)
    ]
    assert {state["path"] for state in states} == {states[0]["path"]}
    state_path = tmp_path / "run" / states[0]["path"]
    content = state_path.read_bytes()
    assert states[0]["sha256"] == hashlib.sha256(content).hexdigest()
    assert state_path.name == f"{states[0]['sha256']}.png"
    assert states[0]["media_type"] == "image/png"
    assert len(list(state_path.parent.glob("*.png"))) == 2


def test_recording_wide_redaction_is_applied_to_stable_states(
    tmp_path: Path,
) -> None:
    with fixture_site() as base_url:
        plan = normalize_recording_plan(
            {
                "id": "static-redaction",
                "browser": {
                    "base_url": base_url,
                    "redactions": [{"target": {"css": "main"}}],
                },
                "beats": [
                    {
                        "id": "redacted",
                        "medium": "browser",
                        "actions": [
                            {"id": "open", "open_page": {"url": "/"}},
                            {
                                "id": "wait",
                                "wait_for": {"hidden": {"text": "Loading"}},
                            },
                        ],
                    }
                ],
            }
        )
        runner = PersistentBrowserRunner(plan.browser)
        runner.start(capture_context(tmp_path))
        try:
            actions = runner.capture_beat(plan.beats[0]).metadata["actions"]
            unmasked = runner.page.screenshot(type="png")
        finally:
            runner.close()

    state = actions[0]["visual"]["state"]
    masked = (tmp_path / "run" / state["path"]).read_bytes()
    assert hashlib.sha256(masked).hexdigest() == state["sha256"]
    assert hashlib.sha256(unmasked).hexdigest() != state["sha256"]
    assert actions[1]["visual"]["state"]["path"] == state["path"]


def test_retained_loading_is_trimmed_to_muted_content_addressed_mp4(
    tmp_path: Path,
) -> None:
    plan = normalize_recording_plan(
        {
            "id": "dynamic-fragment",
            "browser": {},
            "beats": [
                {
                    "id": "dynamic",
                    "medium": "browser",
                    "actions": [
                        {
                            "id": "open",
                            "open_page": {
                                "url": "about:blank",
                                "loading": "show",
                            },
                        }
                    ],
                }
            ],
        }
    )
    runner = PersistentBrowserRunner(plan.browser)
    runner.start(capture_context(tmp_path))
    try:
        action = runner.capture_beat(plan.beats[0]).metadata["actions"][0]
    finally:
        runner.close()
    runner.complete()

    assert action["visual"]["kind"] == "clip"
    records = capture_records(tmp_path)
    fragment = next(
        record
        for record in records
        if record["type"] == "diagnostic"
        and record.get("kind") == "dynamic_fragment"
    )
    path = tmp_path / "run" / fragment["path"]
    content = path.read_bytes()
    assert path.name == f"{hashlib.sha256(content).hexdigest()}.mp4"
    assert fragment["sha256"] == hashlib.sha256(content).hexdigest()
    assert fragment["media_type"] == "video/mp4"
    assert fragment["codec"] == "h264"
    assert fragment["has_audio"] is False
    moov = content.find(b"moov")
    mdat = content.find(b"mdat")
    assert 0 <= moov < mdat
    assert (fragment["width"], fragment["height"]) == (1440, 900)
    assert 0 < fragment["duration_ms"] <= 3_000
    assert fragment["encoded_bytes"] == len(content) <= 2_000_000
    assert fragment["source_start_ms"] == (
        action["visual"]["request"]["source_start_ms"]
    )
    assert fragment["source_end_ms"] >= (
        action["visual"]["request"]["source_end_ms"]
    )


def test_dynamic_fragment_retains_the_frame_before_animation_starts(
    tmp_path: Path,
) -> None:
    with fixture_site() as base_url:
        plan = normalize_recording_plan(
            {
                "id": "dynamic-fragment-start",
                "browser": {"base_url": base_url},
                "beats": [
                    {
                        "id": "dynamic",
                        "medium": "browser",
                        "actions": [
                            {"id": "open", "open_page": {"url": "/animated-capture"}},
                            {
                                "id": "animate",
                                "transition": "captured",
                                "click": {
                                    "target": {"role": "button", "name": "Animate"}
                                },
                            },
                        ],
                    }
                ],
            }
        )
        runner = PersistentBrowserRunner(plan.browser)
        runner.start(capture_context(tmp_path))
        try:
            actions = runner.capture_beat(plan.beats[0]).metadata["actions"]
        finally:
            runner.close()
        runner.complete()

    fragment = next(
        record
        for record in capture_records(tmp_path)
        if record["type"] == "diagnostic"
        and record.get("kind") == "dynamic_fragment"
        and record.get("action_id") == "animate"
    )
    source = tmp_path / "run" / fragment["path"]
    media = browser_visuals.require_browser_media_runtime(require_h264=True)
    first_pixel = subprocess.run(
        [
            media.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-vf",
            "scale=1:1",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    ).stdout

    assert actions[1]["visual"]["kind"] == "clip"
    assert len(first_pixel) == 3
    red, green, _blue = first_pixel
    assert red > 200
    assert green < 80


def test_explicit_captured_wait_can_follow_its_condition_past_implicit_limit(
    tmp_path: Path,
) -> None:
    with fixture_site() as base_url:
        plan = normalize_recording_plan(
            {
                "id": "condition-bounded-fragment",
                "browser": {"base_url": base_url},
                "beats": [
                    {
                        "id": "dynamic",
                        "medium": "browser",
                        "actions": [
                            {"id": "open", "open_page": {"url": "/"}},
                            {
                                "id": "start",
                                "click": {
                                    "target": {
                                        "role": "button",
                                        "name": "Start delayed completion",
                                    }
                                },
                            },
                            {
                                "id": "await-completion",
                                "transition": "captured",
                                "wait_for": {
                                    "visible": {"text": "Complete", "exact": True},
                                    "timeout_ms": 5000,
                                },
                            },
                        ],
                    }
                ],
            }
        )
        runner = PersistentBrowserRunner(plan.browser)
        runner.start(capture_context(tmp_path))
        try:
            actions = runner.capture_beat(plan.beats[0]).metadata["actions"]
        finally:
            runner.close()
        runner.complete()

    wait = actions[2]
    assert wait["completion"] == {"kind": "visible"}
    assert wait["visual"]["kind"] == "clip"
    fragment = next(
        record
        for record in capture_records(tmp_path)
        if record["type"] == "diagnostic"
        and record.get("kind") == "dynamic_fragment"
        and record.get("action_id") == "await-completion"
    )
    request = wait["visual"]["request"]
    authored_duration_ms = (
        request["source_end_ms"] - request["source_start_ms"]
    )
    assert 3000 < authored_duration_ms <= 5000
    assert fragment["duration_ms"] >= authored_duration_ms
    assert fragment["encoded_bytes"] <= 2_000_000


def test_implicit_dynamic_fragment_keeps_short_duration_limit(tmp_path: Path) -> None:
    class StaticPage:
        def screenshot(self, **_kwargs: object) -> bytes:
            return PNG_1X1

        def wait_for_timeout(self, _duration_ms: int) -> None:
            return

    visuals = BrowserVisualCapture(
        StaticPage(),
        run_dir=tmp_path,
        states_dir=tmp_path / "states",
        fragments_dir=tmp_path / "fragments",
        diagnostics_dir=tmp_path / "diagnostics",
        redaction_targets=(),
        locator_factory=lambda _target: None,
    )

    with pytest.raises(BrowserVisualError, match="dynamic window exceeds 3000 ms"):
        visuals.observe(
            beat_id="dynamic",
            action_id="implicit",
            video_start_ms=0,
            video_end_ms=lambda: 3001,
            force_dynamic=True,
        )


def test_dynamic_fragment_boundary_includes_the_final_screenshot_capture(
    tmp_path: Path,
) -> None:
    class AdvancingPage:
        def __init__(self) -> None:
            self.elapsed_ms = 0
            self.screenshot_calls = 0
            self.waits: list[int] = []

        def screenshot(self, **_kwargs: object) -> bytes:
            self.screenshot_calls += 1
            content = PNG_1X1 + f"frame-{self.screenshot_calls}".encode()
            self.elapsed_ms += 20
            return content

        def wait_for_timeout(self, duration_ms: int) -> None:
            self.waits.append(duration_ms)
            self.elapsed_ms += duration_ms

    page = AdvancingPage()
    visuals = BrowserVisualCapture(
        page,
        run_dir=tmp_path,
        states_dir=tmp_path / "states",
        fragments_dir=tmp_path / "fragments",
        diagnostics_dir=tmp_path / "diagnostics",
        redaction_targets=(),
        locator_factory=lambda _target: None,
    )

    visual = visuals.observe(
        beat_id="dynamic",
        action_id="minimum-window",
        video_start_ms=0,
        video_end_ms=lambda: page.elapsed_ms,
        force_dynamic=True,
    )

    assert page.screenshot_calls == 2
    assert page.waits == [280]
    assert visual["request"]["source_end_ms"] == page.elapsed_ms
    assert visual["end_state"]["sha256"] == hashlib.sha256(
        PNG_1X1 + b"frame-2"
    ).hexdigest()


def write_color_transition_video(
    tmp_path: Path, *, frame_rate: int = 25
) -> tuple[str, Path, Path]:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg is unavailable")
    source = tmp_path / "source.webm"
    end_state = tmp_path / "end.png"
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
            f"color=c=black:s=160x90:d=2:r={frame_rate}",
            "-f",
            "lavfi",
            "-i",
            f"color=c=white:s=160x90:d=2:r={frame_rate}",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map",
            "[v]",
            "-c:v",
            "libvpx",
            str(source),
        ],
        check=True,
    )
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
            "color=c=white:s=160x90",
            "-frames:v",
            "1",
            str(end_state),
        ],
        check=True,
    )
    return ffmpeg, source, end_state


def write_repeated_end_state_video(tmp_path: Path) -> tuple[str, Path, Path]:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg is unavailable")
    source = tmp_path / "repeated-end-state.webm"
    end_state = tmp_path / "repeated-end-state.png"
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
            "color=c=black:s=160x90:d=2:r=25",
            "-f",
            "lavfi",
            "-i",
            "color=c=white:s=160x90:d=0.04:r=25",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=160x90:d=0.4:r=25",
            "-f",
            "lavfi",
            "-i",
            "color=c=#fdfdfd:s=160x90:d=0.4:r=25",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=160x90:d=0.4:r=25",
            "-f",
            "lavfi",
            "-i",
            "color=c=white:s=160x90:d=0.4:r=25",
            "-filter_complex",
            "[0:v][1:v][2:v][3:v][4:v][5:v]concat=n=6:v=1:a=0[v]",
            "-map",
            "[v]",
            "-c:v",
            "libvpx",
            "-lossless",
            "1",
            str(source),
        ],
        check=True,
    )
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
            "color=c=white:s=160x90",
            "-frames:v",
            "1",
            str(end_state),
        ],
        check=True,
    )
    return ffmpeg, source, end_state


def write_lagged_small_change_video(
    tmp_path: Path,
) -> tuple[str, Path, Path, Path]:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg is unavailable")
    source = tmp_path / "lagged-small-change.webm"
    start_state = tmp_path / "lagged-small-change-start.png"
    end_state = tmp_path / "lagged-small-change.png"
    base = "color=c=black:s=192x60:r=25"
    distinct_start = "color=c=#404040:s=192x60:r=25"
    first_box = "drawbox=x=20:y=20:w=4:h=4:color=white:t=fill"
    second_box = "drawbox=x=30:y=20:w=4:h=4:color=white:t=fill"
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
            f"{base}:d=0.88",
            "-f",
            "lavfi",
            "-i",
            f"{distinct_start}:d=0.12",
            "-f",
            "lavfi",
            "-i",
            f"{base}:d=0.04,{first_box}",
            "-f",
            "lavfi",
            "-i",
            f"{base}:d=0.4,{first_box},{second_box}",
            "-filter_complex",
            "[0:v][1:v][2:v][3:v]concat=n=4:v=1:a=0[v]",
            "-map",
            "[v]",
            "-c:v",
            "libvpx",
            "-lossless",
            "1",
            str(source),
        ],
        check=True,
    )
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
            distinct_start,
            "-frames:v",
            "1",
            str(start_state),
        ],
        check=True,
    )
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
            f"{base},{first_box}",
            "-frames:v",
            "1",
            str(end_state),
        ],
        check=True,
    )
    return ffmpeg, source, start_state, end_state


def test_dynamic_fragment_uses_first_stable_end_state_match(tmp_path: Path) -> None:
    ffmpeg, source, end_state = write_repeated_end_state_video(tmp_path)

    matched_end_ms = browser_visuals._matching_end_frame_ms(
        ffmpeg,
        source,
        end_state,
        minimum_ms=1500,
    )

    # Three consecutive matches confirm that the end state is stable, but the
    # retained clip must end after the first matching frame. Keeping the two
    # confirmation frames can expose newer page content before the renderer
    # cuts back to the captured end-state screenshot.
    assert 2440 <= matched_end_ms <= 2500


def test_dynamic_fragment_finds_single_exact_state_before_lagged_video_clock(
    tmp_path: Path,
) -> None:
    ffmpeg, source, _start_state, end_state = write_lagged_small_change_video(
        tmp_path
    )

    matched_end_ms = browser_visuals._matching_end_frame_ms(
        ffmpeg,
        source,
        end_state,
        minimum_ms=1120,
        lookback_ms=120,
    )

    assert 1000 <= matched_end_ms <= 1080


def test_dynamic_fragment_aligns_states_when_video_lags_authored_interval(
    tmp_path: Path,
) -> None:
    _ffmpeg, source, start_state, end_state = write_lagged_small_change_video(
        tmp_path
    )
    visuals = BrowserVisualCapture(
        object(),
        run_dir=tmp_path,
        states_dir=tmp_path / "states",
        fragments_dir=tmp_path / "fragments",
        diagnostics_dir=tmp_path / "diagnostics",
        redaction_targets=(),
        locator_factory=lambda _target: None,
    )
    visuals.dynamic_requests.append(
        DynamicFragmentRequest(
            beat_id="dynamic",
            action_id="lagged-small-change",
            source_start_ms=120,
            source_end_ms=1120,
            start_state_path=start_state,
            end_state_path=end_state,
            explicit_dynamic=True,
        )
    )

    (asset,) = visuals.finalize_dynamic_fragments(source)

    assert 880 <= asset.source_start_ms <= 960
    assert 1000 <= asset.source_end_ms <= 1080


def test_adjacent_dynamic_fragments_preserve_continuous_source_video(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ffmpeg, source, end_state = write_color_transition_video(tmp_path)
    visuals = BrowserVisualCapture(
        object(),
        run_dir=tmp_path,
        states_dir=tmp_path / "states",
        fragments_dir=tmp_path / "fragments",
        diagnostics_dir=tmp_path / "diagnostics",
        redaction_targets=(),
        locator_factory=lambda _target: None,
    )
    visuals.dynamic_requests.extend(
        [
            DynamicFragmentRequest(
                beat_id="dynamic",
                action_id="click",
                source_start_ms=0,
                source_end_ms=1000,
                end_state_path=end_state,
                explicit_dynamic=True,
            ),
            DynamicFragmentRequest(
                beat_id="dynamic",
                action_id="wait",
                source_start_ms=1002,
                source_end_ms=1800,
                end_state_path=end_state,
                explicit_dynamic=True,
            ),
            DynamicFragmentRequest(
                beat_id="dynamic",
                action_id="later",
                source_start_ms=2000,
                source_end_ms=2400,
                end_state_path=end_state,
                explicit_dynamic=True,
            ),
        ]
    )
    aligned_ends = iter((800, 1800, 2400))
    monkeypatch.setattr(
        browser_visuals,
        "_matching_end_frame_ms",
        lambda *_args, **_kwargs: next(aligned_ends),
    )

    first, second, later = visuals.finalize_dynamic_fragments(source)

    assert first.source_end_ms == 800
    assert second.source_start_ms == first.source_end_ms
    assert second.duration_ms == 1000
    assert later.source_start_ms == 2000


def test_dynamic_fragment_alignment_error_identifies_beat_and_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ffmpeg, source, _start_state, end_state = write_lagged_small_change_video(
        tmp_path
    )
    visuals = BrowserVisualCapture(
        object(),
        run_dir=tmp_path,
        states_dir=tmp_path / "states",
        fragments_dir=tmp_path / "fragments",
        diagnostics_dir=tmp_path / "diagnostics",
        redaction_targets=(),
        locator_factory=lambda _target: None,
    )
    visuals.dynamic_requests.append(
        DynamicFragmentRequest(
            beat_id="browser-demo",
            action_id="play",
            source_start_ms=0,
            source_end_ms=100,
            end_state_path=end_state,
        )
    )
    monkeypatch.setattr(
        browser_visuals,
        "_matching_end_frame_ms",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            BrowserVisualError(
                "BROWSER_UNSUPPORTED_MOTION",
                "could not align a dynamic fragment with its completed browser frame",
            )
        ),
    )

    with pytest.raises(
        BrowserVisualError,
        match="beat 'browser-demo', action 'play'.*could not align",
    ):
        visuals.finalize_dynamic_fragments(source)


def test_dynamic_fragment_finalization_extends_to_the_matching_end_state(
    tmp_path: Path,
) -> None:
    _ffmpeg, source, end_state = write_color_transition_video(
        tmp_path, frame_rate=50
    )
    visuals = BrowserVisualCapture(
        object(),
        run_dir=tmp_path,
        states_dir=tmp_path / "states",
        fragments_dir=tmp_path / "fragments",
        diagnostics_dir=tmp_path / "diagnostics",
        redaction_targets=(),
        locator_factory=lambda _target: None,
    )
    visuals.dynamic_requests.append(
        DynamicFragmentRequest(
            beat_id="dynamic",
            action_id="lagged-end",
            source_start_ms=0,
            source_end_ms=1500,
            end_state_path=end_state,
            explicit_dynamic=True,
        )
    )

    (asset,) = visuals.finalize_dynamic_fragments(source)

    assert 2000 <= asset.source_end_ms <= 2100
    assert 2000 <= asset.duration_ms <= 2100


def test_implicit_dynamic_fragment_rechecks_limit_after_frame_alignment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ffmpeg, source, end_state = write_color_transition_video(tmp_path)
    monkeypatch.setattr(browser_visuals, "IMPLICIT_DYNAMIC_MAX_DURATION_MS", 1800)
    visuals = BrowserVisualCapture(
        object(),
        run_dir=tmp_path,
        states_dir=tmp_path / "states",
        fragments_dir=tmp_path / "fragments",
        diagnostics_dir=tmp_path / "diagnostics",
        redaction_targets=(),
        locator_factory=lambda _target: None,
    )
    visuals.dynamic_requests.append(
        DynamicFragmentRequest(
            beat_id="dynamic",
            action_id="implicit-lagged-end",
            source_start_ms=0,
            source_end_ms=1500,
            end_state_path=end_state,
        )
    )

    with pytest.raises(BrowserVisualError, match="dynamic window exceeds 1800 ms"):
        visuals.finalize_dynamic_fragments(source)


def test_dynamic_fragment_with_required_redaction_fails_closed(
    tmp_path: Path,
) -> None:
    with fixture_site() as base_url:
        plan = normalize_recording_plan(
            {
                "id": "dynamic-redaction",
                "browser": {
                    "base_url": base_url,
                    "redactions": [{"target": {"css": "main"}}],
                },
                "beats": [
                    {
                        "id": "unsafe",
                        "medium": "browser",
                        "actions": [
                            {
                                "id": "open",
                                "open_page": {"url": "/"},
                                "transition": "captured",
                            }
                        ],
                    }
                ],
            }
        )
        runner = PersistentBrowserRunner(plan.browser)
        runner.start(capture_context(tmp_path))
        try:
            with pytest.raises(BrowserCaptureError) as caught:
                runner.capture_beat(plan.beats[0])
            assert caught.value.code == "BROWSER_REDACTION_UNSAFE"
        finally:
            runner.close()

    assert list((tmp_path / "run" / "diagnostics" / "stability").glob("*.png"))
    assert not any(
        record["type"] == "diagnostic"
        and record.get("kind") == "dynamic_fragment"
        for record in capture_records(tmp_path)
    )


def test_secret_input_automatically_prohibits_captured_motion(
    tmp_path: Path,
) -> None:
    with fixture_site() as base_url:
        plan = normalize_recording_plan(
            {
                "id": "secret-dynamic-redaction",
                "browser": {"base_url": base_url},
                "beats": [
                    {
                        "id": "secret",
                        "medium": "browser",
                        "actions": [
                            {"id": "open", "open_page": {"url": "/"}},
                            {
                                "id": "open-dialog",
                                "click": {
                                    "target": {
                                        "role": "button",
                                        "name": "Open dialog",
                                    }
                                },
                            },
                            {
                                "id": "password",
                                "fill": {
                                    "target": {"label": "Password"},
                                    "secret": {
                                        "env": "DEMO_PASSWORD",
                                        "presentation": "masked",
                                    },
                                },
                            },
                            {
                                "id": "later-motion",
                                "click": {"target": {"css": "#noop"}},
                                "transition": "captured",
                            },
                        ],
                    }
                ],
            }
        )
        runner = PersistentBrowserRunner(plan.browser)
        runner.start(
            capture_context(tmp_path, {"DEMO_PASSWORD": "private-password"})
        )
        try:
            with pytest.raises(BrowserCaptureError) as caught:
                runner.capture_beat(plan.beats[0])
            assert caught.value.code == "BROWSER_REDACTION_UNSAFE"
        finally:
            runner.close()

    private_bytes = b"".join(
        path.read_bytes()
        for path in (tmp_path / "run").rglob("*")
        if path.is_file()
    )
    assert b"private-password" not in private_bytes


def test_failed_browser_capture_has_no_successful_run_end(tmp_path: Path) -> None:
    plan = normalize_recording_plan(
        {
            "id": "failed-browser",
            "browser": {"timeouts": {"action_ms": 300, "readiness_ms": 1000}},
            "beats": [
                {
                    "id": "fail",
                    "medium": "browser",
                    "actions": [
                        {"id": "open", "open_page": {"url": "about:blank"}},
                        {
                            "id": "missing",
                            "click": {"target": {"role": "button", "name": "Missing"}},
                        },
                    ],
                }
            ],
        }
    )
    runner = PersistentBrowserRunner(plan.browser)
    runner.start(capture_context(tmp_path))
    try:
        with pytest.raises(BrowserCaptureError) as caught:
            runner.capture_beat(plan.beats[0])
        assert caught.value.code == "BROWSER_TARGET_COUNT"
        assert str(caught.value) == (
            "BROWSER_TARGET_COUNT: browser beat 'fail', action 'missing': "
            "expected exactly one element matching role='button', name='Missing'; "
            "found 0"
        )
    finally:
        runner.close()

    with pytest.raises(BrowserCaptureError, match="cannot be marked complete"):
        runner.complete()

    records = [
        json.loads(line)
        for line in (tmp_path / "run" / "capture" / "browser.capture.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert records[-1]["type"] != "run_end"


def test_private_browser_artifact_symlink_fails_before_launch(tmp_path: Path) -> None:
    context = capture_context(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (context.paths.capture / "fragments").symlink_to(outside, target_is_directory=True)
    runner = PersistentBrowserRunner(browser_plan().browser)

    with pytest.raises(BrowserCaptureError, match="is a symlink") as caught:
        runner.start(context)

    assert caught.value.code == "BROWSER_SCHEMA"
    assert list(outside.iterdir()) == []
