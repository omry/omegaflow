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


def test_browser_pointer_uses_an_upright_cursor_silhouette() -> None:
    html = (
        REPO_ROOT / "src/omegaflow/player/static/cast-player.html"
    ).read_text(encoding="utf-8")

    assert "polygon(0 0, 0 20px" in html
    assert "border-top: 16px solid #fff" not in html


def test_browser_window_uses_a_contrasting_desktop_surface() -> None:
    html = (
        REPO_ROOT / "src/omegaflow/player/static/cast-player.html"
    ).read_text(encoding="utf-8")

    assert "radial-gradient(circle at 50% 35%, #34405a" in html
    assert "align-items: center" in html
    assert "justify-content: center" in html


def test_embedded_player_preserves_browser_stage_ratio() -> None:
    html = (
        REPO_ROOT / "src/omegaflow/player/static/cast-player.html"
    ).read_text(encoding="utf-8")

    assert (
        "playerRoot.dataset.embedded = String(params.get('embed') === '1')" in html
    )
    assert "playerRoot.dataset.layout = params.get('layout') || ''" in html
    assert '.player[data-layout="wide-browser"]' in html
    assert 'grid-template-rows: auto minmax(0, 1fr) auto;' in html
    assert '.player[data-embedded="true"] .status' in html
    assert '.player[data-embedded="true"] .progress-wrap' in html


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


def test_player_preloads_per_take_audio_for_seamless_handoff() -> None:
    html = (
        REPO_ROOT / "src/omegaflow/player/static/cast-player.html"
    ).read_text(encoding="utf-8")

    assert "element.preload = 'auto';" in html
    assert "element.preload = 'metadata';" not in html


def test_playback_does_not_start_before_player_initialization_finishes() -> None:
    result = run_player_script(
        r"""
vm.runInContext(`
let audioPlayCalls = 0;
presentationManifest = {
  recording: {duration_ms: 10000},
  audio: {
    metadata: 'audio.json',
    intervals: [
      {presentation_start_ms: 0, presentation_end_ms: 10000, source_start_ms: 0, source_end_ms: 10000},
    ],
  },
  beats: [{id: 'terminal', renderer: 'terminal', offset_ms: 0, duration_ms: 10000}],
};
events = [{time: 10, data: 'done'}];
totalSeconds = 10;
audioReady = true;
audio = {
  currentTime: 0,
  duration: 10,
  muted: false,
  paused: true,
  playbackRate: 1,
  pause() { this.paused = true; },
  play() {
    audioPlayCalls += 1;
    this.paused = false;
    return Promise.resolve();
  },
};

updateTransportButtons();
togglePlayPause();

if (playing || audioPlayCalls !== 0 || !playButton.disabled) {
  console.error(JSON.stringify({playing, audioPlayCalls, playDisabled: playButton.disabled}));
  process.exit(1);
}

presentationAudioController = CastPlayerCore.createPresentationAudioController({
  audio,
  intervals: presentationManifest.audio.intervals,
});
playbackReady = true;
updateTransportButtons();
togglePlayPause();

if (!playing || audioPlayCalls !== 1 || playButton.disabled) {
  console.error(JSON.stringify({
    phase: 'ready', playing, audioPlayCalls, playDisabled: playButton.disabled,
  }));
  process.exit(1);
}
`, context);
"""
    )

    assert result.returncode == 0, result.stderr


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
const documentListeners = new Map();

function element(id) {
  if (!elements.has(id)) {
    const listeners = new Map();
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
      addEventListener(type, listener) {
        const handlers = listeners.get(type) || [];
        handlers.push(listener);
        listeners.set(type, handlers);
      },
      dispatchEvent(event) {
        for (const listener of listeners.get(event.type) || []) {
          listener(event);
        }
      },
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
  URL,
  URLSearchParams,
  window: {location: {search: '?manifest=/videos/demo/recording.presentation.json&title=Demo'}},
  document: {
    title: '',
    getElementById: element,
    createElement: () => element(`created-${elements.size}`),
    addEventListener(type, listener) {
      const listeners = documentListeners.get(type) || [];
      listeners.push(listener);
      documentListeners.set(type, listeners);
    },
    dispatchEvent(event) {
      for (const listener of documentListeners.get(event.type) || []) {
        listener(event);
      }
    },
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
  process,
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


def test_watch_autoplay_counts_down_before_playing() -> None:
    result = run_player_script(
        r"""
const countdownTimers = [];
context.setTimeout = (callback, delay) => {
  const timer = {callback, delay};
  countdownTimers.push(timer);
  return timer;
};
context.clearTimeout = () => {};
vm.runInContext(`
events = [{time: 1, data: 'done'}];
totalSeconds = 1;
playbackReady = true;
startAutoplayCountdown();
`, context);

const initialTimers = countdownTimers.slice();
const flash = element('playback-flash');
if (flash.textContent !== '3') {
  console.error(JSON.stringify({phase: 'three', text: flash.textContent}));
  process.exit(1);
}
if (JSON.stringify(initialTimers.map((timer) => timer.delay)) !== '[1000,2000,3000]') {
  console.error(JSON.stringify({delays: initialTimers.map((timer) => timer.delay)}));
  process.exit(1);
}

initialTimers[0].callback();
if (flash.textContent !== '2') {
  console.error(JSON.stringify({phase: 'two', text: flash.textContent}));
  process.exit(1);
}
initialTimers[1].callback();
if (flash.textContent !== '1') {
  console.error(JSON.stringify({phase: 'one', text: flash.textContent}));
  process.exit(1);
}
initialTimers[2].callback();
if (!vm.runInContext('playing', context) || !flash.innerHTML.includes('<svg')) {
  console.error(JSON.stringify({
    phase: 'play',
    playing: vm.runInContext('playing', context),
    html: flash.innerHTML,
  }));
  process.exit(1);
}
"""
    )

    assert result.returncode == 0, result.stderr


def test_terminal_text_highlight_marker_adds_and_removes_exact_occurrence() -> None:
    result = run_player_script(
        r"""
vm.runInContext(`
resetTerminalBuffer();
appendChunk('created config.yaml\\ncreated config.yaml');
applyTerminalHighlightMarker('omegaflow:highlight:{"active":true,"id":"one","text":"config.yaml","occurrence":2}');
`, context);
const highlighted = element('terminal').innerHTML;
if ((highlighted.match(/terminal-text-highlight/g) || []).length !== 1) {
  console.error(JSON.stringify({phase: 'active', highlighted}));
  process.exit(1);
}
if (!highlighted.includes('<span class="terminal-text-highlight">config.yaml</span>')) {
  console.error(JSON.stringify({phase: 'exact-text', highlighted}));
  process.exit(1);
}
vm.runInContext(`
applyTerminalHighlightMarker('omegaflow:highlight:{"active":false,"id":"one"}');
`, context);
const cleared = element('terminal').innerHTML;
if (cleared.includes('terminal-text-highlight')) {
  console.error(JSON.stringify({phase: 'cleared', cleared}));
  process.exit(1);
}
"""
    )

    assert result.returncode == 0, result.stderr


def test_terminal_tui_redraw_updates_lines_in_place_and_removes_finished_surface() -> None:
    result = run_player_script(
        r"""
vm.runInContext(`
resetTerminalBuffer();
appendChunk('progress [░░░░] 0/4\\ncurrent Recording\\n');
appendChunk('\\u001b[');
appendChunk('2F\\r\\u001b[2Kprogress [██░░] 2/4\\n\\r\\u001b[2Kcurrent Assembling\\n');
`, context);
const redrawn = element('terminal').innerHTML;
if (
  redrawn.includes('0/4') || redrawn.includes('Recording') ||
  !redrawn.includes('2/4') || !redrawn.includes('Assembling')
) {
  console.error(JSON.stringify({phase: 'redrawn', redrawn}));
  process.exit(1);
}
vm.runInContext(`appendChunk('\\u001b[2F\\u001b[2M');`, context);
const finished = element('terminal').innerHTML;
if (finished.includes('progress') || finished.includes('current')) {
  console.error(JSON.stringify({phase: 'finished', finished}));
  process.exit(1);
}
"""
    )

    assert result.returncode == 0, result.stderr


def test_terminal_tui_supports_cursor_motion_erasure_and_alternate_screen() -> None:
    result = run_player_script(
        r"""
vm.runInContext(`
resetTerminalBuffer();
appendChunk('primary\\nsecond');
appendChunk('\\u001b[1F\\u001b[3CX\\r\\u001b[2Kreplacement');
`, context);
const edited = element('terminal').innerHTML;
if (!edited.startsWith('replacement\nsecond')) {
  console.error(JSON.stringify({phase: 'edited', edited}));
  process.exit(1);
}
vm.runInContext(`
appendChunk('\\u001b[?1049hfull-screen\\u001b[2;4H!');
`, context);
const alternate = element('terminal').innerHTML;
if (!alternate.startsWith('full-screen') || !alternate.includes('   !')) {
  console.error(JSON.stringify({phase: 'alternate', alternate}));
  process.exit(1);
}
vm.runInContext(`appendChunk('\\u001b[?1049l');`, context);
const restored = element('terminal').innerHTML;
if (restored !== edited) {
  console.error(JSON.stringify({phase: 'restored', edited, restored}));
  process.exit(1);
}
"""
    )

    assert result.returncode == 0, result.stderr


def test_scrubbing_cancels_watch_autoplay_countdown() -> None:
    result = run_player_script(
        r"""
const countdownTimers = [];
const clearedTimers = [];
context.setTimeout = (callback, delay) => {
  const timer = {callback, delay};
  countdownTimers.push(timer);
  return timer;
};
context.clearTimeout = (timer) => { clearedTimers.push(timer); };
vm.runInContext(`
totalSeconds = 10;
startAutoplayCountdown();
beginScrub();
if (
  autoplayCountdownActive || autoplayCountdownTimers.length !== 0 ||
  !scrubbing || playing
) {
  console.error(JSON.stringify({
    autoplayCountdownActive,
    timerCount: autoplayCountdownTimers.length,
    scrubbing,
    playing,
  }));
  process.exit(1);
}
`, context);
if (clearedTimers.length !== 3) {
  console.error(JSON.stringify({cleared: clearedTimers.length}));
  process.exit(1);
}
"""
    )

    assert result.returncode == 0, result.stderr


def test_keyboard_seek_cancels_watch_autoplay_countdown() -> None:
    result = run_player_script(
        r"""
const countdownTimers = [];
const clearedTimers = [];
context.setTimeout = (callback, delay) => {
  const timer = {callback, delay};
  countdownTimers.push(timer);
  return timer;
};
context.clearTimeout = (timer) => { clearedTimers.push(timer); };
vm.runInContext(`
totalSeconds = 20;
events = [{time: 20, data: 'done'}];
startAutoplayCountdown();
progress.dispatchEvent({
  type: 'keydown',
  key: 'ArrowRight',
  target: progress,
  defaultPrevented: false,
  altKey: false,
  ctrlKey: false,
  metaKey: false,
  shiftKey: false,
  preventDefault() { this.defaultPrevented = true; },
});
if (
  autoplayCountdownActive || autoplayCountdownTimers.length !== 0 ||
  currentSeconds !== 10
) {
  console.error(JSON.stringify({
    autoplayCountdownActive,
    timerCount: autoplayCountdownTimers.length,
    currentSeconds,
  }));
  process.exit(1);
}
`, context);
if (clearedTimers.length !== 3) {
  console.error(JSON.stringify({cleared: clearedTimers.length}));
  process.exit(1);
}
"""
    )

    assert result.returncode == 0, result.stderr


def test_watch_autoplay_rewinds_when_browser_blocks_audio() -> None:
    result = run_player_script(
        r"""
vm.runInContext(`
(async () => {
  let allowAudio = false;
  presentationManifest = {
    recording: {duration_ms: 10000},
    beats: [{id: 'terminal', renderer: 'terminal', offset_ms: 0, duration_ms: 10000}],
  };
  events = [{time: 10, data: 'done'}];
  totalSeconds = 10;
  audioReady = true;
  audio = {
    currentTime: 0,
    duration: 10,
    muted: false,
    paused: true,
    playbackRate: 1,
    pause() { this.paused = true; },
    play() {
      if (!allowAudio) {
        const error = new Error('audible autoplay is blocked');
        error.name = 'NotAllowedError';
        return Promise.reject(error);
      }
      this.paused = false;
      return Promise.resolve();
    },
  };
  presentationAudioController = CastPlayerCore.createPresentationAudioController({
    audio,
    intervals: [
      {presentation_start_ms: 0, presentation_end_ms: 10000, source_start_ms: 0, source_end_ms: 10000},
    ],
    onPlayRejected: handleAudioPlaybackRejected,
    onPlayStarted: handleAudioPlaybackStarted,
  });
  playbackReady = true;

  play({autoplay: true, feedback: true});
  await Promise.resolve();
  await Promise.resolve();
  if (
    playing || currentSeconds !== 0 || audio.currentTime !== 0 ||
    playbackFlash.dataset.audioUnlock !== 'true' ||
    playbackFlash.dataset.visible !== 'true' ||
    voice.dataset.state !== 'waiting'
  ) {
    console.error(JSON.stringify({
      phase: 'blocked', playing, currentSeconds, audioTime: audio.currentTime,
      flash: playbackFlash.dataset, voice: voice.dataset,
    }));
    process.exit(1);
  }

  allowAudio = true;
  play({feedback: true});
  await Promise.resolve();
  await Promise.resolve();
  if (!playing || audio.paused || playbackFlash.dataset.audioUnlock === 'true') {
    console.error(JSON.stringify({
      phase: 'unlocked', playing, paused: audio.paused, flash: playbackFlash.dataset,
    }));
    process.exit(1);
  }
})().catch((error) => {
  console.error(error.stack);
  process.exit(1);
});
`, context);
"""
    )

    assert result.returncode == 0, result.stderr


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


def test_manifest_scrubber_uses_beat_ticks_and_sections() -> None:
    result = run_player_script(
        r"""
vm.runInContext(`
presentationManifest = {
  beats: [
    {id: 'prepare', heading: 'Prepare', offset_ms: 0, duration_ms: 3320},
    {id: 'browser', heading: 'Browser', offset_ms: 3320, duration_ms: 4732},
    {id: 'verify', heading: 'Verify', offset_ms: 8052, duration_ms: 4316},
  ],
};
guidedPausePoints = [{time: 8.052, heading: 'Browser checkpoint'}];
totalSeconds = 12.368;
renderSectionMarkers();
const starts = sectionMarkers.children.map((marker) => Number(marker.dataset.start));
const sectionStarts = sectionStartTimes();
const active = sectionForSeconds(4);
if (
  JSON.stringify(starts) !== JSON.stringify([0, 3.32, 8.052]) ||
  JSON.stringify(sectionStarts) !== JSON.stringify([0, 3.32, 8.052]) ||
  active.heading !== 'Browser'
) {
  console.error(JSON.stringify({starts, sectionStarts, active}));
  process.exit(1);
}
`, context);
"""
    )

    assert result.returncode == 0, result.stderr


def test_scrubbing_manifest_previews_renderer_and_does_not_snap_when_unguided() -> None:
    result = run_player_script(
        r"""
vm.runInContext(`
let renderedAt = null;
presentationManifest = {
  recording: {duration_ms: 12000},
  beats: [
    {id: 'one', renderer: 'terminal', offset_ms: 0, duration_ms: 4000},
    {id: 'two', renderer: 'browser', offset_ms: 4000, duration_ms: 4000},
    {id: 'three', renderer: 'terminal', offset_ms: 8000, duration_ms: 4000},
  ],
};
presentationShell = {
  renderAt(milliseconds) { renderedAt = milliseconds; return Promise.resolve(); },
  setPlaybackRate() {},
};
events = [{time: 12, data: 'done'}];
audioPlaybackSegments = [{
  audioStart: 0, audioEnd: 12, presentationStart: 0, presentationEnd: 12,
}];
audioReady = true;
audio = new Audio('/audio.mp3');
audio.duration = 12;
totalSeconds = 12;
guidedPausePoints = [{time: 8, heading: 'Checkpoint'}];
guidedMode = false;
playing = true;
startedAt = 0;
beginScrub();
progress.value = '500';
previewSeek(progressValueSeconds());
if (renderedAt !== 6000 || audio.currentTime !== 6) {
  console.error(JSON.stringify({phase: 'preview', renderedAt, audioTime: audio.currentTime}));
  process.exit(1);
}
commitProgressSeek();
if (!playing || currentSeconds !== 6 || audio.currentTime !== 6 || !guideModal.hidden) {
  console.error(JSON.stringify({
    phase: 'commit', playing, currentSeconds, audioTime: audio.currentTime,
    guideHidden: guideModal.hidden,
  }));
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


def test_browser_only_manifest_can_play_without_terminal_events() -> None:
    result = run_player_script(
        r"""
vm.runInContext(`
presentationManifest = {
  beats: [{id: 'browser', renderer: 'browser'}],
};
events = [];
totalSeconds = 10;
currentSeconds = 0;
playbackReady = true;
play();
if (!playing || progressTimer == null) {
  console.error(JSON.stringify({playing, progressTimer}));
  process.exit(1);
}
`, context);
"""
    )

    assert result.returncode == 0, result.stderr


def test_continue_from_final_browser_checkpoint_finishes_without_replay() -> None:
    result = run_player_script(
        r"""
vm.runInContext(`
presentationManifest = {beats: [{id: 'browser', renderer: 'browser'}]};
events = [];
totalSeconds = 10;
currentSeconds = 10;
pausedAtGuidedPoint = true;
playbackReady = true;
play();
if (playing || currentSeconds !== 10 || pausedAtGuidedPoint || !guideModal.hidden) {
  console.error(JSON.stringify({playing, currentSeconds, pausedAtGuidedPoint, hidden: guideModal.hidden}));
  process.exit(1);
}
`, context);
"""
    )

    assert result.returncode == 0, result.stderr


def test_player_applies_playback_rate_to_shell_audio_and_clock() -> None:
    result = run_player_script(
        r"""
vm.runInContext(`
let shellRate = 0;
presentationShell = {setPlaybackRate(rate) { shellRate = rate; }, renderAt() { return Promise.resolve(); }};
presentationManifest = {
  recording: {duration_ms: 10000},
  beats: [{id: 'browser', renderer: 'browser', offset_ms: 0, duration_ms: 10000}],
};
audio = new Audio('/audio.mp3');
totalSeconds = 10;
setPlaybackRate(1.5);
if (
  playbackRate !== 1.5 || shellRate !== 1.5 ||
  audio.playbackRate !== 1.5 || rateButton.textContent !== '1.5×'
) {
  console.error(JSON.stringify({playbackRate, shellRate, audioRate: audio.playbackRate, label: rateButton.textContent}));
  process.exit(1);
}
`, context);
"""
    )

    assert result.returncode == 0, result.stderr


def test_presentation_shell_is_the_only_terminal_event_renderer() -> None:
    result = run_player_script(
        r"""
const scheduled = [];
context.setTimeout = (callback, delay) => {
  scheduled.push({callback, delay});
  return scheduled.length;
};
context.scheduled = scheduled;
vm.runInContext(`
let legacyAppends = 0;
let shellRenders = 0;
appendChunk = () => { legacyAppends += 1; };
presentationManifest = {
  recording: {duration_ms: 2000},
  beats: [{id: 'terminal', renderer: 'terminal', offset_ms: 0, duration_ms: 2000}],
};
presentationShell = {
  renderAt() { shellRenders += 1; return Promise.resolve(); },
  setPlaying() {},
};
events = [{time: 1, data: 'typed once'}];
totalSeconds = 2;
currentSeconds = 0;
renderAt(1);
renderPresentationFrame(1.1);
renderPresentationFrame(1.2);
currentSeconds = 0;
scheduleEvents();
if (
  legacyAppends !== 0 || shellRenders !== 3 ||
  scheduled.some((timer) => timer.delay === 1000)
) {
  console.error(JSON.stringify({
    legacyAppends, shellRenders,
    delays: scheduled.map((timer) => timer.delay),
  }));
  process.exit(1);
}
`, context);
"""
    )

    assert result.returncode == 0, result.stderr


def test_player_suppresses_context_menu_and_right_click_decreases_rate() -> None:
    result = run_player_script(
        r"""
vm.runInContext(`
let prevented = 0;
rateButton.dispatchEvent({type: 'click', target: rateButton});
if (playbackRate !== 1.25) {
  console.error(JSON.stringify({phase: 'left', playbackRate}));
  process.exit(1);
}
playerRoot.dispatchEvent({
  type: 'contextmenu',
  target: rateButton,
  preventDefault() { prevented += 1; },
});
if (playbackRate !== 1 || prevented !== 1) {
  console.error(JSON.stringify({phase: 'rate-context', playbackRate, prevented}));
  process.exit(1);
}
playerRoot.dispatchEvent({
  type: 'contextmenu',
  target: terminal,
  preventDefault() { prevented += 1; },
});
if (playbackRate !== 1 || prevented !== 2) {
  console.error(JSON.stringify({phase: 'player-context', playbackRate, prevented}));
  process.exit(1);
}
if (!rateButton.title.includes('right-click previous')) {
  console.error(JSON.stringify({phase: 'title', title: rateButton.title}));
  process.exit(1);
}
cyclePlaybackRate(-1);
if (playbackRate !== 2) {
  console.error(JSON.stringify({phase: 'reverse-wrap', playbackRate}));
  process.exit(1);
}
`, context);
"""
    )

    assert result.returncode == 0, result.stderr


def test_narration_members_follow_manifest_audio_source_time() -> None:
    result = run_player_script(
        r"""
vm.runInContext(`
narrationSegments = [{
  id: 'take:create', offset: 2, duration: 3, heading: 'Create', text: 'Create it', wordSpans: [],
}];
audioPlaybackSegments = [{
  audioStart: 2, audioEnd: 5, presentationStart: 7, presentationEnd: 10,
}];
const active = narrationSegmentForSeconds(8.25);
if (
  active.segment.id !== 'take:create' ||
  Math.abs(active.localSeconds - 1.25) > 0.0001
) {
  console.error(JSON.stringify(active));
  process.exit(1);
}
`, context);
"""
    )

    assert result.returncode == 0, result.stderr


def test_player_loads_v3_take_audio_members_and_word_timestamps() -> None:
    result = run_player_script(
        r"""
vm.runInContext(`
audioMetaUrl = 'https://example.test/demo/audio.json';
manifestBaseUrl = new URL('https://example.test/demo/');
presentationManifest = {
  beats: [{id: 'create', heading: 'Create', guide: {success_hint: 'Created.'}}],
};
fetch = async (url) => ({
  ok: true,
  status: 200,
  async json() {
    if (new URL(String(url)).pathname.endsWith('audio.json')) {
        return {
          version: 3,
          takes: [{
            id: 'take', source_start_ms: 1000, source_end_ms: 2200,
            src: 'audio/take-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.mp3',
            sha256: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
            timestamps: 'timestamps/take.json',
          members: [{beat_id: 'create', text: 'Create it', text_start: 0, text_end: 9}],
        }],
      };
    }
    return {
      members: [{
        beat_id: 'create', text_start: 0, text_end: 9,
        source_start_ms: 200, source_end_ms: 1200,
      }],
      words: [{text: 'Create', text_start: 0, text_end: 6, start_ms: 200, end_ms: 600}],
    };
  },
});
loadAudioMeta().then(() => {
  const segment = narrationSegments[0];
  if (
      narrationSegments.length < 1 || audioTakeDescriptors.length !== 1 ||
      !audioTakeDescriptors[0].src.endsWith('.mp3') ||
      segment.offset !== 1.2 || segment.duration !== 1 ||
    segment.heading !== 'Create' || segment.guide.success_hint !== 'Created.' ||
    segment.wordSpans[0].textStart !== 0 || segment.wordSpans[0].start !== 0 ||
    segment.wordSpans[0].end !== 0.4
  ) {
    console.error(JSON.stringify(narrationSegments));
    process.exit(1);
  }
}).catch((error) => {
  console.error(error.stack);
  process.exit(1);
});
`, context);
"""
    )

    assert result.returncode == 0, result.stderr


def test_space_toggles_playback_and_shows_visual_feedback() -> None:
    result = run_player_script(
        r"""
vm.runInContext(`
events = [{time: 1, data: 'done'}];
totalSeconds = 1;
playbackReady = true;
updateTransportButtons();
let prevented = 0;
const event = {
  type: 'keydown',
  key: ' ',
  code: 'Space',
  target: terminal,
  defaultPrevented: false,
  altKey: false,
  ctrlKey: false,
  metaKey: false,
  shiftKey: false,
  repeat: false,
  preventDefault() {
    this.defaultPrevented = true;
    prevented += 1;
  },
};
document.dispatchEvent(event);
if (
  !playing ||
  prevented !== 1 ||
  playbackFlash.dataset.visible !== 'true' ||
  playbackFlash.innerHTML !== icons.play
) {
  console.error(JSON.stringify({
    phase: 'play',
    playing,
    prevented,
    flashVisible: playbackFlash.dataset.visible,
    flashIcon: playbackFlash.innerHTML,
  }));
  process.exit(1);
}
event.defaultPrevented = false;
document.dispatchEvent(event);
if (
  playing ||
  prevented !== 2 ||
  playbackFlash.dataset.visible !== 'true' ||
  playbackFlash.innerHTML !== icons.pause
) {
  console.error(JSON.stringify({
    phase: 'pause',
    playing,
    prevented,
    flashVisible: playbackFlash.dataset.visible,
    flashIcon: playbackFlash.innerHTML,
  }));
  process.exit(1);
}
`, context);
"""
    )

    assert result.returncode == 0, result.stderr


def test_space_does_not_hijack_editable_or_native_controls() -> None:
    result = run_player_script(
        r"""
vm.runInContext(`
events = [{time: 1, data: 'done'}];
totalSeconds = 1;
const targets = [
  {tagName: 'input', isContentEditable: false},
  {tagName: 'textarea', isContentEditable: false},
  {tagName: 'select', isContentEditable: false},
  {tagName: 'div', isContentEditable: true},
  {tagName: 'button', isContentEditable: false},
  {tagName: 'a', isContentEditable: false},
];
let prevented = 0;
for (const target of targets) {
  document.dispatchEvent({
    type: 'keydown',
    key: ' ',
    code: 'Space',
    target,
    defaultPrevented: false,
    altKey: false,
    ctrlKey: false,
    metaKey: false,
    shiftKey: false,
    repeat: false,
    preventDefault() { prevented += 1; },
  });
}
if (playing || prevented !== 0) {
  console.error(JSON.stringify({playing, prevented}));
  process.exit(1);
}
`, context);
"""
    )

    assert result.returncode == 0, result.stderr


def test_space_ignores_modified_or_repeated_keydown() -> None:
    result = run_player_script(
        r"""
vm.runInContext(`
events = [{time: 1, data: 'done'}];
totalSeconds = 1;
const baseEvent = {
  type: 'keydown',
  key: ' ',
  code: 'Space',
  target: terminal,
  defaultPrevented: false,
  altKey: false,
  ctrlKey: false,
  metaKey: false,
  shiftKey: false,
  repeat: false,
};
let prevented = 0;
for (const property of ['altKey', 'ctrlKey', 'metaKey', 'shiftKey', 'repeat']) {
  document.dispatchEvent({
    ...baseEvent,
    [property]: true,
    preventDefault() { prevented += 1; },
  });
}
if (playing || prevented !== 0) {
  console.error(JSON.stringify({playing, prevented}));
  process.exit(1);
}
`, context);
"""
    )

    assert result.returncode == 0, result.stderr
