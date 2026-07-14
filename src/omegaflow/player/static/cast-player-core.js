(function (global) {
  'use strict';

  const defaultAudioBoundaryEpsilonSeconds = 0.05;

  function createCastAudioTimeline(segments = [], options = {}) {
    const audioBoundaryEpsilonSeconds = Number.isFinite(options.audioBoundaryEpsilonSeconds)
      ? options.audioBoundaryEpsilonSeconds
      : defaultAudioBoundaryEpsilonSeconds;

    function segmentForPresentation(seconds) {
      return segments.find((segment) => (
        seconds >= segment.presentationStart &&
        seconds < segment.presentationEnd
      )) || null;
    }

    function nextSegmentAfter(seconds) {
      return segments.find(
        (segment) => segment.presentationStart > seconds,
      ) || null;
    }

    function audioTimeForPresentation(seconds, fallbackDuration = null) {
      const segment = segmentForPresentation(seconds);
      if (segment) {
        return Math.max(
          0,
          Math.min(
            segment.audioEnd,
            segment.audioStart + (seconds - segment.presentationStart),
          ),
        );
      }
      const next = nextSegmentAfter(seconds);
      if (next) {
        return next.audioStart;
      }
      const previous = [...segments]
        .reverse()
        .find((candidate) => seconds >= candidate.presentationEnd);
      if (previous) {
        return previous.audioEnd;
      }
      return Math.min(seconds, fallbackDuration || seconds);
    }

    function advanceBoundary(seconds, segment) {
      if (!segment) {
        return seconds;
      }
      if (seconds < segment.presentationEnd - audioBoundaryEpsilonSeconds) {
        return seconds;
      }
      if (seconds < segment.presentationEnd) {
        return segment.presentationEnd;
      }
      return seconds;
    }

    return {
      advanceBoundary,
      audioTimeForPresentation,
      nextSegmentAfter,
      segmentForPresentation,
    };
  }

  function requirePresentation(condition, message) {
    if (!condition) {
      throw new Error(`invalid presentation manifest: ${message}`);
    }
  }

  function validatePresentationManifest(manifest) {
    requirePresentation(manifest && typeof manifest === 'object', 'expected an object');
    requirePresentation(manifest.manifest_version === 1, 'manifest_version must be 1');
    requirePresentation(manifest.recording && typeof manifest.recording === 'object', 'recording is required');
    requirePresentation(Number.isInteger(manifest.recording.duration_ms), 'recording duration must be an integer');
    requirePresentation(manifest.recording.duration_ms >= 0, 'recording duration must be non-negative');
    requirePresentation(Array.isArray(manifest.beats) && manifest.beats.length > 0, 'beats are required');
    requirePresentation(manifest.renderers && typeof manifest.renderers === 'object', 'renderers are required');

    let expectedOffset = 0;
    const usedRenderers = new Set();
    for (const beat of manifest.beats) {
      requirePresentation(beat && typeof beat === 'object', 'beat must be an object');
      requirePresentation(typeof beat.id === 'string' && beat.id, 'beat id is required');
      requirePresentation(['terminal', 'browser'].includes(beat.renderer), `unsupported renderer ${beat.renderer}`);
      requirePresentation(Number.isInteger(beat.offset_ms), `beat ${beat.id} offset must be an integer`);
      requirePresentation(Number.isInteger(beat.duration_ms) && beat.duration_ms >= 0, `beat ${beat.id} duration is invalid`);
      requirePresentation(beat.offset_ms === expectedOffset, `beat ${beat.id} is not contiguous`);
      requirePresentation(typeof beat.payload === 'string' && beat.payload, `beat ${beat.id} payload is required`);
      expectedOffset += beat.duration_ms;
      usedRenderers.add(beat.renderer);
    }
    requirePresentation(expectedOffset === manifest.recording.duration_ms, 'final beat end does not match duration');
    const declaredRenderers = Object.keys(manifest.renderers).sort();
    requirePresentation(
      JSON.stringify(declaredRenderers) === JSON.stringify([...usedRenderers].sort()),
      'renderer header does not match beats',
    );
    for (const renderer of declaredRenderers) {
      requirePresentation(manifest.renderers[renderer].payload_version === 1, `${renderer} payload version is unsupported`);
    }
    return manifest;
  }

  function beatIndexForPresentation(manifest, globalMs) {
    const beats = manifest.beats;
    const clamped = Math.max(0, Math.min(globalMs, manifest.recording.duration_ms));
    let selected = 0;
    for (let index = 1; index < beats.length; index += 1) {
      if (beats[index].offset_ms > clamped) {
        break;
      }
      selected = index;
    }
    return selected;
  }

  function createPresentationShell(options = {}) {
    const manifest = validatePresentationManifest(options.manifest);
    const rendererFactories = options.rendererFactories || {};
    const loadPayload = options.loadPayload;
    requirePresentation(typeof loadPayload === 'function', 'loadPayload is required');
    const loaded = new Map();
    let playbackRate = 1;
    let disposed = false;
    let currentIndex = null;

    async function rendererAt(index) {
      if (disposed) {
        throw new Error('presentation shell is disposed');
      }
      if (loaded.has(index)) {
        return loaded.get(index);
      }
      const beat = manifest.beats[index];
      const factory = rendererFactories[beat.renderer];
      requirePresentation(typeof factory === 'function', `renderer ${beat.renderer} is unavailable`);
      const renderer = factory();
      requirePresentation(renderer && typeof renderer.load === 'function', `${beat.renderer} renderer has no load method`);
      requirePresentation(typeof renderer.renderAt === 'function', `${beat.renderer} renderer has no renderAt method`);
      const payload = await loadPayload(beat);
      await renderer.load({
        assets: manifest.assets || {},
        beat,
        container: options.container || null,
        payload,
      });
      if (typeof renderer.setPlaybackRate === 'function') {
        renderer.setPlaybackRate(playbackRate);
      }
      loaded.set(index, renderer);
      return renderer;
    }

    async function retain(indices) {
      for (const [index, renderer] of loaded.entries()) {
        if (!indices.has(index)) {
          if (typeof renderer.dispose === 'function') {
            renderer.dispose();
          }
          loaded.delete(index);
        }
      }
    }

    async function preloadAfter(index) {
      const nextIndex = index + 1;
      if (nextIndex >= manifest.beats.length) {
        return;
      }
      const renderer = await rendererAt(nextIndex);
      if (typeof renderer.preload === 'function') {
        await renderer.preload();
      }
    }

    async function renderAt(globalMs) {
      const index = beatIndexForPresentation(manifest, globalMs);
      const beat = manifest.beats[index];
      const renderer = await rendererAt(index);
      currentIndex = index;
      const localMs = Math.max(0, Math.min(globalMs - beat.offset_ms, beat.duration_ms));
      renderer.renderAt(localMs);
      const retained = new Set([index]);
      if (index + 1 < manifest.beats.length) {
        retained.add(index + 1);
      }
      await preloadAfter(index);
      await retain(retained);
      return {beat, index, localMs, renderer};
    }

    function setPlaybackRate(rate) {
      if (!Number.isFinite(rate) || rate <= 0) {
        throw new Error('playback rate must be positive');
      }
      playbackRate = rate;
      for (const renderer of loaded.values()) {
        if (typeof renderer.setPlaybackRate === 'function') {
          renderer.setPlaybackRate(rate);
        }
      }
    }

    function dispose() {
      if (disposed) {
        return;
      }
      disposed = true;
      for (const renderer of loaded.values()) {
        if (typeof renderer.dispose === 'function') {
          renderer.dispose();
        }
      }
      loaded.clear();
      currentIndex = null;
    }

    return {
      dispose,
      manifest,
      preload: () => rendererAt(0).then((renderer) => (
        typeof renderer.preload === 'function' ? renderer.preload() : undefined
      )),
      renderAt,
      setPlaybackRate,
      state: () => ({currentIndex, disposed, playbackRate}),
    };
  }

  function decodeAsciinemaCast(source) {
    const lines = String(source || '').split(/\r?\n/).filter((line) => line !== '');
    if (lines.length === 0) {
      throw new Error('cast is empty');
    }
    const header = JSON.parse(lines[0]);
    if (!header || ![2, 3].includes(header.version)) {
      throw new Error('cast version is unsupported');
    }
    let elapsedMs = 0;
    const events = lines.slice(1).map((line) => {
      const event = JSON.parse(line);
      if (!Array.isArray(event) || event.length < 3 || !Number.isFinite(event[0]) || event[0] < 0) {
        throw new Error('cast event is invalid');
      }
      const nextMs = header.version === 3 ? elapsedMs + (event[0] * 1000) : event[0] * 1000;
      if (header.version === 2 && nextMs < elapsedMs) {
        throw new Error('cast events are not ordered');
      }
      elapsedMs = nextMs;
      return {atMs: elapsedMs, data: event[2], type: event[1]};
    });
    return {events, header};
  }

  function createTerminalRendererAdapter(options = {}) {
    let cast = null;
    let container = null;
    let playbackRate = 1;
    let disposed = false;

    return {
      async load(context) {
        if (disposed) {
          throw new Error('terminal renderer is disposed');
        }
        container = context.container;
        cast = typeof context.payload === 'string'
          ? decodeAsciinemaCast(context.payload)
          : context.payload;
        if (!cast || !cast.header || !Array.isArray(cast.events)) {
          throw new Error('terminal payload is invalid');
        }
      },
      renderAt(localMs) {
        if (!cast || disposed) {
          throw new Error('terminal renderer is not loaded');
        }
        if (typeof options.reset === 'function') {
          options.reset({container, header: cast.header});
        }
        for (const event of cast.events) {
          if (event.atMs > localMs) {
            break;
          }
          if (typeof options.applyEvent === 'function') {
            options.applyEvent({container, event});
          }
        }
      },
      setPlaybackRate(rate) {
        playbackRate = rate;
      },
      async preload() {},
      dispose() {
        if (disposed) {
          return;
        }
        if (typeof options.clear === 'function') {
          options.clear({container});
        }
        cast = null;
        container = null;
        disposed = true;
      },
      state() {
        return {disposed, playbackRate};
      },
    };
  }

  const api = {
    beatIndexForPresentation,
    createPresentationShell,
    createCastAudioTimeline,
    createTerminalRendererAdapter,
    decodeAsciinemaCast,
    defaultAudioBoundaryEpsilonSeconds,
    validatePresentationManifest,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  global.CastPlayerCore = api;
}(typeof globalThis !== 'undefined' ? globalThis : window));
