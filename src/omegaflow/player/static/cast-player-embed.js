(function (global) {
  'use strict';

  const observedAttributes = [
    'autoplay',
    'intro',
    'intro-segment',
    'intro-seconds',
    'layout',
    'manifest',
    'player',
    'title',
  ];
  const playerPlayingMessage = 'omegaflow:player-playing';
  const playerPauseMessage = 'omegaflow:player-pause';

  function optionalAttribute(element, name) {
    const value = element.getAttribute(name);
    return value == null || value === '' ? null : value;
  }

  function buildCastPlayerUrl(options = {}) {
    const player = options.player || 'cast-player.html';
    const params = new URLSearchParams();
    params.set('manifest', options.manifest || '');
    params.set('embed', '1');
    if (options.autoplay) {
      params.set('autoplay', options.autoplay);
    }
    if (options.layout) {
      params.set('layout', options.layout);
    }
    if (options.title) {
      params.set('title', options.title || 'Terminal recording');
    }
    if (options.intro) {
      params.set('intro', options.intro);
    }
    if (options.introSegment) {
      params.set('introSegment', options.introSegment);
    }
    if (options.introSeconds != null && options.introSeconds !== '') {
      params.set('introSeconds', String(options.introSeconds));
    }
    return `${player}?${params.toString()}`;
  }

  function iframeOptionsFromElement(element) {
    return {
      autoplay: optionalAttribute(element, 'autoplay'),
      intro: optionalAttribute(element, 'intro'),
      introSegment: optionalAttribute(element, 'intro-segment'),
      introSeconds: optionalAttribute(element, 'intro-seconds'),
      layout: optionalAttribute(element, 'layout'),
      manifest: optionalAttribute(element, 'manifest'),
      player: optionalAttribute(element, 'player'),
      title: optionalAttribute(element, 'title'),
    };
  }

  function createCastPlayerIframe(options = {}) {
    const iframe = document.createElement('iframe');
    iframe.title = options.title || 'Terminal recording';
    iframe.src = buildCastPlayerUrl(options);
    iframe.loading = 'lazy';
    iframe.allow = 'autoplay';
    iframe.allowFullscreen = true;
    return iframe;
  }

  function castPlayerIframes() {
    if (typeof document.querySelectorAll !== 'function') {
      return [];
    }
    return [...document.querySelectorAll('cast-player-embed iframe')];
  }

  function postPlayerMessage(iframe, type) {
    if (!iframe?.contentWindow || typeof iframe.contentWindow.postMessage !== 'function') {
      return;
    }
    let targetOrigin = '*';
    try {
      const base = document.baseURI || global.location?.href;
      const origin = new URL(iframe.src, base).origin;
      if (origin && origin !== 'null') {
        targetOrigin = origin;
      }
    } catch (_error) {
      // A relative or custom player URL can still receive a best-effort command.
    }
    iframe.contentWindow.postMessage({type, version: 1}, targetOrigin);
  }

  function pauseCastPlayerEmbed(element) {
    postPlayerMessage(element.querySelector('iframe'), playerPauseMessage);
  }

  function coordinateEmbeddedPlayerAudio(event) {
    if (
      event?.data?.type !== playerPlayingMessage ||
      event.data.version !== 1
    ) {
      return;
    }
    const iframes = castPlayerIframes();
    if (!iframes.some((iframe) => iframe.contentWindow === event.source)) {
      return;
    }
    for (const iframe of iframes) {
      if (iframe.contentWindow !== event.source) {
        postPlayerMessage(iframe, playerPauseMessage);
      }
    }
  }

  function hrefFor(value) {
    try {
      const base = document.baseURI || global.location?.href || window.location?.href;
      return new URL(value, base).href;
    } catch (_error) {
      return value;
    }
  }

  function renderCastPlayerEmbed(element) {
    const options = iframeOptionsFromElement(element);
    const nextSrc = buildCastPlayerUrl(options);
    const nextTitle = options.title || 'Terminal recording';
    const existing = element.querySelector('iframe');
    if (existing) {
      if (hrefFor(existing.src) !== hrefFor(nextSrc)) {
        existing.src = nextSrc;
      }
      if (existing.title !== nextTitle) {
        existing.title = nextTitle;
      }
      existing.loading = 'lazy';
      existing.allow = 'autoplay';
      existing.allowFullscreen = true;
      return;
    }
    element.textContent = '';
    element.appendChild(createCastPlayerIframe(options));
  }

  const api = {
    buildCastPlayerUrl,
    createCastPlayerIframe,
    pauseCastPlayerEmbed,
    renderCastPlayerEmbed,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  global.CastPlayerEmbed = api;
  if (
    typeof global.customElements !== 'undefined' &&
    typeof global.HTMLElement !== 'undefined' &&
    !global.customElements.get('cast-player-embed')
  ) {
    class CastPlayerEmbedElement extends global.HTMLElement {
      static get observedAttributes() {
        return observedAttributes;
      }

      connectedCallback() {
        renderCastPlayerEmbed(this);
      }

      disconnectedCallback() {
        pauseCastPlayerEmbed(this);
      }

      attributeChangedCallback() {
        if (this.isConnected) {
          renderCastPlayerEmbed(this);
        }
      }
    }

    global.customElements.define('cast-player-embed', CastPlayerEmbedElement);
  }
  if (
    typeof global.addEventListener === 'function' &&
    !global.__omegaflowCastPlayerAudioCoordinator
  ) {
    global.__omegaflowCastPlayerAudioCoordinator = true;
    global.addEventListener('message', coordinateEmbeddedPlayerAudio);
  }
}(typeof globalThis !== 'undefined' ? globalThis : window));
