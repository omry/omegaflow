from __future__ import annotations

import hashlib
import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from omegaflow.browser_capture import (
    BrowserCaptureError,
    PersistentBrowserRunner,
    resolve_browser_authentication,
)
from omegaflow.capture import CaptureContext
from omegaflow.recording_plan import normalize_recording_plan


FIXTURE_HTML = b"""<!doctype html>
<html><body>
  <main>Browser fixture</main>
  <p id="loading" hidden>Loading</p>
  <button id="open">Open dialog</button>
  <button id="noop">Noop</button>
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
        runner.start(context)
        try:
            capture = runner.capture_beat(plan.beats[0])
            metadata = capture.metadata
            actions = metadata["actions"]
            old_seq = actions[0]["completion"]["response_seq"]
            created_seq = actions[2]["completion"]["response_seq"]
            assert created_seq > old_seq
            assert runner.page.locator("body").get_attribute("data-shortcut") == "yes"
            assert runner.page.get_by_test_id("scrollbox").evaluate(
                "element => element.scrollTop"
            ) > 0
            assert all(check["passed"] for check in metadata["checks"])
            assert runner.secrets.values == {"private-password"}
            assert "private-password" not in json.dumps(dict(metadata))
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
        capture_text = json.dumps(capture_records)
        assert "private-password" not in capture_text
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
    assert len(list(state_path.parent.glob("*.png"))) == 1


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


def test_retained_loading_is_trimmed_to_muted_content_addressed_webm(
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
    assert path.name == f"{hashlib.sha256(content).hexdigest()}.webm"
    assert fragment["sha256"] == hashlib.sha256(content).hexdigest()
    assert fragment["media_type"] == "video/webm"
    assert fragment["codec"] == "vp8"
    assert fragment["has_audio"] is False
    assert (fragment["width"], fragment["height"]) == (1440, 900)
    assert 0 < fragment["duration_ms"] <= 3_000
    assert fragment["encoded_bytes"] == len(content) <= 2_000_000
    assert fragment["source_start_ms"] == (
        action["visual"]["request"]["source_start_ms"]
    )
    assert fragment["source_end_ms"] == (
        action["visual"]["request"]["source_end_ms"]
    )


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
