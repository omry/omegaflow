from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_embed_script(script_body: str) -> subprocess.CompletedProcess[str]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")
    script = (
        r"""
const fs = require('fs');
const vm = require('vm');

class FakeHTMLElement {
  constructor() {
    this.attributes = new Map();
    this.children = [];
    this.textContent = '';
    this.isConnected = true;
  }

  getAttribute(name) {
    return this.attributes.has(name) ? this.attributes.get(name) : null;
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  querySelector(selector) {
    if (selector !== 'iframe') {
      return null;
    }
    return this.children.find((child) => child.tagName === 'iframe') || null;
  }

  appendChild(child) {
    this.children.push(child);
    return child;
  }
}

const defined = new Map();
const context = {
  URL,
  URLSearchParams,
  HTMLElement: FakeHTMLElement,
  customElements: {
    define(name, klass) {
      defined.set(name, klass);
    },
    get(name) {
      return defined.get(name);
    },
  },
  document: {
    baseURI: 'https://example.test/docs/watch/',
    createElement(tagName) {
      return {
        tagName,
        allow: '',
        allowFullscreen: false,
        loading: '',
        src: '',
        title: '',
      };
    },
  },
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(
  fs.readFileSync('src/omegaflow/player/static/cast-player-embed.js', 'utf8'),
  context,
);
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


def test_cast_player_embed_does_not_recreate_same_iframe() -> None:
    result = run_embed_script(
        r"""
const Element = context.customElements.get('cast-player-embed');
const element = new Element();
element.setAttribute('title', 'Demo Build');
element.setAttribute('manifest', '/videos/demo/recording.presentation.json');
element.setAttribute('intro-segment', 'overview');
element.setAttribute('player', '/cast-player.html');

element.connectedCallback();
const firstIframe = element.children[0];
firstIframe.src = new URL(firstIframe.src, context.document.baseURI).href;
element.attributeChangedCallback();
const secondIframe = element.children[0];

if (
  element.children.length !== 1 ||
  firstIframe !== secondIframe ||
  secondIframe.src !== 'https://example.test/cast-player.html?manifest=%2Fvideos%2Fdemo%2Frecording.presentation.json&embed=1&title=Demo+Build&introSegment=overview'
) {
  console.error(JSON.stringify({
    children: element.children.length,
    sameIframe: firstIframe === secondIframe,
    src: secondIframe && secondIframe.src,
  }));
  process.exit(1);
}
"""
    )

    assert result.returncode == 0, result.stderr


def test_cast_player_embed_updates_existing_iframe_when_url_changes() -> None:
    result = run_embed_script(
        r"""
const Element = context.customElements.get('cast-player-embed');
const element = new Element();
element.setAttribute('title', 'Demo Build');
element.setAttribute('manifest', '/videos/demo/recording.presentation.json');
element.setAttribute('player', '/cast-player.html');

element.connectedCallback();
const firstIframe = element.children[0];
element.setAttribute('manifest', '/videos/other/recording.presentation.json');
element.attributeChangedCallback();

if (
  element.children.length !== 1 ||
  element.children[0] !== firstIframe ||
  element.children[0].src !== '/cast-player.html?manifest=%2Fvideos%2Fother%2Frecording.presentation.json&embed=1&title=Demo+Build'
) {
  console.error(JSON.stringify({
    children: element.children.length,
    sameIframe: element.children[0] === firstIframe,
    src: element.children[0] && element.children[0].src,
  }));
  process.exit(1);
}
"""
    )

    assert result.returncode == 0, result.stderr


def test_cast_player_embed_accepts_manifest_and_title() -> None:
    result = run_embed_script(
        r"""
const Element = context.customElements.get('cast-player-embed');
const element = new Element();
element.setAttribute('title', 'Browser Demo');
element.setAttribute('manifest', '/videos/demo/recording.presentation.json');
element.setAttribute('layout', 'wide-browser');
element.setAttribute('player', '/cast-player.html');
element.connectedCallback();

if (
  element.children.length !== 1 ||
  element.children[0].src !== '/cast-player.html?manifest=%2Fvideos%2Fdemo%2Frecording.presentation.json&embed=1&layout=wide-browser&title=Browser+Demo'
) {
  console.error(JSON.stringify({src: element.children[0] && element.children[0].src}));
  process.exit(1);
}
"""
    )

    assert result.returncode == 0, result.stderr
