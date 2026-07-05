from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_player_script(script_body: str) -> subprocess.CompletedProcess[str]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")

    script = (
        r"""
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const html = fs.readFileSync('src/omegaflow_studio/player/static/cast-player.html', 'utf8');
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
    ? fs.readFileSync(path.join('src/omegaflow_studio/player/static', script[1]), 'utf8')
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
