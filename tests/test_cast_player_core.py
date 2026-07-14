from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_core_script(script: str) -> subprocess.CompletedProcess[str]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")
    return subprocess.run(
        [node, "-"],
        cwd=REPO_ROOT,
        input=(
            "const core = require('./src/omegaflow/player/static/cast-player-core.js');\n"
            + script
        ),
        text=True,
        capture_output=True,
        check=False,
    )


def test_shared_shell_maps_global_time_and_retains_current_and_next() -> None:
    result = run_core_script(
        r"""
const manifest = {
  manifest_version: 1,
  recording: {id: 'demo', duration_ms: 2000},
  renderers: {terminal: {payload_version: 1}},
  assets: {},
  beats: [
    {id: 'one', renderer: 'terminal', offset_ms: 0, duration_ms: 1000, payload: 'one.cast'},
    {id: 'two', renderer: 'terminal', offset_ms: 1000, duration_ms: 1000, payload: 'two.cast'},
  ],
};
const calls = [];
function factory() {
  let beat = null;
  return {
    async load(context) { beat = context.beat; calls.push(`load:${beat.id}`); },
    renderAt(localMs) { calls.push(`render:${beat.id}:${localMs}`); },
    setPlaybackRate(rate) { calls.push(`rate:${beat ? beat.id : 'new'}:${rate}`); },
    async preload() { calls.push(`preload:${beat.id}`); },
    dispose() { calls.push(`dispose:${beat.id}`); },
  };
}
(async () => {
  const shell = core.createPresentationShell({
    manifest,
    rendererFactories: {terminal: factory},
    loadPayload: async (beat) => beat.payload,
  });
  await shell.renderAt(500);
  await shell.renderAt(1200);
  shell.setPlaybackRate(1.5);
  shell.dispose();
  const required = [
    'load:one', 'render:one:500', 'load:two', 'preload:two',
    'render:two:200', 'rate:two:1.5',
    'dispose:one', 'dispose:two',
  ];
  for (const item of required) {
    if (!calls.includes(item)) {
      console.error(JSON.stringify({calls, missing: item}));
      process.exit(1);
    }
  }
})().catch((error) => {
  console.error(error.stack);
  process.exit(1);
});
"""
    )

    assert result.returncode == 0, result.stderr


def test_exact_half_open_boundary_selects_the_following_beat_at_local_zero() -> None:
    result = run_core_script(
        r"""
const manifest = {
  manifest_version: 1,
  recording: {id: 'boundary', duration_ms: 2000},
  renderers: {terminal: {payload_version: 1}},
  assets: {},
  beats: [
    {id: 'one', renderer: 'terminal', offset_ms: 0, duration_ms: 1000, payload: 'one.cast'},
    {id: 'two', renderer: 'terminal', offset_ms: 1000, duration_ms: 1000, payload: 'two.cast'},
  ],
};
const calls = [];
function factory() {
  let id = '';
  return {
    async load(context) { id = context.beat.id; },
    renderAt(localMs) { calls.push(`${id}:${localMs}`); },
    setPlaybackRate() {},
    async preload() {},
    dispose() {},
  };
}
(async () => {
  const shell = core.createPresentationShell({
    manifest,
    rendererFactories: {terminal: factory},
    loadPayload: async (beat) => beat.payload,
  });
  await shell.renderAt(999);
  await shell.renderAt(1000);
  const expected = ['one:999', 'two:0'];
  if (JSON.stringify(calls) !== JSON.stringify(expected)) {
    console.error(JSON.stringify({expected, calls}));
    process.exit(1);
  }
})().catch((error) => {
  console.error(error.stack);
  process.exit(1);
});
"""
    )

    assert result.returncode == 0, result.stderr


def test_terminal_renderer_reconstructs_from_header_on_every_seek() -> None:
    result = run_core_script(
        r"""
const output = [];
const renderer = core.createTerminalRendererAdapter({
  reset({header}) { output.push(`reset:${header.version}`); },
  applyEvent({event}) { output.push(event.data); },
});
(async () => {
  await renderer.load({
    container: {},
    payload: '{"version":3,"term":{"cols":80,"rows":24}}\n[0.1,"o","A"]\n[0.2,"o","B"]\n',
  });
  renderer.renderAt(250);
  renderer.renderAt(400);
  const expected = ['reset:3', 'A', 'reset:3', 'A', 'B'];
  if (JSON.stringify(output) !== JSON.stringify(expected)) {
    console.error(JSON.stringify({expected, output}));
    process.exit(1);
  }
})().catch((error) => {
  console.error(error.stack);
  process.exit(1);
});
"""
    )

    assert result.returncode == 0, result.stderr


def test_browser_renderer_is_seek_pure_and_reconstructs_layered_scene() -> None:
    result = run_core_script(
        r"""
const payload = {
  payload_version: 1,
  beat_id: 'browser',
  duration_ms: 1000,
  viewport: {width: 1000, height: 500, device_scale_factor: 1},
  initial_state: 'initial',
  initial_pointer: {x: 0, y: 0, visible: true},
  initial_display_url: 'https://example.test/',
  events: [
    {
      kind: 'pointer_move', action_id: 'open', at_ms: 100, end_ms: 300,
      start: {x: 0, y: 0}, end: {x: 100, y: 50},
      curve: {x1: 25, y1: 5, x2: 75, y2: 45},
    },
    {
      kind: 'click', action_id: 'open', at_ms: 300, end_ms: 400,
      point: {x: 100, y: 50}, button: 'left',
    },
    {
      kind: 'state', action_id: 'open', at_ms: 400, end_ms: 600,
      asset: 'final', transition: 'fade',
    },
    {
      kind: 'display_url', action_id: 'open', at_ms: 600, end_ms: 600,
      value: 'https://example.test/final',
    },
    {
      kind: 'clip', action_id: 'play', at_ms: 700, end_ms: 900,
      asset: 'clip', trim_start_ms: 0, trim_end_ms: 200,
    },
  ],
};
const scenes = [];
const renderer = core.createBrowserRendererAdapter({
  render({scene}) { scenes.push(JSON.parse(JSON.stringify(scene))); },
});
(async () => {
  await renderer.load({payload, beat: {id: 'browser'}, assets: {}, container: null});
  renderer.renderAt(500);
  renderer.renderAt(200);
  renderer.renderAt(500);
  if (JSON.stringify(scenes[0]) !== JSON.stringify(scenes[2])) {
    console.error(JSON.stringify(scenes));
    process.exit(1);
  }
  if (
    scenes[0].visual.asset !== 'final' ||
    scenes[0].visual.previousAsset !== 'initial' ||
    scenes[0].visual.progress !== 0.5 ||
    scenes[1].pointer.x <= 0 || scenes[1].pointer.x >= 100 ||
    scenes[2].displayUrl !== 'https://example.test/'
  ) {
    console.error(JSON.stringify(scenes));
    process.exit(1);
  }
  const final = renderer.renderAt(600);
  if (final.displayUrl !== 'https://example.test/final') {
    console.error(JSON.stringify(final));
    process.exit(1);
  }
  const clip = renderer.renderAt(800);
  if (clip.visual.kind !== 'clip' || clip.visual.previousAsset !== 'final') {
    console.error(JSON.stringify(clip));
    process.exit(1);
  }
  const earlyPointer = renderer.renderAt(150).pointer;
  if (earlyPointer.x <= 0 || earlyPointer.x >= 15) {
    console.error(JSON.stringify({earlyPointer}));
    process.exit(1);
  }
})().catch((error) => {
  console.error(error.stack);
  process.exit(1);
});
"""
    )

    assert result.returncode == 0, result.stderr


def test_browser_viewport_layout_scales_uniformly_and_letterboxes() -> None:
    result = run_core_script(
        r"""
const wide = core.browserViewportLayout(1000, 1000, {width: 1000, height: 500});
const tall = core.browserViewportLayout(500, 1000, {width: 1000, height: 500});
if (
  wide.scale !== 1 || wide.left !== 0 || wide.top !== 250 ||
  tall.scale !== 0.5 || tall.left !== 0 || tall.top !== 375
) {
  console.error(JSON.stringify({wide, tall}));
  process.exit(1);
}
"""
    )

    assert result.returncode == 0, result.stderr


def test_browser_dom_renderer_materializes_framing_overlays_scroll_and_clip() -> None:
    result = run_core_script(
        r"""
function node(tag) {
  return {
    tag, children: [], className: '', dataset: {}, hidden: false, textContent: '',
    clientWidth: 800, clientHeight: 600, currentTime: 0, duration: 10,
    muted: false, playsInline: false, playbackRate: 1,
    attributes: new Map(), style: {},
    append(...items) { this.children.push(...items); },
    replaceChildren(...items) { this.children = items; },
    setAttribute(name, value) { this.attributes.set(name, String(value)); },
    getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; },
    pause() {}, remove() { this.removed = true; },
  };
}
function find(root, className) {
  if (String(root.className).split(/\s+/).includes(className)) return root;
  for (const child of root.children || []) {
    const match = find(child, className);
    if (match) return match;
  }
  return null;
}
const document = {createElement: node};
const container = node('container');
const payload = {
  payload_version: 1, beat_id: 'browser', duration_ms: 1000,
  viewport: {width: 400, height: 200, device_scale_factor: 1},
  initial_state: 'initial', initial_pointer: {x: 1, y: 2, visible: true},
  initial_display_url: 'https://public.test/',
  events: [
    {kind: 'state', action_id: 'a', at_ms: 100, end_ms: 300, asset: 'final', transition: 'fade'},
    {kind: 'text', action_id: 'a', at_ms: 200, end_ms: 400, target: 'input', mode: 'fill', final: 'safe', style: {
      clipping_rect: {x: 10, y: 20, width: 120, height: 30}, font_family: 'sans-serif',
      font_size: 12, font_weight: '400', font_style: 'normal', line_height: 16,
      letter_spacing: 0, color: '#000', text_align: 'left', padding_top: 0,
      padding_right: 0, padding_bottom: 0, padding_left: 0,
    }},
    {kind: 'display_url', action_id: 'a', at_ms: 200, end_ms: 200, value: 'https://public.test/safe'},
    {kind: 'scroll', action_id: 'b', at_ms: 400, end_ms: 600,
      start_asset: 'final', end_asset: 'scrolled', container: {x: 0, y: 0, width: 200, height: 100},
      start: {x: 0, y: 0}, end: {x: 0, y: 80}},
    {kind: 'clip', action_id: 'c', at_ms: 600, end_ms: 800, asset: 'clip', trim_start_ms: 100, trim_end_ms: 500},
  ],
};
const assets = {
  initial: {path: 'initial.webp'}, final: {path: 'final.webp'},
  scrolled: {path: 'scrolled.webp'}, clip: {path: 'clip.webm'},
};
(async () => {
  const renderer = core.createBrowserDomRenderer({document});
  await renderer.load({
    assets, beat: {id: 'browser', transition_in: 'window-open'}, container, payload,
    presentation: {browser: {window: {mode: 'framed', theme: 'kde-breeze', title: 'Demo'}, chrome: {mode: 'full'}}},
  });
  renderer.renderAt(250);
  const root = container.children[0];
  const frame = find(root, 'browser-window');
  const chrome = find(root, 'browser-chrome');
  const url = find(root, 'browser-chrome-url');
  const text = find(root, 'browser-text-overlay');
  const primary = find(root, 'browser-state-primary');
  if (
    frame.dataset.mode !== 'framed' || chrome.dataset.mode !== 'full' || chrome.hidden ||
    url.textContent !== 'https://public.test/safe' || text.textContent !== 's' ||
    primary.style.opacity !== '0.75' || frame.style.transform !== 'scale(0.9866666666666667)'
  ) {
    console.error(JSON.stringify({frame, chrome, url: url.textContent, text: text.textContent, opacity: primary.style.opacity}));
    process.exit(1);
  }
  if (renderer.state().decodedAssetBytes !== 400 * 200 * 4 * 4) {
    console.error(JSON.stringify(renderer.state()));
    process.exit(1);
  }
  renderer.renderAt(500);
  const scrollClip = find(root, 'browser-scroll-clip');
  const scrollImage = find(root, 'browser-scroll-image');
  if (scrollClip.hidden || scrollImage.style.transform !== 'translate(0px, -40px)') {
    console.error(JSON.stringify({hidden: scrollClip.hidden, transform: scrollImage.style.transform}));
    process.exit(1);
  }
  if (scrollImage.style.width !== '400px' || scrollImage.style.height !== '200px') {
    console.error(JSON.stringify({width: scrollImage.style.width, height: scrollImage.style.height}));
    process.exit(1);
  }
  renderer.setPlaybackRate(1.5);
  renderer.renderAt(700);
  const clip = find(root, 'browser-clip');
  if (
    clip.hidden || !clip.muted || clip.playbackRate !== 1.5 ||
    Math.abs(clip.currentTime - 0.3) > 0.001 || primary.hidden ||
    primary.getAttribute('src') !== 'scrolled.webp' || clip.style.opacity !== '1'
  ) {
    console.error(JSON.stringify({
      hidden: clip.hidden, muted: clip.muted, rate: clip.playbackRate,
      time: clip.currentTime, fallbackHidden: primary.hidden,
      fallback: primary.getAttribute('src'), opacity: clip.style.opacity,
    }));
    process.exit(1);
  }
})().catch((error) => {
  console.error(error.stack);
  process.exit(1);
});
"""
    )

    assert result.returncode == 0, result.stderr


def test_presentation_audio_controller_pauses_gaps_and_corrects_drift() -> None:
    result = run_core_script(
        r"""
const calls = [];
const audio = {
  currentTime: 0,
  muted: false,
  paused: true,
  playbackRate: 1,
  pause() { this.paused = true; calls.push('pause'); },
  play() { this.paused = false; calls.push('play'); return Promise.resolve(); },
};
const controller = core.createPresentationAudioController({
  audio,
  intervals: [
    {presentation_start_ms: 100, presentation_end_ms: 500, source_start_ms: 0, source_end_ms: 400},
    {presentation_start_ms: 800, presentation_end_ms: 1200, source_start_ms: 400, source_end_ms: 800},
  ],
});
const first = controller.synchronize(300, {playing: true, playbackRate: 1.5, muted: true});
audio.currentTime = 0.21;
controller.synchronize(310, {playing: true, playbackRate: 1.5, muted: true});
const gap = controller.synchronize(600, {playing: true});
const second = controller.synchronize(1100, {playing: false});
if (
  !first.active || first.sourceMs !== 200 ||
  gap.active || gap.sourceMs !== 400 ||
  !second.active || second.sourceMs !== 700 ||
  audio.currentTime !== 0.7 || audio.playbackRate !== 1 ||
  controller.state().correctionCount !== 3 ||
  calls.filter((value) => value === 'play').length !== 1
) {
  console.error(JSON.stringify({audio, calls, first, gap, second, state: controller.state()}));
  process.exit(1);
}
"""
    )

    assert result.returncode == 0, result.stderr


def test_presentation_audio_controller_reports_blocked_play_without_retrying() -> None:
    result = run_core_script(
        r"""
let rejectPlay;
let playCalls = 0;
const rejections = [];
const audio = {
  currentTime: 0,
  muted: false,
  paused: true,
  playbackRate: 1,
  pause() { this.paused = true; },
  play() {
    playCalls += 1;
    return new Promise((_resolve, reject) => { rejectPlay = reject; });
  },
};
(async () => {
  const controller = core.createPresentationAudioController({
    audio,
    intervals: [
      {presentation_start_ms: 0, presentation_end_ms: 1000, source_start_ms: 0, source_end_ms: 1000},
    ],
    onPlayRejected(error) { rejections.push(error.name); },
  });
  controller.synchronize(0, {playing: true});
  controller.synchronize(20, {playing: true});
  if (playCalls !== 1 || !controller.state().playPending) {
    console.error(JSON.stringify({phase: 'pending', playCalls, state: controller.state()}));
    process.exit(1);
  }
  const error = new Error('audible autoplay is blocked');
  error.name = 'NotAllowedError';
  rejectPlay(error);
  await Promise.resolve();
  await Promise.resolve();
  if (
    playCalls !== 1 || controller.state().playPending ||
    JSON.stringify(rejections) !== '["NotAllowedError"]'
  ) {
    console.error(JSON.stringify({phase: 'rejected', playCalls, rejections, state: controller.state()}));
    process.exit(1);
  }
})().catch((error) => {
  console.error(error.stack);
  process.exit(1);
});
"""
    )

    assert result.returncode == 0, result.stderr


def test_shell_fails_closed_when_current_and_next_exceed_decoded_budget() -> None:
    result = run_core_script(
        r"""
const manifest = {
  manifest_version: 1,
  recording: {id: 'memory', duration_ms: 2000},
  renderers: {browser: {payload_version: 1}},
  assets: {},
  beats: [
    {id: 'one', renderer: 'browser', offset_ms: 0, duration_ms: 1000, payload: 'one.json'},
    {id: 'two', renderer: 'browser', offset_ms: 1000, duration_ms: 1000, payload: 'two.json'},
  ],
};
function factory() {
  return {
    async load() {}, renderAt() {}, async preload() {}, dispose() {},
    state() { return {decodedAssetBytes: 40 * 1024 * 1024}; },
  };
}
(async () => {
  const shell = core.createPresentationShell({
    manifest,
    rendererFactories: {browser: factory},
    loadPayload: async () => ({}),
  });
  try {
    await shell.renderAt(100);
    process.exit(1);
  } catch (error) {
    if (!String(error).includes('memory budget exceeded')) {
      console.error(error.stack);
      process.exit(1);
    }
  }
})().catch((error) => {
  console.error(error.stack);
  process.exit(1);
});
"""
    )

    assert result.returncode == 0, result.stderr


def test_shell_disposes_renderer_that_finishes_loading_after_shell_disposal() -> None:
    result = run_core_script(
        r"""
const manifest = {
  manifest_version: 1,
  recording: {id: 'dispose', duration_ms: 1000},
  renderers: {browser: {payload_version: 1}},
  assets: {},
  beats: [
    {id: 'one', renderer: 'browser', offset_ms: 0, duration_ms: 1000, payload: 'one.json'},
  ],
};
let release;
const blocked = new Promise((resolve) => { release = resolve; });
let disposed = 0;
let removed = 0;
function factory() {
  return {
    async load() { await blocked; }, renderAt() {}, async preload() {},
    dispose() { disposed += 1; },
  };
}
(async () => {
  const shell = core.createPresentationShell({
    manifest,
    rendererFactories: {browser: factory},
    loadPayload: async () => ({}),
    createRendererContainer: () => ({}),
    removeRendererContainer: () => { removed += 1; },
  });
  const rendering = shell.renderAt(100);
  await Promise.resolve();
  shell.dispose();
  release();
  try {
    await rendering;
    process.exit(1);
  } catch (error) {
    if (!String(error).includes('disposed') || disposed !== 1 || removed !== 1) {
      console.error(JSON.stringify({error: String(error), disposed, removed}));
      process.exit(1);
    }
  }
})().catch((error) => {
  console.error(error.stack);
  process.exit(1);
});
"""
    )

    assert result.returncode == 0, result.stderr


def test_shared_shell_rejects_non_contiguous_manifest() -> None:
    result = run_core_script(
        r"""
try {
  core.validatePresentationManifest({
    manifest_version: 1,
    recording: {duration_ms: 10},
    renderers: {terminal: {payload_version: 1}},
    beats: [{id: 'one', renderer: 'terminal', offset_ms: 1, duration_ms: 10, payload: 'one.cast'}],
  });
  process.exit(1);
} catch (error) {
  if (!String(error).includes('not contiguous')) {
    console.error(error.stack);
    process.exit(1);
  }
}
"""
    )

    assert result.returncode == 0, result.stderr


def test_terminal_decoder_rejects_decreasing_v2_timestamps() -> None:
    result = run_core_script(
        r"""
try {
  core.decodeAsciinemaCast(
    '{"version":2,"width":80,"height":24}\n[0.2,"o","A"]\n[0.1,"o","B"]\n',
  );
  process.exit(1);
} catch (error) {
  if (!String(error).includes('not ordered')) {
    console.error(error.stack);
    process.exit(1);
  }
}
"""
    )

    assert result.returncode == 0, result.stderr
