from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_published_player_matches_packaged_player() -> None:
    packaged = REPO_ROOT / "src/omegaflow/player/static/cast-player.html"
    published = REPO_ROOT / "website/static/cast-player.html"

    assert published.read_bytes() == packaged.read_bytes()


def test_player_uses_night_studio_brand_without_replacing_ansi_colors() -> None:
    html = (
        REPO_ROOT / "src/omegaflow/player/static/cast-player.html"
    ).read_text(encoding="utf-8")

    assert "--brand: #8b7cff" in html
    assert "--cue: #ffc247" in html
    assert "--ansi-white: #f8f8f2" in html
    assert 'class="player-brand"' not in html
    assert "linear-gradient(90deg, var(--brand), var(--cue))" in html
    assert "#play {" in html
    assert "background: var(--cue);" in html
    assert ".ansi-green { color: var(--green); }" in html
    assert ".ansi-cyan { color: var(--cyan); }" in html
    assert ".ansi-white { color: var(--ansi-white); }" in html


def test_player_links_logo_in_a_separate_top_bar_column() -> None:
    html = (
        REPO_ROOT / "src/omegaflow/player/static/cast-player.html"
    ).read_text(encoding="utf-8")

    assert "grid-template-columns: 1.75rem minmax(0, 1fr);" in html
    assert '<div class="bar">\n        <a class="player-logo-link"' in html
    assert '</a>\n        <div class="narration"' in html
    assert 'class="player-logo-link"' in html
    assert 'href="https://omegaflow.dev/"' in html
    assert 'aria-label="Open the OmegaFlow website"' in html


def run_player_script(script_body: str) -> subprocess.CompletedProcess[str]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")

    script = (
        r"""
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const html = fs.readFileSync('src/omegaflow/player/static/cast-player.html', 'utf8');
const scripts = [...html.matchAll(/<script(?:\s+src="([^"]+)")?\s*>([\s\S]*?)<\/script>/g)];
const elements = new Map();

function element(id) {
  if (!elements.has(id)) {
    const item = {
      id,
      children: [],
      className: '',
      textContent: '',
      innerHTML: '',
      scrollTop: 0,
      scrollLeft: 0,
      scrollHeight: 0,
      clientHeight: 0,
      offsetTop: 0,
      offsetHeight: 0,
      value: '0',
      disabled: false,
      hidden: false,
      style: {
        properties: {},
        setProperty(name, value) {
          this.properties[name] = value;
        },
      },
      dataset: {},
      appendChild(child) {
        this.children.push(child);
      },
      querySelectorAll(selector) {
        if (selector === '.section-marker') {
          return this.children.filter((child) => (
            String(child.className || '').split(/\s+/).includes('section-marker')
          ));
        }
        return [];
      },
      querySelector() {
        return null;
      },
      classList: {
        add() {},
        remove() {},
      },
      addEventListener() {},
      setAttribute() {},
      removeAttribute() {},
      getBoundingClientRect() {
        return {left: 0, width: 100};
      },
    };
    elements.set(id, item);
  }
  return elements.get(id);
}

const context = {
  URLSearchParams,
  window: {location: {search: '?cast=/casts/demo.cast&title=Demo'}},
  document: {
    title: '',
    getElementById: element,
    createElement: () => element(`created-${elements.size}`),
    addEventListener() {},
  },
  fetch: () => new Promise(() => {}),
  Audio: function () {
    return {
      addEventListener() {},
      pause() {},
      play() { return Promise.resolve(); },
      currentTime: 0,
      duration: 0,
      muted: false,
    };
  },
  performance: {now: () => 0},
  setTimeout: () => 0,
  clearTimeout() {},
  setInterval: () => 0,
  clearInterval() {},
  console,
};
context.window.parent = context.window;
context.window.document = context.document;
vm.createContext(context);
for (const script of scripts) {
  const source = script[1]
    ? fs.readFileSync(path.join('src/omegaflow/player/static', script[1]), 'utf8')
    : script[2];
  vm.runInContext(source, context);
}
"""
        + script_body
    )
    return subprocess.run(
        [node, "-"],
        cwd=REPO_ROOT,
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )


def test_preview_seek_does_not_overwrite_scrubber_value() -> None:
    result = run_player_script(
        r"""
vm.runInContext(`
totalSeconds = 100;
progress.value = '750';
previewSeek(progressValueSeconds());
if (progress.value !== '750') {
  console.error(JSON.stringify({value: progress.value}));
  process.exit(1);
}
if (currentSeconds !== 75) {
  console.error(JSON.stringify({currentSeconds}));
  process.exit(1);
}
`, context);
"""
    )

    assert result.returncode == 0, result.stderr


def test_scrubbing_seeks_audio_to_selected_time_before_release() -> None:
    result = run_player_script(
        r"""
vm.runInContext(`
totalSeconds = 100;
events = [{time: 100, data: 'done'}];
audioReady = true;
audio = new Audio('/audio.mp3');
audio.duration = 100;
playing = true;
startedAt = 0;
beginScrub();
progress.value = '750';
previewSeek(progressValueSeconds());
if (audio.currentTime !== 75) {
  console.error(JSON.stringify({phase: 'preview', audioTime: audio.currentTime}));
  process.exit(1);
}
commitProgressSeek();
if (!playing || currentSeconds !== 75 || audio.currentTime !== 75) {
  console.error(JSON.stringify({playing, currentSeconds, audioTime: audio.currentTime}));
  process.exit(1);
}
`, context);
"""
    )

    assert result.returncode == 0, result.stderr


def test_player_does_not_make_first_audio_segment_intro_by_default() -> None:
    result = run_player_script(
        r"""
vm.runInContext(`
narrationSegments = [
  {id: 'install', offset: 0, duration: 10, waits: [], pauseAfter: 0},
];
introSeconds = 10;
if (selectedIntroSegmentIndex() !== -1) {
  console.error(JSON.stringify({
    selectedIntroSegmentIndex: selectedIntroSegmentIndex(),
  }));
  process.exit(1);
}
`, context);
"""
    )

    assert result.returncode == 0, result.stderr


def test_player_starts_first_audio_segment_at_first_caption_by_default() -> None:
    result = run_player_script(
        r"""
vm.runInContext(`
narrationSegments = [
  {
    id: 'install',
    offset: 0,
    duration: 10,
    waits: [],
    pauseAfter: 0,
    guide: null,
    heading: 'Install',
  },
];
introSeconds = 0;
const presentationEvents = buildPresentationEvents([
  {time: 0.1, data: 'before caption'},
  {time: 0.2, data: '\\u001b[36;1m# Install\\n'},
  {time: 1.0, data: 'python -m pip install omegaflow'},
]);
if (presentationEvents[0].time !== 0.1) {
  console.error(JSON.stringify({firstEventTime: presentationEvents[0].time}));
  process.exit(1);
}
if (
  audioPlaybackSegments.length !== 1 ||
  audioPlaybackSegments[0].id !== 'install' ||
  audioPlaybackSegments[0].presentationStart !== 0.2
) {
  console.error(JSON.stringify({audioPlaybackSegments}));
  process.exit(1);
}
`, context);
"""
    )

    assert result.returncode == 0, result.stderr


def test_narration_scroll_uses_position_relative_to_narration_box() -> None:
    result = run_player_script(
        r"""
vm.runInContext(`
const currentWord = {
  offsetTop: 100,
  getBoundingClientRect() { return {top: 100}; },
};
narration.clientHeight = 40;
narration.scrollHeight = 100;
narration.scrollTop = 0;
narration.getBoundingClientRect = () => ({top: 100});
narration.querySelector = () => currentWord;
updateNarrationScroll();
if (narration.scrollTop !== 0) {
  console.error(JSON.stringify({scrollTop: narration.scrollTop}));
  process.exit(1);
}
narration.scrollTop = 20;
updateNarrationScroll();
if (narration.scrollTop !== 20) {
  console.error(JSON.stringify({scrollTop: narration.scrollTop}));
  process.exit(1);
}
`, context);
"""
    )

    assert result.returncode == 0, result.stderr
