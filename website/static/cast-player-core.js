(function (global) {
  'use strict';

  const defaultAudioBoundaryEpsilonSeconds = 0.05;
  const defaultAudioDriftToleranceMs = 150;
  const browserDecodedAssetBudgetBytes = 64 * 1024 * 1024;

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

  function createPresentationAudioTimeline(intervals = []) {
    const normalized = intervals.map((interval) => ({
      presentationStartMs: interval.presentation_start_ms,
      presentationEndMs: interval.presentation_end_ms,
      sourceStartMs: interval.source_start_ms,
      sourceEndMs: interval.source_end_ms,
    }));
    function intervalAt(presentationMs) {
      return normalized.find((interval) => (
        presentationMs >= interval.presentationStartMs &&
        presentationMs < interval.presentationEndMs
      )) || null;
    }
    function nextInterval(presentationMs) {
      return normalized.find(
        (interval) => interval.presentationStartMs > presentationMs,
      ) || null;
    }
    function sourceTimeMs(presentationMs) {
      const active = intervalAt(presentationMs);
      if (active) {
        return active.sourceStartMs + (presentationMs - active.presentationStartMs);
      }
      const next = nextInterval(presentationMs);
      if (next) {
        return next.sourceStartMs;
      }
      const previous = [...normalized].reverse().find(
        (interval) => presentationMs >= interval.presentationEndMs,
      );
      return previous ? previous.sourceEndMs : 0;
    }
    return {intervalAt, intervals: normalized, nextInterval, sourceTimeMs};
  }

  function createPresentationAudioController(options = {}) {
    const audio = options.audio;
    if (!audio) {
      throw new Error('presentation audio controller requires an audio element');
    }
    const timeline = createPresentationAudioTimeline(options.intervals || []);
    const toleranceMs = Number.isFinite(options.driftToleranceMs)
      ? Math.max(0, options.driftToleranceMs)
      : defaultAudioDriftToleranceMs;
    const onPlayStarted = typeof options.onPlayStarted === 'function'
      ? options.onPlayStarted
      : () => undefined;
    const onPlayRejected = typeof options.onPlayRejected === 'function'
      ? options.onPlayRejected
      : () => undefined;
    let correctionCount = 0;
    let playAttempt = 0;
    let playPending = false;

    function cancelPendingPlay() {
      playAttempt += 1;
      playPending = false;
    }

    function startPlayback() {
      const attempt = playAttempt + 1;
      playAttempt = attempt;
      let result;
      try {
        result = audio.play();
      } catch (error) {
        onPlayRejected(error);
        return;
      }
      if (!result || typeof result.then !== 'function') {
        onPlayStarted();
        return;
      }
      playPending = true;
      Promise.resolve(result).then(
        () => {
          if (playAttempt !== attempt) {
            return;
          }
          playPending = false;
          onPlayStarted();
        },
        (error) => {
          if (playAttempt !== attempt) {
            return;
          }
          playPending = false;
          onPlayRejected(error);
        },
      );
    }

    function synchronize(presentationMs, state = {}) {
      const active = timeline.intervalAt(presentationMs);
      audio.muted = Boolean(state.muted);
      audio.playbackRate = Number.isFinite(state.playbackRate)
        ? state.playbackRate
        : 1;
      if (!active) {
        cancelPendingPlay();
        audio.pause();
        const sourceMs = timeline.sourceTimeMs(presentationMs);
        if (Math.abs(((audio.currentTime || 0) * 1000) - sourceMs) > toleranceMs) {
          audio.currentTime = sourceMs / 1000;
          correctionCount += 1;
        }
        return {active: false, sourceMs};
      }
      const sourceMs = active.sourceStartMs +
        (presentationMs - active.presentationStartMs);
      const driftMs = ((audio.currentTime || 0) * 1000) - sourceMs;
      if (!Number.isFinite(driftMs) || Math.abs(driftMs) > toleranceMs) {
        audio.currentTime = sourceMs / 1000;
        correctionCount += 1;
      }
      if (state.playing) {
        if (audio.paused && !playPending && typeof audio.play === 'function') {
          startPlayback();
        }
      } else {
        cancelPendingPlay();
        audio.pause();
      }
      return {active: true, driftMs, sourceMs};
    }

    return {
      state: () => ({correctionCount, playPending, toleranceMs}),
      synchronize,
      timeline,
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
    const loading = new Map();
    let playbackRate = 1;
    let disposed = false;
    let currentIndex = null;
    let renderGeneration = 0;
    const decodedAssetBudget = Number.isFinite(options.decodedAssetBudgetBytes)
      ? options.decodedAssetBudgetBytes
      : browserDecodedAssetBudgetBytes;

    function decodedResidencyBytes() {
      let total = 0;
      for (const renderer of loaded.values()) {
        if (typeof renderer.state !== 'function') {
          continue;
        }
        const value = renderer.state().decodedAssetBytes;
        if (Number.isFinite(value) && value > 0) {
          total += value;
        }
      }
      return total;
    }

    async function rendererAt(index) {
      if (disposed) {
        throw new Error('presentation shell is disposed');
      }
      if (loaded.has(index)) {
        return loaded.get(index);
      }
      if (!loading.has(index)) {
        const promise = (async () => {
          const beat = manifest.beats[index];
          const factory = rendererFactories[beat.renderer];
          requirePresentation(typeof factory === 'function', `renderer ${beat.renderer} is unavailable`);
          const renderer = factory();
          requirePresentation(renderer && typeof renderer.load === 'function', `${beat.renderer} renderer has no load method`);
          requirePresentation(typeof renderer.renderAt === 'function', `${beat.renderer} renderer has no renderAt method`);
          let rendererContainer = null;
          try {
            const payload = await loadPayload(beat);
            rendererContainer = typeof options.createRendererContainer === 'function'
              ? options.createRendererContainer({beat, index})
              : options.container || null;
            await renderer.load({
              assets: manifest.assets || {},
              beat,
              container: rendererContainer,
              payload,
              presentation: manifest.presentation || {},
              resolveAsset: options.resolveAsset,
            });
            if (disposed) {
              throw new Error('presentation shell is disposed');
            }
            renderer.__presentationContainer = rendererContainer;
            if (typeof renderer.setPlaybackRate === 'function') {
              renderer.setPlaybackRate(playbackRate);
            }
            loaded.set(index, renderer);
            if (decodedResidencyBytes() > decodedAssetBudget) {
              throw new Error('invalid presentation manifest: browser decoded-asset memory budget exceeded');
            }
            return renderer;
          } catch (error) {
            if (loaded.get(index) === renderer) {
              loaded.delete(index);
            }
            if (typeof renderer.dispose === 'function') {
              renderer.dispose();
            }
            if (typeof options.removeRendererContainer === 'function') {
              options.removeRendererContainer({beat, container: rendererContainer, index});
            }
            throw error;
          }
        })();
        loading.set(index, promise);
        promise.then(
          () => loading.delete(index),
          () => loading.delete(index),
        );
      }
      return loading.get(index);
    }

    async function retain(indices) {
      for (const [index, renderer] of loaded.entries()) {
        if (!indices.has(index)) {
          if (typeof renderer.dispose === 'function') {
            renderer.dispose();
          }
          if (typeof options.removeRendererContainer === 'function') {
            options.removeRendererContainer({
              beat: manifest.beats[index],
              container: renderer.__presentationContainer,
              index,
            });
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
      const generation = ++renderGeneration;
      const index = beatIndexForPresentation(manifest, globalMs);
      const beat = manifest.beats[index];
      const renderer = await rendererAt(index);
      if (generation !== renderGeneration || disposed) {
        return {beat, index, localMs: null, renderer, stale: true};
      }
      currentIndex = index;
      const localMs = Math.max(0, Math.min(globalMs - beat.offset_ms, beat.duration_ms));
      if (typeof options.activateRenderer === 'function') {
        options.activateRenderer({
          beat,
          container: renderer.__presentationContainer,
          index,
          renderer,
        });
      }
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
      renderGeneration += 1;
      for (const renderer of loaded.values()) {
        if (typeof renderer.dispose === 'function') {
          renderer.dispose();
        }
        if (typeof options.removeRendererContainer === 'function') {
          options.removeRendererContainer({
            container: renderer.__presentationContainer,
          });
        }
      }
      loaded.clear();
      loading.clear();
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
      state: () => ({
        currentIndex,
        decodedAssetBudgetBytes: decodedAssetBudget,
        decodedAssetBytes: decodedResidencyBytes(),
        disposed,
        playbackRate,
      }),
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
        const extra = typeof options.state === 'function' ? options.state() : {};
        return {disposed, playbackRate, ...extra};
      },
    };
  }

  function clampUnit(value) {
    return Math.max(0, Math.min(1, value));
  }

  function eventProgress(event, localMs) {
    if (event.end_ms <= event.at_ms) {
      return localMs >= event.at_ms ? 1 : 0;
    }
    return clampUnit((localMs - event.at_ms) / (event.end_ms - event.at_ms));
  }

  function cubicPoint(start, end, curve, progress) {
    const inverse = 1 - progress;
    const startWeight = inverse * inverse * inverse;
    const controlOneWeight = 3 * inverse * inverse * progress;
    const controlTwoWeight = 3 * inverse * progress * progress;
    const endWeight = progress * progress * progress;
    return {
      x: (startWeight * start.x) + (controlOneWeight * curve.x1) +
        (controlTwoWeight * curve.x2) + (endWeight * end.x),
      y: (startWeight * start.y) + (controlOneWeight * curve.y1) +
        (controlTwoWeight * curve.y2) + (endWeight * end.y),
    };
  }

  function minimumJerkProgress(progress) {
    const value = clampUnit(progress);
    return value * value * value * (10 + (value * ((6 * value) - 15)));
  }

  function browserViewportLayout(availableWidth, availableHeight, viewport) {
    if (
      !Number.isFinite(availableWidth) || availableWidth < 0 ||
      !Number.isFinite(availableHeight) || availableHeight < 0 ||
      !viewport || !Number.isFinite(viewport.width) || viewport.width <= 0 ||
      !Number.isFinite(viewport.height) || viewport.height <= 0
    ) {
      throw new Error('browser viewport layout is invalid');
    }
    const scale = Math.min(
      availableWidth / viewport.width,
      availableHeight / viewport.height,
    );
    const width = viewport.width * scale;
    const height = viewport.height * scale;
    return {
      scale,
      width,
      height,
      left: (availableWidth - width) / 2,
      top: (availableHeight - height) / 2,
    };
  }

  function browserWindowLayout(availableWidth, availableHeight, viewport, decoration = {}) {
    const borderWidth = decoration.borderWidth || 0;
    const titlebarHeight = decoration.titlebarHeight || 0;
    const chromeHeight = decoration.chromeHeight || 0;
    if (
      !Number.isFinite(borderWidth) || borderWidth < 0 ||
      !Number.isFinite(titlebarHeight) || titlebarHeight < 0 ||
      !Number.isFinite(chromeHeight) || chromeHeight < 0
    ) {
      throw new Error('browser window decoration is invalid');
    }
    const horizontalDecoration = borderWidth * 2;
    const verticalDecoration = (borderWidth * 2) + titlebarHeight + chromeHeight;
    const nativeWidth = viewport.width + horizontalDecoration;
    const nativeHeight = viewport.height + verticalDecoration;
    const windowLayout = browserViewportLayout(
      availableWidth,
      availableHeight,
      {width: nativeWidth, height: nativeHeight},
    );
    return {
      ...windowLayout,
      contentWidth: viewport.width * windowLayout.scale,
      contentHeight: viewport.height * windowLayout.scale,
      nativeWidth,
      nativeHeight,
    };
  }

  function browserSceneAt(payload, localMs) {
    if (!payload || payload.payload_version !== 1 || !Array.isArray(payload.events)) {
      throw new Error('browser payload is invalid');
    }
    const clampedMs = Math.max(0, Math.min(Number(localMs) || 0, payload.duration_ms));
    const scene = {
      localMs: clampedMs,
      viewport: payload.viewport,
      visual: {kind: 'state', asset: payload.initial_state, transition: 'cut', progress: 1},
      pointer: {...payload.initial_pointer},
      click: null,
      focus: null,
      text: null,
      key: null,
      displayUrl: payload.initial_display_url,
    };
    let previousState = payload.initial_state;
    for (const event of payload.events) {
      if (event.at_ms > clampedMs) {
        continue;
      }
      const progress = eventProgress(event, clampedMs);
      if (event.kind === 'state') {
        scene.visual = {
          kind: 'state',
          asset: event.asset,
          previousAsset: previousState,
          transition: event.transition,
          progress,
        };
        if (progress >= 1) {
          previousState = event.asset;
        }
      } else if (event.kind === 'clip') {
        const trimDuration = Math.max(0, event.trim_end_ms - event.trim_start_ms);
        scene.visual = {
          kind: 'clip',
          asset: event.asset,
          previousAsset: previousState,
          mediaMs: event.trim_start_ms + (trimDuration * progress),
          progress,
        };
      } else if (event.kind === 'scroll') {
        scene.visual = {
          kind: 'scroll',
          startAsset: event.start_asset,
          endAsset: event.end_asset,
          container: event.container,
          start: event.start,
          end: event.end,
          progress,
        };
        if (progress >= 1) {
          previousState = event.end_asset;
        }
      } else if (event.kind === 'pointer_move') {
        scene.pointer = {
          ...cubicPoint(
            event.start,
            event.end,
            event.curve,
            minimumJerkProgress(progress),
          ),
          visible: true,
        };
      } else if (event.kind === 'click') {
        scene.pointer = {...event.point, visible: true};
        scene.click = progress < 1 ? {...event.point, progress, button: event.button} : null;
      } else if (event.kind === 'focus') {
        scene.focus = progress < 1 ? {target: event.target, progress} : null;
      } else if (event.kind === 'text') {
        const characters = Math.round(event.final.length * progress);
        scene.text = progress < 1 ? {
          target: event.target,
          style: event.style,
          mode: event.mode,
          value: event.final.slice(0, characters),
          progress,
        } : null;
      } else if (event.kind === 'key') {
        scene.key = progress < 1 ? {label: event.label, progress} : null;
      } else if (event.kind === 'display_url') {
        scene.displayUrl = event.value;
      }
    }
    return scene;
  }

  function createBrowserRendererAdapter(options = {}) {
    let context = null;
    let payload = null;
    let playbackRate = 1;
    let disposed = false;

    return {
      async load(nextContext) {
        if (disposed) {
          throw new Error('browser renderer is disposed');
        }
        context = nextContext;
        payload = typeof nextContext.payload === 'string'
          ? JSON.parse(nextContext.payload)
          : nextContext.payload;
        browserSceneAt(payload, 0);
        if (typeof options.load === 'function') {
          await options.load({...context, payload});
        }
      },
      renderAt(localMs) {
        if (!payload || disposed) {
          throw new Error('browser renderer is not loaded');
        }
        const scene = browserSceneAt(payload, localMs);
        if (typeof options.render === 'function') {
          options.render({...context, playbackRate, scene});
        }
        return scene;
      },
      setPlaybackRate(rate) {
        playbackRate = rate;
        if (typeof options.setPlaybackRate === 'function') {
          options.setPlaybackRate(rate);
        }
      },
      async preload() {
        if (typeof options.preload === 'function') {
          await options.preload({...context, payload});
        }
      },
      dispose() {
        if (disposed) {
          return;
        }
        if (typeof options.dispose === 'function') {
          options.dispose(context);
        }
        context = null;
        payload = null;
        disposed = true;
      },
      state() {
        const extra = typeof options.state === 'function' ? options.state() : {};
        return {disposed, playbackRate, ...extra};
      },
    };
  }

  function createBrowserDomRenderer(options = {}) {
    const documentObject = options.document || global.document;
    if (!documentObject || typeof documentObject.createElement !== 'function') {
      throw new Error('browser DOM renderer requires a document');
    }
    let context = null;
    let elements = null;
    let playbackRate = 1;
    let decodedAssetBytes = 0;
    let preloadedImages = [];
    let entryTransitionStartMs = 0;
    let windowDecoration = {};
    let resizeObserver = null;
    let lastScene = null;

    function element(tag, className) {
      const value = documentObject.createElement(tag);
      value.className = className;
      return value;
    }

    function assetSource(assetId) {
      const asset = context && context.assets ? context.assets[assetId] : null;
      if (!asset || typeof asset.path !== 'string') {
        throw new Error(`browser asset ${assetId} is unavailable`);
      }
      if (typeof context.resolveAsset === 'function') {
        return context.resolveAsset(assetId, asset);
      }
      return asset.path;
    }

    function setImage(image, assetId) {
      const source = assetSource(assetId);
      if (image.getAttribute('src') !== source) {
        image.setAttribute('src', source);
      }
    }

    function styleBounds(node, bounds) {
      node.style.left = `${bounds.x}px`;
      node.style.top = `${bounds.y}px`;
      node.style.width = `${bounds.width}px`;
      node.style.height = `${bounds.height}px`;
    }

    function applyTextStyle(node, style) {
      const clipping = style.clipping_rect;
      styleBounds(node, clipping);
      node.style.fontFamily = style.font_family;
      node.style.fontSize = `${style.font_size}px`;
      node.style.fontWeight = style.font_weight;
      node.style.fontStyle = style.font_style;
      node.style.lineHeight = `${style.line_height}px`;
      node.style.letterSpacing = `${style.letter_spacing}px`;
      node.style.color = style.color;
      node.style.textAlign = style.text_align;
      node.style.padding = `${style.padding_top}px ${style.padding_right}px ` +
        `${style.padding_bottom}px ${style.padding_left}px`;
    }

    function reducedMotion() {
      return typeof global.matchMedia === 'function' &&
        global.matchMedia('(prefers-reduced-motion: reduce)').matches;
    }

    function rendererContentBox() {
      let width = elements.root.clientWidth;
      let height = elements.root.clientHeight;
      if (typeof global.getComputedStyle === 'function') {
        const style = global.getComputedStyle(elements.root);
        width -= (Number.parseFloat(style.paddingLeft) || 0) +
          (Number.parseFloat(style.paddingRight) || 0);
        height -= (Number.parseFloat(style.paddingTop) || 0) +
          (Number.parseFloat(style.paddingBottom) || 0);
      }
      return {width: Math.max(0, width), height: Math.max(0, height)};
    }

    function renderVisual(scene) {
      const visual = scene.visual;
      elements.primary.hidden = true;
      elements.secondary.hidden = true;
      for (const clip of elements.clips.values()) {
        clip.hidden = true;
      }
      elements.scrollClip.hidden = true;
      if (visual.kind === 'state') {
        setImage(elements.primary, visual.asset);
        elements.primary.hidden = false;
        elements.primary.style.opacity = '1';
        if (
          visual.transition === 'fade' && visual.previousAsset &&
          visual.progress < 1 && !reducedMotion()
        ) {
          setImage(elements.secondary, visual.previousAsset);
          elements.secondary.hidden = false;
          elements.secondary.style.opacity = String(1 - visual.progress);
          elements.primary.style.opacity = String(visual.progress);
        }
      } else if (visual.kind === 'clip') {
        if (visual.previousAsset) {
          setImage(elements.primary, visual.previousAsset);
          elements.primary.hidden = false;
          elements.primary.style.opacity = '1';
        }
        const clip = elements.clips.get(visual.asset);
        if (!clip) {
          throw new Error(`browser clip ${visual.asset} is unavailable`);
        }
        clip.muted = true;
        clip.playsInline = true;
        clip.playbackRate = playbackRate;
        clip.hidden = false;
        clip.style.opacity = !Number.isFinite(clip.readyState) || clip.readyState >= 2
          ? '1'
          : '0';
        const targetSeconds = visual.mediaMs / 1000;
        if (
          Number.isFinite(clip.duration) &&
          Math.abs((clip.currentTime || 0) - targetSeconds) > 0.04
        ) {
          clip.currentTime = Math.min(clip.duration, targetSeconds);
        }
        clip.pause();
      } else if (visual.kind === 'scroll') {
        const asset = visual.progress >= 1 ? visual.endAsset : visual.startAsset;
        setImage(elements.primary, asset);
        elements.primary.hidden = false;
        if (visual.progress < 1) {
          styleBounds(elements.scrollClip, visual.container);
          elements.scrollClip.hidden = false;
          setImage(elements.scrollImage, visual.startAsset);
          elements.scrollImage.style.width = `${scene.viewport.width}px`;
          elements.scrollImage.style.height = `${scene.viewport.height}px`;
          elements.scrollImage.style.left = `${-visual.container.x}px`;
          elements.scrollImage.style.top = `${-visual.container.y}px`;
          const x = (visual.end.x - visual.start.x) * visual.progress;
          const y = (visual.end.y - visual.start.y) * visual.progress;
          elements.scrollImage.style.transform = `translate(${-x}px, ${-y}px)`;
        }
      }
    }

    function renderOverlay(scene) {
      elements.focus.hidden = !scene.focus;
      if (scene.focus) {
        styleBounds(elements.focus, scene.focus.target);
        elements.focus.style.opacity = String(1 - scene.focus.progress);
      }
      elements.text.hidden = !scene.text;
      if (scene.text) {
        applyTextStyle(elements.text, scene.text.style);
        elements.text.textContent = scene.text.value;
      } else {
        elements.text.textContent = '';
      }
      elements.pointer.hidden = !scene.pointer.visible;
      if (scene.pointer.visible) {
        elements.pointer.style.transform = `translate(${scene.pointer.x}px, ${scene.pointer.y}px)`;
      }
      elements.click.hidden = !scene.click;
      if (scene.click) {
        elements.click.style.left = `${scene.click.x}px`;
        elements.click.style.top = `${scene.click.y}px`;
        elements.click.style.opacity = String(1 - scene.click.progress);
        elements.click.style.transform = `translate(-50%, -50%) scale(${0.5 + scene.click.progress})`;
      }
      elements.key.hidden = !scene.key;
      if (scene.key) {
        elements.key.textContent = scene.key.label;
        elements.key.style.opacity = String(Math.sin(Math.PI * scene.key.progress));
      }
      elements.url.textContent = scene.displayUrl || '';
    }

    function applyEntryTransition(scene) {
      const transition = context.beat.transition_in;
      const animatedEntry = transition === 'fade' || transition === 'window-open';
      if (animatedEntry && scene.localMs < entryTransitionStartMs) {
        elements.layout.style.opacity = '0';
        elements.layout.style.transform = 'none';
        return;
      }
      const progress = clampUnit((scene.localMs - entryTransitionStartMs) / 300);
      if (reducedMotion() || transition === null || transition === 'cut') {
        elements.layout.style.opacity = '1';
        elements.layout.style.transform = 'none';
      } else if (transition === 'fade') {
        elements.layout.style.opacity = String(progress);
        elements.layout.style.transform = 'none';
      } else if (transition === 'window-open') {
        elements.layout.style.opacity = String(progress);
        elements.layout.style.transform = `scale(${0.92 + (0.08 * progress)})`;
      }
    }

    function renderBrowserScene(scene) {
      const available = rendererContentBox();
      const layout = browserWindowLayout(
        available.width,
        available.height,
        scene.viewport,
        windowDecoration,
      );
      elements.layout.style.width = `${layout.width}px`;
      elements.layout.style.height = `${layout.height}px`;
      elements.window.style.width = `${layout.nativeWidth}px`;
      elements.window.style.height = `${layout.nativeHeight}px`;
      elements.window.style.transform = `scale(${layout.scale})`;
      elements.host.style.width = `${scene.viewport.width}px`;
      elements.host.style.height = `${scene.viewport.height}px`;
      elements.viewport.style.width = `${scene.viewport.width}px`;
      elements.viewport.style.height = `${scene.viewport.height}px`;
      elements.viewport.style.left = '0px';
      elements.viewport.style.top = '0px';
      elements.viewport.style.transform = 'none';
      renderVisual(scene);
      renderOverlay(scene);
      applyEntryTransition(scene);
    }

    const adapter = createBrowserRendererAdapter({
      async load(nextContext) {
        context = nextContext;
        const firstVisualEvent = nextContext.payload.events.find(
          (event) => ['state', 'clip', 'scroll'].includes(event.kind),
        );
        entryTransitionStartMs = firstVisualEvent ? firstVisualEvent.at_ms : 0;
        const viewportConfig = nextContext.payload.viewport;
        const scale = viewportConfig.device_scale_factor || 1;
        decodedAssetBytes = Math.round(
          viewportConfig.width * viewportConfig.height * scale * scale * 4 * 4,
        );
        const browserPresentation = nextContext.presentation.browser || {};
        const windowConfig = browserPresentation.window || {mode: 'none'};
        const chromeConfig = browserPresentation.chrome || {mode: 'hidden'};
        windowDecoration = {
          borderWidth: windowConfig.mode === 'framed' ? 1 : 0,
          titlebarHeight: windowConfig.mode === 'framed' ? 30 : 0,
          chromeHeight: chromeConfig.mode === 'hidden' ? 0 : 38,
        };
        const root = element('div', 'browser-renderer');
        const windowLayout = element('div', 'browser-window-layout');
        const windowFrame = element('div', 'browser-window');
        windowFrame.dataset.mode = windowConfig.mode || 'none';
        windowFrame.dataset.theme = windowConfig.theme || 'kde-breeze';
        const titlebar = element('div', 'browser-window-titlebar');
        titlebar.hidden = windowConfig.mode !== 'framed';
        const controls = element('span', 'browser-window-controls');
        controls.setAttribute('aria-hidden', 'true');
        controls.textContent = '●  ●  ●';
        const title = element('span', 'browser-window-title');
        title.textContent = windowConfig.title || '';
        titlebar.append(controls, title);
        const chrome = element('div', 'browser-chrome');
        chrome.dataset.mode = chromeConfig.mode || 'hidden';
        chrome.hidden = chromeConfig.mode === 'hidden';
        const navigation = element('span', 'browser-chrome-navigation');
        navigation.setAttribute('aria-hidden', 'true');
        navigation.textContent = '‹  ›  ↻';
        const url = element('span', 'browser-chrome-url');
        chrome.append(navigation, url);
        const host = element('div', 'browser-viewport-host');
        const viewport = element('div', 'browser-viewport');
        const primary = element('img', 'browser-state browser-state-primary');
        const secondary = element('img', 'browser-state browser-state-secondary');
        const clips = new Map();
        for (const event of nextContext.payload.events) {
          if (event.kind !== 'clip' || clips.has(event.asset)) {
            continue;
          }
          const clip = element('video', 'browser-clip');
          clip.muted = true;
          clip.playsInline = true;
          clip.preload = 'auto';
          clip.hidden = true;
          clip.setAttribute('muted', '');
          clip.setAttribute('playsinline', '');
          clip.setAttribute('preload', 'auto');
          clip.setAttribute('src', assetSource(event.asset));
          clips.set(event.asset, clip);
        }
        const scrollClip = element('div', 'browser-scroll-clip');
        const scrollImage = element('img', 'browser-scroll-image');
        scrollClip.append(scrollImage);
        const focus = element('div', 'browser-focus');
        const text = element('div', 'browser-text-overlay');
        const pointer = element('div', 'browser-pointer');
        const click = element('div', 'browser-click-feedback');
        const key = element('div', 'browser-key-feedback');
        viewport.append(
          secondary, primary, ...clips.values(), scrollClip, focus, text, pointer,
          click, key,
        );
        host.append(viewport);
        windowFrame.append(titlebar, chrome, host);
        windowLayout.append(windowFrame);
        root.append(windowLayout);
        nextContext.container.replaceChildren(root);
        elements = {
          root,
          layout: windowLayout,
          window: windowFrame,
          chrome,
          url,
          host,
          viewport,
          primary,
          secondary,
          clips,
          scrollClip,
          scrollImage,
          focus,
          text,
          pointer,
          click,
          key,
        };
        if (typeof global.ResizeObserver === 'function') {
          resizeObserver = new global.ResizeObserver(() => {
            if (lastScene) {
              renderBrowserScene(lastScene);
            }
          });
          resizeObserver.observe(root);
        }
      },
      render({scene}) {
        lastScene = scene;
        renderBrowserScene(scene);
      },
      setPlaybackRate(rate) {
        playbackRate = rate;
        if (elements) {
          for (const clip of elements.clips.values()) {
            clip.playbackRate = rate;
          }
        }
      },
      async preload({payload}) {
        const imageAssets = new Set([payload.initial_state]);
        for (const event of payload.events) {
          if (event.kind === 'state') {
            imageAssets.add(event.asset);
          } else if (event.kind === 'scroll') {
            imageAssets.add(event.start_asset);
            imageAssets.add(event.end_asset);
          }
        }
        preloadedImages = typeof global.Image === 'function'
          ? [...imageAssets].map((assetId) => {
              const image = new global.Image();
              image.src = assetSource(assetId);
              return image;
            })
          : [];
        const imageLoads = preloadedImages.map(async (image) => {
          if (typeof image.decode === 'function') {
            await image.decode().catch(() => {});
          }
        });
        const clipLoads = [...elements.clips.values()].map((clip) => {
          if (!Number.isFinite(clip.readyState) || clip.readyState >= 2) {
            return Promise.resolve();
          }
          if (typeof clip.addEventListener !== 'function') {
            if (typeof clip.load === 'function') {
              clip.load();
            }
            return Promise.resolve();
          }
          return new Promise((resolve) => {
            let timer = null;
            const finish = () => {
              clip.removeEventListener('loadeddata', finish);
              clip.removeEventListener('error', finish);
              if (timer !== null) {
                global.clearTimeout(timer);
              }
              resolve();
            };
            clip.addEventListener('loadeddata', finish, {once: true});
            clip.addEventListener('error', finish, {once: true});
            timer = global.setTimeout(finish, 3000);
            if (typeof clip.load === 'function') {
              clip.load();
            }
          });
        });
        await Promise.all([...imageLoads, ...clipLoads]);
      },
      dispose() {
        if (resizeObserver) {
          resizeObserver.disconnect();
        }
        if (elements) {
          for (const clip of elements.clips.values()) {
            clip.pause();
          }
          elements.root.remove();
        }
        context = null;
        elements = null;
        decodedAssetBytes = 0;
        preloadedImages = [];
        entryTransitionStartMs = 0;
        windowDecoration = {};
        resizeObserver = null;
        lastScene = null;
      },
      state: () => ({decodedAssetBytes}),
    });
    return adapter;
  }

  const api = {
    beatIndexForPresentation,
    browserSceneAt,
    browserDecodedAssetBudgetBytes,
    browserViewportLayout,
    browserWindowLayout,
    createBrowserRendererAdapter,
    createBrowserDomRenderer,
    createPresentationAudioController,
    createPresentationAudioTimeline,
    createPresentationShell,
    createCastAudioTimeline,
    createTerminalRendererAdapter,
    decodeAsciinemaCast,
    defaultAudioBoundaryEpsilonSeconds,
    defaultAudioDriftToleranceMs,
    validatePresentationManifest,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  global.CastPlayerCore = api;
}(typeof globalThis !== 'undefined' ? globalThis : window));
