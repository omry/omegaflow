(function (global) {
  'use strict';

  const observedAttributes = [
    'audio',
    'audio-meta',
    'intro',
    'intro-segment',
    'intro-seconds',
    'player',
    'src',
    'title',
  ];

  function optionalAttribute(element, name) {
    const value = element.getAttribute(name);
    return value == null || value === '' ? null : value;
  }

  function buildCastPlayerUrl(options = {}) {
    const player = options.player || 'cast-player.html';
    const params = new URLSearchParams({
      cast: options.src || '',
      title: options.title || 'Terminal recording',
    });
    if (options.audio) {
      params.set('audio', options.audio);
    }
    if (options.audioMeta) {
      params.set('audioMeta', options.audioMeta);
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
      audio: optionalAttribute(element, 'audio'),
      audioMeta: optionalAttribute(element, 'audio-meta'),
      intro: optionalAttribute(element, 'intro'),
      introSegment: optionalAttribute(element, 'intro-segment'),
      introSeconds: optionalAttribute(element, 'intro-seconds'),
      player: optionalAttribute(element, 'player'),
      src: optionalAttribute(element, 'src'),
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

      attributeChangedCallback() {
        if (this.isConnected) {
          renderCastPlayerEmbed(this);
        }
      }
    }

    global.customElements.define('cast-player-embed', CastPlayerEmbedElement);
  }
}(typeof globalThis !== 'undefined' ? globalThis : window));
