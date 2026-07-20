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
const globalListeners = new Map();
const context = {
  URL,
  URLSearchParams,
  HTMLElement: FakeHTMLElement,
  addEventListener(type, listener) {
    const listeners = globalListeners.get(type) || [];
    listeners.push(listener);
    globalListeners.set(type, listeners);
  },
  dispatchEvent(event) {
    for (const listener of globalListeners.get(event.type) || []) {
      listener(event);
    }
  },
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
    querySelectorAll() {
      return [];
    },
    createElement(tagName) {
      return {
        tagName,
        allow: '',
        allowFullscreen: false,
        loading: '',
        src: '',
        title: '',
        contentWindow: {
          messages: [],
          postMessage(message, targetOrigin) {
            this.messages.push({message, targetOrigin});
          },
        },
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


def test_cast_player_embed_forwards_autoplay_mode() -> None:
    result = run_embed_script(
        r"""
const Element = context.customElements.get('cast-player-embed');
const element = new Element();
element.setAttribute('manifest', '/videos/demo/recording.presentation.json');
element.setAttribute('autoplay', 'countdown');
element.setAttribute('player', '/cast-player.html');
element.connectedCallback();

if (
  element.children.length !== 1 ||
  element.children[0].src !== '/cast-player.html?manifest=%2Fvideos%2Fdemo%2Frecording.presentation.json&embed=1&autoplay=countdown'
) {
  console.error(JSON.stringify({src: element.children[0] && element.children[0].src}));
  process.exit(1);
}
"""
    )

    assert result.returncode == 0, result.stderr


def test_cast_player_embed_allows_only_one_player_to_keep_audio_focus() -> None:
    result = run_embed_script(
        r"""
const Element = context.customElements.get('cast-player-embed');
const first = new Element();
first.setAttribute('manifest', '/videos/first/recording.presentation.json');
first.setAttribute('player', '/cast-player.html');
first.connectedCallback();
const second = new Element();
second.setAttribute('manifest', '/videos/second/recording.presentation.json');
second.setAttribute('player', '/cast-player.html');
second.connectedCallback();
const firstIframe = first.children[0];
const secondIframe = second.children[0];
context.document.querySelectorAll = (selector) => (
  selector === 'cast-player-embed iframe' ? [firstIframe, secondIframe] : []
);

context.dispatchEvent({
  type: 'message',
  data: {type: 'omegaflow:player-playing', version: 1},
  source: secondIframe.contentWindow,
});

if (
  firstIframe.contentWindow.messages.length !== 1 ||
  firstIframe.contentWindow.messages[0].message.type !== 'omegaflow:player-pause' ||
  firstIframe.contentWindow.messages[0].targetOrigin !== 'https://example.test' ||
  secondIframe.contentWindow.messages.length !== 0
) {
  console.error(JSON.stringify({
    first: firstIframe.contentWindow.messages,
    second: secondIframe.contentWindow.messages,
  }));
  process.exit(1);
}

second.disconnectedCallback();
if (
  secondIframe.contentWindow.messages.length !== 1 ||
  secondIframe.contentWindow.messages[0].message.type !== 'omegaflow:player-pause'
) {
  console.error(JSON.stringify({disconnected: secondIframe.contentWindow.messages}));
  process.exit(1);
}
"""
    )

    assert result.returncode == 0, result.stderr


def test_homepage_opens_player_paused() -> None:
    homepage = (REPO_ROOT / "website/src/pages/index.js").read_text()

    assert "<VideoPlayer" in homepage
    assert 'autoplay="countdown"' not in homepage


def test_homepage_uses_full_height_player_on_tall_desktops() -> None:
    stylesheet = (REPO_ROOT / "website/src/css/custom.css").read_text()

    assert "@media (min-width: 997px) and (min-height: 650px)" in stylesheet
    assert ".homeHero__video .video-player iframe" in stylesheet
    assert "height: 422px" in stylesheet
