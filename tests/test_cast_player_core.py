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
