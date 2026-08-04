(function (global) {
  'use strict';

  const defaultAudioBoundaryEpsilonSeconds = 0.05;
  const defaultAudioDriftToleranceMs = 150;
  const browserDecodedAssetBudgetBytes = 64 * 1024 * 1024;
  const browserMediaDiagnosticEventLimit = 200;
  const browserMediaDiagnosticSampleLimit = 600;
  const presentationPaneLimit = 64;
  const presentationItemLimit = 100000;
  const terminalBoundaryOutputByteLimit = 8 * 1024 * 1024;
  const visualizationTextLimit = 100000;
  const visualizationTokenLimit = 10000;
  const visualizationTokenKinds = new Set([
    'key',
    'string',
    'number',
    'boolean',
    'comment',
    'keyword',
    'operator',
    'punctuation',
  ]);
  const visualizationPayloadKeys = [
    'beat_id',
    'duration_ms',
    'highlights',
    'language',
    'payload_version',
    'text',
    'tokens',
  ];

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

  function createPresentationAudioDeck(takes = []) {
    if (!Array.isArray(takes) || takes.length === 0) {
      throw new Error('presentation audio deck requires at least one take');
    }
    const normalized = takes.map((take) => ({
      audio: take.audio,
      id: String(take.id || ''),
      sourceEndMs: Number(take.source_end_ms),
      sourceStartMs: Number(take.source_start_ms),
    }));
    for (let index = 0; index < normalized.length; index += 1) {
      const take = normalized[index];
      const expectedStart = index === 0 ? 0 : normalized[index - 1].sourceEndMs;
      if (
        !take.audio || !take.id || !Number.isFinite(take.sourceStartMs) ||
        !Number.isFinite(take.sourceEndMs) || take.sourceStartMs !== expectedStart ||
        take.sourceEndMs <= take.sourceStartMs
      ) {
        throw new Error('presentation audio deck take is invalid');
      }
    }
    let activeIndex = 0;
    let muted = false;
    let playbackRate = 1;
    let playing = false;
    const listeners = new Map();

    function emit(type, event) {
      for (const listener of listeners.get(type) || []) {
        listener(event);
      }
    }

    function continuePlayback() {
      if (!playing) {
        return;
      }
      try {
        const result = normalized[activeIndex].audio.play();
        if (result && typeof result.catch === 'function') {
          result.catch(() => undefined);
        }
      } catch (_error) {
        // The presentation audio controller retries rejected playback.
      }
    }

    function select(globalSeconds) {
      const sourceMs = Math.max(
        0,
        Math.min(Number(globalSeconds || 0) * 1000, normalized.at(-1).sourceEndMs),
      );
      const nextIndex = normalized.findIndex((take, index) => (
        sourceMs >= take.sourceStartMs &&
        (sourceMs < take.sourceEndMs || index === normalized.length - 1)
      ));
      const resolvedIndex = nextIndex < 0 ? normalized.length - 1 : nextIndex;
      const changedTake = resolvedIndex !== activeIndex;
      if (changedTake) {
        normalized[activeIndex].audio.pause();
        activeIndex = resolvedIndex;
      }
      const take = normalized[activeIndex];
      const localSeconds = Math.max(
        0,
        Math.min(
          (sourceMs - take.sourceStartMs) / 1000,
          (take.sourceEndMs - take.sourceStartMs) / 1000,
        ),
      );
      if (Math.abs(Number(take.audio.currentTime || 0) - localSeconds) > 0.001) {
        take.audio.currentTime = localSeconds;
      }
      if (changedTake) {
        continuePlayback();
      }
      return take;
    }

    normalized.forEach((take, index) => {
      take.audio.addEventListener('ended', (event) => {
        if (index === activeIndex && index + 1 < normalized.length) {
          activeIndex += 1;
          normalized[activeIndex].audio.currentTime = 0;
          continuePlayback();
        }
        emit('ended', event);
      });
      take.audio.addEventListener('error', (event) => emit('error', event));
    });

    return {
      addEventListener(type, listener) {
        if (!listeners.has(type)) {
          listeners.set(type, new Set());
        }
        listeners.get(type).add(listener);
      },
      get currentTime() {
        const take = normalized[activeIndex];
        return (take.sourceStartMs / 1000) + Number(take.audio.currentTime || 0);
      },
      set currentTime(seconds) {
        select(seconds);
      },
      get duration() {
        return normalized.at(-1).sourceEndMs / 1000;
      },
      get muted() {
        return muted;
      },
      set muted(value) {
        muted = Boolean(value);
        for (const take of normalized) {
          take.audio.muted = muted;
        }
      },
      pause() {
        playing = false;
        for (const take of normalized) {
          take.audio.pause();
        }
      },
      get paused() {
        return normalized[activeIndex].audio.paused;
      },
      play() {
        playing = true;
        return normalized[activeIndex].audio.play();
      },
      get playbackRate() {
        return playbackRate;
      },
      set playbackRate(value) {
        playbackRate = Number(value);
        for (const take of normalized) {
          take.audio.playbackRate = playbackRate;
        }
      },
      state() {
        return {activeTakeId: normalized[activeIndex].id};
      },
    };
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
        if (Math.abs(((audio.currentTime || 0) * 1000) - sourceMs) > 1) {
          audio.currentTime = sourceMs / 1000;
          correctionCount += 1;
        }
        return {active: false, sourceMs};
      }
      const sourceMs = active.sourceStartMs +
        (presentationMs - active.presentationStartMs);
      const driftMs = ((audio.currentTime || 0) * 1000) - sourceMs;
      const positioning = !state.playing || (audio.paused && !playPending);
      if (
        positioning &&
        (!Number.isFinite(driftMs) || Math.abs(driftMs) > toleranceMs)
      ) {
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
    requirePresentation(
      typeof manifest.signatures === 'string' && manifest.signatures,
      'signatures sidecar is required',
    );
    requirePresentation(manifest.recording && typeof manifest.recording === 'object', 'recording is required');
    requirePresentation(Number.isInteger(manifest.recording.duration_ms), 'recording duration must be an integer');
    requirePresentation(manifest.recording.duration_ms >= 0, 'recording duration must be non-negative');
    requirePresentation(Array.isArray(manifest.beats) && manifest.beats.length > 0, 'beats are required');
    requirePresentation(Array.isArray(manifest.panes) && manifest.panes.length > 0, 'panes are required');
    requirePresentation(
      manifest.panes.length <= presentationPaneLimit,
      `panes exceeds ${presentationPaneLimit} entries`,
    );
    requirePresentation(manifest.renderers && typeof manifest.renderers === 'object', 'renderers are required');

    const paneById = new Map();
    for (const pane of manifest.panes) {
      requirePresentation(pane && typeof pane === 'object', 'pane must be an object');
      requirePresentation(
        typeof pane.id === 'string' && /^[A-Za-z][A-Za-z0-9_-]*$/.test(pane.id),
        'pane id is invalid',
      );
      requirePresentation(!paneById.has(pane.id), `duplicate pane ${pane.id}`);
      requirePresentation(
        ['visualization', 'terminal', 'browser'].includes(pane.renderer),
        `unsupported renderer ${pane.renderer}`,
      );
      paneById.set(pane.id, pane);
    }
    let expectedOffset = 0;
    const usedRenderers = new Set();
    const usedPanes = new Set();
    const toolbarControls = new Set([
      'previous',
      'play',
      'restart',
      'next',
      'guided',
      'speed',
      'mute',
    ]);
    const paneBeatIdsByPane = new Map(
      manifest.panes.map((pane) => [pane.id, new Set()]),
    );
    let structureCount = manifest.beats.length;
    requirePresentation(
      structureCount <= presentationItemLimit,
      `aggregate structure exceeds ${presentationItemLimit} entries`,
    );
    for (const beat of manifest.beats) {
      requirePresentation(beat && typeof beat === 'object', 'beat must be an object');
      requirePresentation(typeof beat.id === 'string' && beat.id, 'beat id is required');
      requirePresentation(Number.isInteger(beat.offset_ms), `beat ${beat.id} offset must be an integer`);
      requirePresentation(Number.isInteger(beat.duration_ms) && beat.duration_ms >= 0, `beat ${beat.id} duration is invalid`);
      requirePresentation(beat.offset_ms === expectedOffset, `beat ${beat.id} is not contiguous`);
      requirePresentation(
        beat.layout && Array.isArray(beat.layout.areas) && beat.layout.areas.length > 0,
        `beat ${beat.id} layout is required`,
      );
      let columns = null;
      const layoutPanes = new Set();
      for (const row of beat.layout.areas) {
        requirePresentation(Array.isArray(row) && row.length > 0, `beat ${beat.id} layout row is invalid`);
        if (columns == null) {
          columns = row.length;
        }
        requirePresentation(row.length === columns, `beat ${beat.id} layout is not rectangular`);
        structureCount += row.length;
        requirePresentation(
          structureCount <= presentationItemLimit,
          `aggregate structure exceeds ${presentationItemLimit} entries`,
        );
        for (const paneId of row) {
          requirePresentation(paneById.has(paneId), `beat ${beat.id} layout references unknown pane ${paneId}`);
          layoutPanes.add(paneId);
        }
      }
      for (const paneId of layoutPanes) {
        const positions = [];
        for (let rowIndex = 0; rowIndex < beat.layout.areas.length; rowIndex += 1) {
          for (let columnIndex = 0; columnIndex < beat.layout.areas[rowIndex].length; columnIndex += 1) {
            if (beat.layout.areas[rowIndex][columnIndex] === paneId) {
              positions.push([rowIndex, columnIndex]);
            }
          }
        }
        const rows = positions.map(([rowIndex]) => rowIndex);
        const columns = positions.map(([, columnIndex]) => columnIndex);
        for (let rowIndex = Math.min(...rows); rowIndex <= Math.max(...rows); rowIndex += 1) {
          for (let columnIndex = Math.min(...columns); columnIndex <= Math.max(...columns); columnIndex += 1) {
            requirePresentation(
              beat.layout.areas[rowIndex][columnIndex] === paneId,
              `beat ${beat.id} layout area ${paneId} must form a contiguous rectangle`,
            );
          }
        }
      }
      requirePresentation(
        Array.isArray(beat.pane_tracks) && beat.pane_tracks.length > 0,
        `beat ${beat.id} pane tracks are required`,
      );
      structureCount += beat.pane_tracks.length;
      requirePresentation(
        structureCount <= presentationItemLimit,
        `aggregate structure exceeds ${presentationItemLimit} entries`,
      );
      const trackPanes = new Set();
      for (const track of beat.pane_tracks) {
        requirePresentation(
          track && paneById.has(track.pane_id),
          `beat ${beat.id} pane track is invalid`,
        );
        requirePresentation(!trackPanes.has(track.pane_id), `beat ${beat.id} has a duplicate pane track`);
        requirePresentation(['first', 'hidden'].includes(track.initial), `pane ${track.pane_id} initial state is invalid`);
        requirePresentation(Array.isArray(track.beats) && track.beats.length > 0, `pane ${track.pane_id} beats are required`);
        structureCount += track.beats.length;
        requirePresentation(
          structureCount <= presentationItemLimit,
          `aggregate structure exceeds ${presentationItemLimit} entries`,
        );
        trackPanes.add(track.pane_id);
        usedPanes.add(track.pane_id);
        let precedingEnd = 0;
        const paneBeatIds = paneBeatIdsByPane.get(track.pane_id);
        for (const paneBeat of track.beats) {
          requirePresentation(
            paneBeat && typeof paneBeat.id === 'string' && paneBeat.id,
            `pane ${track.pane_id} beat id is required`,
          );
          requirePresentation(!paneBeatIds.has(paneBeat.id), `pane ${track.pane_id} has a duplicate beat`);
          paneBeatIds.add(paneBeat.id);
          requirePresentation(
            Number.isInteger(paneBeat.offset_ms) && paneBeat.offset_ms >= precedingEnd,
            `pane beat ${paneBeat.id} overlaps its predecessor`,
          );
          requirePresentation(
            Number.isInteger(paneBeat.duration_ms) && paneBeat.duration_ms >= 0,
            `pane beat ${paneBeat.id} duration is invalid`,
          );
          requirePresentation(
            paneBeat.offset_ms + paneBeat.duration_ms <= beat.duration_ms,
            `pane beat ${paneBeat.id} exceeds its outer beat`,
          );
          requirePresentation(
            typeof paneBeat.payload === 'string' && paneBeat.payload,
            `pane beat ${paneBeat.id} payload is required`,
          );
          const transition = paneBeat.transition;
          requirePresentation(
            transition && ['cut', 'fade'].includes(transition.kind),
            `pane beat ${paneBeat.id} transition is invalid`,
          );
          requirePresentation(
            Number.isInteger(transition.duration_ms) &&
              transition.duration_ms >= 0 &&
              transition.duration_ms <= paneBeat.duration_ms,
            `pane beat ${paneBeat.id} transition duration is invalid`,
          );
          requirePresentation(
            transition.kind !== 'cut' || transition.duration_ms === 0,
            `pane beat ${paneBeat.id} cut transition must be instantaneous`,
          );
          precedingEnd = paneBeat.offset_ms + paneBeat.duration_ms;
        }
      }
      requirePresentation(
        layoutPanes.size === trackPanes.size &&
          [...layoutPanes].every((paneId) => trackPanes.has(paneId)),
        `beat ${beat.id} layout and pane tracks disagree`,
      );
      if (beat.player != null) {
        requirePresentation(
          beat.player && typeof beat.player === 'object',
          `beat ${beat.id} player must be an object`,
        );
        const highlight = beat.player.highlight;
        requirePresentation(
          highlight && typeof highlight === 'object',
          `beat ${beat.id} player highlight must be an object`,
        );
        requirePresentation(
          toolbarControls.has(highlight.control),
          `beat ${beat.id} player highlight control is unsupported`,
        );
        requirePresentation(
          Number.isInteger(highlight.start_ms) && highlight.start_ms >= 0 &&
            Number.isInteger(highlight.end_ms) &&
            highlight.end_ms > highlight.start_ms &&
            highlight.end_ms <= beat.duration_ms,
          `beat ${beat.id} player highlight timing is invalid`,
        );
      }
      expectedOffset += beat.duration_ms;
    }
    requirePresentation(expectedOffset === manifest.recording.duration_ms, 'final beat end does not match duration');
    requirePresentation(
      usedPanes.size === paneById.size && [...paneById.keys()].every((paneId) => usedPanes.has(paneId)),
      'all panes must be used',
    );
    for (const pane of paneById.values()) {
      usedRenderers.add(pane.renderer);
    }
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
    const paneById = new Map(manifest.panes.map((pane) => [pane.id, pane]));
    const loaded = new Map();
    const loading = new Map();
    let playbackRate = 1;
    let playing = false;
    let muted = false;
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

    function entryAt(outerIndex, trackIndex, paneBeatIndex) {
      const outerBeat = manifest.beats[outerIndex];
      const track = outerBeat.pane_tracks[trackIndex];
      const pane = paneById.get(track.pane_id);
      const beat = track.beats[paneBeatIndex];
      return {
        beat,
        key: `${outerIndex}:${trackIndex}:${paneBeatIndex}`,
        outerBeat,
        outerIndex,
        pane,
        paneBeatIndex,
        track,
        trackIndex,
      };
    }

    async function rendererAt(entry) {
      if (disposed) {
        throw new Error('presentation shell is disposed');
      }
      if (loaded.has(entry.key)) {
        return loaded.get(entry.key);
      }
      if (!loading.has(entry.key)) {
        const promise = (async () => {
          const factory = rendererFactories[entry.pane.renderer];
          requirePresentation(
            typeof factory === 'function',
            `renderer ${entry.pane.renderer} is unavailable`,
          );
          const renderer = factory();
          requirePresentation(
            renderer && typeof renderer.load === 'function',
            `${entry.pane.renderer} renderer has no load method`,
          );
          requirePresentation(
            typeof renderer.renderAt === 'function',
            `${entry.pane.renderer} renderer has no renderAt method`,
          );
          let rendererContainer = null;
          try {
            const payload = await loadPayload(entry.beat, entry);
            rendererContainer = typeof options.createRendererContainer === 'function'
              ? options.createRendererContainer(entry)
              : options.container || null;
            const rendererBeat = {
              ...entry.beat,
              transition_in: entry.paneBeatIndex === 0
                ? entry.outerBeat.transition_in
                : null,
            };
            await renderer.load({
              assets: manifest.assets || {},
              beat: rendererBeat,
              container: rendererContainer,
              outerBeat: entry.outerBeat,
              pane: entry.pane,
              payload,
              presentation: manifest.presentation || {},
              resolveAsset: options.resolveAsset,
              track: entry.track,
            });
            if (disposed) {
              throw new Error('presentation shell is disposed');
            }
            renderer.__presentationContainer = rendererContainer;
            if (typeof renderer.setPlaybackRate === 'function') {
              renderer.setPlaybackRate(playbackRate);
            }
            if (typeof renderer.setPlaying === 'function') {
              renderer.setPlaying(playing);
            }
            if (typeof renderer.setMuted === 'function') {
              renderer.setMuted(muted);
            }
            renderer.__presentationEntry = entry;
            loaded.set(entry.key, renderer);
            if (decodedResidencyBytes() > decodedAssetBudget) {
              throw new Error('invalid presentation manifest: browser decoded-asset memory budget exceeded');
            }
            return renderer;
          } catch (error) {
            if (loaded.get(entry.key) === renderer) {
              loaded.delete(entry.key);
            }
            if (typeof renderer.dispose === 'function') {
              renderer.dispose();
            }
            if (typeof options.removeRendererContainer === 'function') {
              options.removeRendererContainer({...entry, container: rendererContainer});
            }
            throw error;
          }
        })();
        loading.set(entry.key, promise);
        promise.then(
          () => loading.delete(entry.key),
          () => loading.delete(entry.key),
        );
      }
      return loading.get(entry.key);
    }

    async function retain(keys) {
      for (const [key, renderer] of loaded.entries()) {
        if (!keys.has(key)) {
          const entry = renderer.__presentationEntry;
          if (typeof renderer.dispose === 'function') {
            renderer.dispose();
          }
          if (typeof options.removeRendererContainer === 'function') {
            options.removeRendererContainer({
              ...entry,
              container: renderer.__presentationContainer,
            });
          }
          loaded.delete(key);
        }
      }
    }

    async function preloadEntries(entries) {
      for (const entry of entries) {
        const renderer = await rendererAt(entry);
        if (typeof renderer.preload === 'function') {
          await renderer.preload();
        }
      }
    }

    function finalLocalMs(paneBeat) {
      return paneBeat.duration_ms - paneBeat.transition.duration_ms;
    }

    function paneLayersAt(track, localMs) {
      const beats = track.beats;
      const first = beats[0];
      if (localMs < first.offset_ms) {
        return track.initial === 'hidden'
          ? []
          : [{paneBeatIndex: 0, localMs: 0, opacity: 1}];
      }
      for (let paneBeatIndex = 0; paneBeatIndex < beats.length; paneBeatIndex += 1) {
        const beat = beats[paneBeatIndex];
        const previous = paneBeatIndex > 0 ? beats[paneBeatIndex - 1] : null;
        const start = beat.offset_ms;
        const end = start + beat.duration_ms;
        if (localMs < start) {
          return previous == null
            ? []
            : [{
              paneBeatIndex: paneBeatIndex - 1,
              localMs: finalLocalMs(previous),
              opacity: 1,
            }];
        }
        if (localMs < end || paneBeatIndex === beats.length - 1) {
          const transitionDuration = beat.transition.duration_ms;
          const transitionEnd = start + transitionDuration;
          if (transitionDuration > 0 && localMs < transitionEnd) {
            if (previous == null && track.initial === 'first') {
              return [{paneBeatIndex, localMs: 0, opacity: 1}];
            }
            const progress = Math.max(0, Math.min(
              1,
              (localMs - start) / transitionDuration,
            ));
            const layers = [];
            if (previous != null) {
              layers.push({
                paneBeatIndex: paneBeatIndex - 1,
                localMs: finalLocalMs(previous),
                opacity: 1,
              });
            }
            layers.push({paneBeatIndex, localMs: 0, opacity: progress});
            return layers;
          }
          return [{
            paneBeatIndex,
            localMs: Math.max(0, Math.min(
              localMs - transitionEnd,
              finalLocalMs(beat),
            )),
            opacity: 1,
          }];
        }
      }
      const lastIndex = beats.length - 1;
      return [{
        paneBeatIndex: lastIndex,
        localMs: finalLocalMs(beats[lastIndex]),
        opacity: 1,
      }];
    }

    function playbackWindowEntries(outerIndex, localMs, {firstOnly = false} = {}) {
      if (outerIndex < 0 || outerIndex >= manifest.beats.length) {
        return [];
      }
      const outerBeat = manifest.beats[outerIndex];
      const entries = [];
      for (let trackIndex = 0; trackIndex < outerBeat.pane_tracks.length; trackIndex += 1) {
        const track = outerBeat.pane_tracks[trackIndex];
        if (firstOnly) {
          entries.push(entryAt(outerIndex, trackIndex, 0));
          continue;
        }
        const activeIndexes = paneLayersAt(track, localMs)
          .map((layer) => layer.paneBeatIndex);
        const indexes = new Set(activeIndexes);
        const latestIndex = activeIndexes.length > 0
          ? Math.max(...activeIndexes)
          : -1;
        const nextIndex = latestIndex + 1;
        if (nextIndex < track.beats.length) {
          indexes.add(nextIndex);
        }
        if (indexes.size === 0) {
          indexes.add(0);
        }
        for (const paneBeatIndex of indexes) {
          entries.push(entryAt(outerIndex, trackIndex, paneBeatIndex));
        }
      }
      return entries;
    }

    async function renderAt(globalMs) {
      const generation = ++renderGeneration;
      const index = beatIndexForPresentation(manifest, globalMs);
      const beat = manifest.beats[index];
      const localMs = Math.max(0, Math.min(globalMs - beat.offset_ms, beat.duration_ms));
      const panes = [];
      for (let trackIndex = 0; trackIndex < beat.pane_tracks.length; trackIndex += 1) {
        const track = beat.pane_tracks[trackIndex];
        const pane = paneById.get(track.pane_id);
        const layerPlans = paneLayersAt(track, localMs);
        const layers = [];
        for (const layerPlan of layerPlans) {
          const entry = entryAt(index, trackIndex, layerPlan.paneBeatIndex);
          const renderer = await rendererAt(entry);
          layers.push({
            beat: entry.beat,
            container: renderer.__presentationContainer,
            localMs: layerPlan.localMs,
            opacity: layerPlan.opacity,
            renderer,
          });
        }
        if (layers.length > 0) {
          panes.push({layers, pane, track});
        }
      }
      if (generation !== renderGeneration || disposed) {
        return {beat, index, localMs: null, panes, stale: true};
      }
      currentIndex = index;
      for (const pane of panes) {
        for (const layer of pane.layers) {
          layer.renderer.renderAt(layer.localMs);
        }
      }
      if (typeof options.activateComposition === 'function') {
        options.activateComposition({beat, index, layout: beat.layout, localMs, panes});
      }
      const currentEntries = playbackWindowEntries(index, localMs);
      const nextEntries = playbackWindowEntries(index + 1, 0, {firstOnly: true});
      const retained = new Set(currentEntries.map((entry) => entry.key));
      if (index + 1 < manifest.beats.length) {
        for (const entry of nextEntries) {
          retained.add(entry.key);
        }
      }
      await preloadEntries(currentEntries);
      await preloadEntries(nextEntries);
      await retain(retained);
      return {beat, index, localMs, panes};
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

    function setPlaying(nextPlaying) {
      playing = Boolean(nextPlaying);
      for (const renderer of loaded.values()) {
        if (typeof renderer.setPlaying === 'function') {
          renderer.setPlaying(playing);
        }
      }
    }

    function setMuted(nextMuted) {
      muted = Boolean(nextMuted);
      for (const renderer of loaded.values()) {
        if (typeof renderer.setMuted === 'function') {
          renderer.setMuted(muted);
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
          const entry = renderer.__presentationEntry;
          options.removeRendererContainer({
            ...entry,
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
      preload: () => preloadEntries(
        playbackWindowEntries(0, 0, {firstOnly: true}),
      ),
      renderAt,
      setMuted,
      setPlaybackRate,
      setPlaying,
      state: () => ({
        currentIndex,
        decodedAssetBudgetBytes: decodedAssetBudget,
        decodedAssetBytes: decodedResidencyBytes(),
        disposed,
        muted,
        playbackRate,
        playing,
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
    if (lines.length - 1 > presentationItemLimit) {
      throw new Error(`cast events exceeds ${presentationItemLimit} entries`);
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

  function visualizationSegments(payload, localMs = 0) {
    if (!payload || typeof payload !== 'object') {
      throw new Error('visualization payload is invalid');
    }
    if (
      Object.keys(payload).sort().join(',') !== visualizationPayloadKeys.join(',') ||
      payload.payload_version !== 1 ||
      typeof payload.beat_id !== 'string' ||
      !/^[A-Za-z][A-Za-z0-9_-]*$/.test(payload.beat_id) ||
      !Number.isInteger(payload.duration_ms) ||
      payload.duration_ms < 0 ||
      typeof payload.language !== 'string' ||
      !/^[A-Za-z][A-Za-z0-9_+.-]{0,31}$/.test(payload.language) ||
      typeof payload.text !== 'string' ||
      payload.text.length === 0 ||
      Array.from(payload.text).length > visualizationTextLimit ||
      !Array.isArray(payload.highlights) ||
      payload.highlights.length > visualizationTokenLimit ||
      !Array.isArray(payload.tokens) ||
      payload.tokens.length > visualizationTokenLimit
    ) {
      throw new Error('visualization payload is invalid');
    }
    const characters = Array.from(payload.text);
    const resolvedLocalMs = Math.max(
      0,
      Math.min(Number(localMs) || 0, payload.duration_ms),
    );
    const highlights = [];
    for (const highlight of payload.highlights) {
      if (
        !highlight ||
        typeof highlight !== 'object' ||
        Object.keys(highlight).sort().join(',') !== 'color,end,end_ms,start,start_ms' ||
        !Number.isInteger(highlight.start) ||
        !Number.isInteger(highlight.end) ||
        !Number.isInteger(highlight.start_ms) ||
        !Number.isInteger(highlight.end_ms) ||
        highlight.start < 0 ||
        highlight.end <= highlight.start ||
        highlight.end > characters.length ||
        highlight.start_ms < 0 ||
        highlight.end_ms <= highlight.start_ms ||
        highlight.end_ms > payload.duration_ms ||
        !['cue', 'brand'].includes(highlight.color)
      ) {
        throw new Error('visualization highlight range is invalid');
      }
      if (
        resolvedLocalMs >= highlight.start_ms &&
        resolvedLocalMs < highlight.end_ms
      ) {
        highlights.push(highlight);
      }
    }
    highlights.sort((left, right) => left.start - right.start || left.end - right.end);
    for (let index = 1; index < highlights.length; index += 1) {
      if (highlights[index].start < highlights[index - 1].end) {
        throw new Error('active visualization highlights overlap');
      }
    }
    const tokens = [];
    let previousTokenEnd = 0;
    for (const token of payload.tokens) {
      if (
        !token ||
        typeof token !== 'object' ||
        Object.keys(token).sort().join(',') !== 'end,kind,start' ||
        !Number.isInteger(token.start) ||
        !Number.isInteger(token.end) ||
        token.start < previousTokenEnd ||
        token.end <= token.start ||
        token.end > characters.length ||
        !visualizationTokenKinds.has(token.kind)
      ) {
        throw new Error('visualization token range is invalid');
      }
      tokens.push(token);
      previousTokenEnd = token.end;
    }
    const boundaries = new Set([0, characters.length]);
    for (const token of tokens) {
      boundaries.add(token.start);
      boundaries.add(token.end);
    }
    for (const highlight of highlights) {
      boundaries.add(highlight.start);
      boundaries.add(highlight.end);
    }
    const positions = Array.from(boundaries).sort((left, right) => left - right);
    const segments = [];
    let tokenIndex = 0;
    let highlightIndex = 0;
    for (let index = 0; index < positions.length - 1; index += 1) {
      const start = positions[index];
      const end = positions[index + 1];
      if (end <= start) {
        continue;
      }
      while (tokenIndex < tokens.length && tokens[tokenIndex].end <= start) {
        tokenIndex += 1;
      }
      while (
        highlightIndex < highlights.length &&
        highlights[highlightIndex].end <= start
      ) {
        highlightIndex += 1;
      }
      const token = tokens[tokenIndex];
      const highlight = highlights[highlightIndex];
      segments.push({
        kind: token && token.start <= start && token.end >= end
          ? token.kind
          : null,
        highlight: highlight && highlight.start <= start && highlight.end >= end
          ? highlight.color
          : null,
        text: characters.slice(start, end).join(''),
      });
    }
    return segments;
  }

  function createVisualizationRendererAdapter(options = {}) {
    let context = null;
    let payload = null;
    let segments = null;
    let playbackRate = 1;
    let disposed = false;

    return {
      async load(nextContext) {
        if (disposed) {
          throw new Error('visualization renderer is disposed');
        }
        context = nextContext;
        payload = typeof nextContext.payload === 'string'
          ? JSON.parse(nextContext.payload)
          : nextContext.payload;
        segments = visualizationSegments(payload, 0);
        if (typeof options.load === 'function') {
          await options.load({...context, payload, segments});
        }
      },
      renderAt(localMs) {
        if (!segments || disposed) {
          throw new Error('visualization renderer is not loaded');
        }
        const resolvedLocalMs = Math.max(
          0,
          Math.min(Number(localMs) || 0, payload.duration_ms),
        );
        segments = visualizationSegments(payload, resolvedLocalMs);
        if (typeof options.render === 'function') {
          options.render({
            ...context,
            localMs: resolvedLocalMs,
            payload,
            playbackRate,
            segments,
          });
        }
        return segments;
      },
      setPlaybackRate(rate) {
        playbackRate = rate;
      },
      async preload() {},
      dispose() {
        if (disposed) {
          return;
        }
        if (typeof options.dispose === 'function') {
          options.dispose(context);
        }
        context = null;
        payload = null;
        segments = null;
        disposed = true;
      },
      state() {
        return {disposed, playbackRate};
      },
    };
  }

  function createVisualizationDomRenderer(options = {}) {
    const documentObject = options.document || global.document;
    if (!documentObject || typeof documentObject.createElement !== 'function') {
      throw new Error('visualization DOM renderer requires a document');
    }
    let root = null;
    let rendered = '';
    function renderSegments(segments) {
      const signature = JSON.stringify(segments);
      if (!root || signature === rendered) {
        return;
      }
      rendered = signature;
      root.replaceChildren();
      for (const segment of segments) {
        if (segment.kind == null && segment.highlight == null) {
          root.append(documentObject.createTextNode(segment.text));
          continue;
        }
        const token = documentObject.createElement('span');
        token.className = [
          segment.kind == null
            ? ''
            : `visualization-token visualization-token-${segment.kind}`,
          segment.highlight == null
            ? ''
            : 'visualization-text-highlight',
          segment.highlight === 'brand'
            ? 'visualization-text-highlight-brand'
            : '',
        ].filter(Boolean).join(' ');
        if (segment.kind != null) {
          token.dataset.tokenKind = segment.kind;
        }
        if (segment.highlight != null) {
          token.dataset.highlightColor = segment.highlight;
        }
        token.textContent = segment.text;
        root.append(token);
      }
    }
    return createVisualizationRendererAdapter({
      load({container, payload, segments}) {
        if (!container) {
          throw new Error('visualization renderer requires a container');
        }
        root = documentObject.createElement('pre');
        root.className = 'visualization-content';
        root.dataset.language = payload.language;
        container.append(root);
        renderSegments(segments);
      },
      render({segments}) {
        renderSegments(segments);
      },
      dispose() {
        if (root) {
          root.remove();
        }
        root = null;
        rendered = '';
      },
    });
  }

  function createTerminalRendererAdapter(options = {}) {
    let cast = null;
    let container = null;
    let initialState = null;
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
        const boundaryOutput = cast.header.omegaflow_boundary_output;
        if (boundaryOutput !== undefined) {
          if (
            !Array.isArray(boundaryOutput) ||
            boundaryOutput.some((chunk) => typeof chunk !== 'string')
          ) {
            throw new Error('terminal boundary output is invalid');
          }
          if (boundaryOutput.length > presentationItemLimit) {
            throw new Error(
              `terminal boundary output exceeds ${presentationItemLimit} entries`,
            );
          }
          const encoder = new TextEncoder();
          let boundaryOutputBytes = 0;
          for (const chunk of boundaryOutput) {
            boundaryOutputBytes += encoder.encode(chunk).byteLength;
            if (boundaryOutputBytes > terminalBoundaryOutputByteLimit) {
              throw new Error(
                `terminal boundary output exceeds ${terminalBoundaryOutputByteLimit} bytes`,
              );
            }
          }
          if (typeof options.createInitialState !== 'function') {
            throw new Error('terminal boundary state is unsupported');
          }
          initialState = options.createInitialState({
            container,
            header: cast.header,
            output: boundaryOutput,
          });
        }
      },
      renderAt(localMs) {
        if (!cast || disposed) {
          throw new Error('terminal renderer is not loaded');
        }
        if (typeof options.reset === 'function') {
          options.reset({container, header: cast.header});
        }
        if (initialState !== null) {
          if (typeof options.restoreInitialState !== 'function') {
            throw new Error('terminal boundary restore is unsupported');
          }
          options.restoreInitialState({container, state: initialState});
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
        initialState = null;
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
    if (payload.events.length > presentationItemLimit) {
      throw new Error(`browser events exceeds ${presentationItemLimit} entries`);
    }
    const clampedMs = Math.max(0, Math.min(Number(localMs) || 0, payload.duration_ms));
    let pointerVisible = Boolean(payload.initial_pointer.visible);
    const scene = {
      localMs: clampedMs,
      viewport: payload.viewport,
      visual: {kind: 'state', asset: payload.initial_state, transition: 'cut', progress: 1},
      pointer: {...payload.initial_pointer, pressed: false},
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
          hasAudio: event.has_audio === true,
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
      } else if (event.kind === 'pointer_visibility') {
        pointerVisible = Boolean(event.visible);
        scene.pointer = {...scene.pointer, visible: pointerVisible};
      } else if (event.kind === 'pointer_move') {
        scene.pointer = {
          ...cubicPoint(
            event.start,
            event.end,
            event.curve,
            minimumJerkProgress(progress),
          ),
          visible: pointerVisible,
          pressed: false,
        };
      } else if (event.kind === 'drag') {
        scene.pointer = {
          ...cubicPoint(
            event.start,
            event.end,
            event.curve,
            minimumJerkProgress(progress),
          ),
          visible: pointerVisible,
          pressed: progress < 1,
        };
      } else if (event.kind === 'click') {
        scene.pointer = {...event.point, visible: pointerVisible, pressed: false};
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
    let playing = false;
    let muted = true;
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
          options.render({...context, playbackRate, playing, scene});
        }
        return scene;
      },
      setPlaybackRate(rate) {
        playbackRate = rate;
        if (typeof options.setPlaybackRate === 'function') {
          options.setPlaybackRate(rate);
        }
      },
      setPlaying(nextPlaying) {
        playing = Boolean(nextPlaying);
        if (typeof options.setPlaying === 'function') {
          options.setPlaying(playing);
        }
      },
      setMuted(nextMuted) {
        muted = Boolean(nextMuted);
        if (typeof options.setMuted === 'function') {
          options.setMuted(muted);
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
        return {disposed, muted, playbackRate, playing, ...extra};
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
    let playing = false;
    let muted = true;
    let decodedAssetBytes = 0;
    let preloadedImages = [];
    let clipPreloads = new Map();
    let clipsWithDecodedFrames = new Set();
    let entryTransitionStartMs = 0;
    let entryTransitionDurationMs = 300;
    let entryTransition = 'cut';
    let windowDecoration = {};
    let resizeObserver = null;
    let lastScene = null;
    let activeClipAsset = null;
    const clipDiagnostics = new Map();

    function diagnosticNowMs() {
      if (global.performance && typeof global.performance.now === 'function') {
        return Math.round(global.performance.now());
      }
      return Date.now();
    }

    function diagnosticRoot() {
      const existing = global.__omegaflowMediaDiagnostics;
      if (
        existing && existing.version === 1 && Array.isArray(existing.clips)
      ) {
        return existing;
      }
      const created = {version: 1, clips: [], nextClipId: 1};
      global.__omegaflowMediaDiagnostics = created;
      return created;
    }

    function mediaError(error) {
      if (!error) {
        return null;
      }
      return {
        code: Number.isFinite(error.code) ? error.code : null,
        message: String(error.message || ''),
        name: String(error.name || ''),
      };
    }

    function clipState(clip, extra = {}) {
      return {
        atMs: diagnosticNowMs(),
        currentTime: Number(clip.currentTime || 0),
        duration: Number.isFinite(clip.duration) ? clip.duration : null,
        ended: Boolean(clip.ended),
        error: mediaError(clip.error),
        hidden: Boolean(clip.hidden),
        networkState: Number.isFinite(clip.networkState) ? clip.networkState : null,
        paused: Boolean(clip.paused),
        playbackRate: Number(clip.playbackRate || 1),
        readyState: Number.isFinite(clip.readyState) ? clip.readyState : null,
        seeking: Boolean(clip.seeking),
        ...extra,
      };
    }

    function appendLimited(items, value, limit) {
      items.push(value);
      if (items.length > limit) {
        items.splice(0, items.length - limit);
      }
    }

    function recordClipEvent(diagnostic, clip, type, detail = {}) {
      const state = clipState(clip, {type, ...detail});
      diagnostic.last = state;
      appendLimited(
        diagnostic.mediaEvents,
        state,
        browserMediaDiagnosticEventLimit,
      );
    }

    function recordClipSample(diagnostic, clip, detail) {
      diagnostic.sampleCount += 1;
      const state = clipState(clip, detail);
      diagnostic.last = state;
      appendLimited(
        diagnostic.samples,
        state,
        browserMediaDiagnosticSampleLimit,
      );
    }

    function registerClipDiagnostic(assetId, clip, source) {
      const root = diagnosticRoot();
      const diagnostic = {
        id: root.nextClipId,
        assetId,
        beatId: String(context?.beat?.id || context?.payload?.beat_id || ''),
        createdAtMs: diagnosticNowMs(),
        disposed: false,
        mediaEvents: [],
        playAttempts: 0,
        playRejections: [],
        playResolutions: 0,
        sampleCount: 0,
        samples: [],
        source,
        last: null,
      };
      root.nextClipId += 1;
      root.clips.push(diagnostic);
      clipDiagnostics.set(assetId, diagnostic);
      if (typeof clip.addEventListener === 'function') {
        for (const type of [
          'canplay', 'ended', 'error', 'loadeddata', 'pause', 'play', 'playing',
          'seeked', 'seeking', 'stalled', 'suspend', 'waiting',
        ]) {
          clip.addEventListener(type, () => {
            recordClipEvent(diagnostic, clip, type);
          });
        }
      }
      recordClipEvent(diagnostic, clip, 'created');
      return diagnostic;
    }

    function playClip(clip, diagnostic) {
      diagnostic.playAttempts += 1;
      recordClipEvent(diagnostic, clip, 'play-attempt');
      let playResult;
      try {
        playResult = clip.play();
      } catch (error) {
        const rejection = {
          atMs: diagnosticNowMs(),
          ...mediaError(error),
        };
        appendLimited(
          diagnostic.playRejections,
          rejection,
          browserMediaDiagnosticEventLimit,
        );
        recordClipEvent(diagnostic, clip, 'play-rejected', {rejection});
        throw error;
      }
      if (playResult && typeof playResult.then === 'function') {
        playResult.then(() => {
          diagnostic.playResolutions += 1;
          recordClipEvent(diagnostic, clip, 'play-resolved');
        }).catch((error) => {
          const rejection = {
            atMs: diagnosticNowMs(),
            ...mediaError(error),
          };
          appendLimited(
            diagnostic.playRejections,
            rejection,
            browserMediaDiagnosticEventLimit,
          );
          recordClipEvent(diagnostic, clip, 'play-rejected', {rejection});
        });
      }
    }

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
      elements.primary.style.opacity = '1';
      elements.secondary.style.opacity = '1';
      for (const [assetId, clip] of elements.clips.entries()) {
        const active = visual.kind === 'clip' && visual.asset === assetId;
        const hidden = !active;
        if (clip.hidden !== hidden) {
          clip.hidden = hidden;
          const diagnostic = clipDiagnostics.get(assetId);
          if (diagnostic) {
            recordClipEvent(diagnostic, clip, hidden ? 'hidden' : 'shown');
          }
        }
        if (!active && !clip.paused) {
          clip.pause();
        }
      }
      elements.scrollClip.hidden = true;
      if (visual.kind === 'state') {
        activeClipAsset = null;
        setImage(elements.primary, visual.asset);
        elements.primary.hidden = false;
        elements.primary.style.opacity = '1';
        if (
          visual.transition === 'fade' && visual.previousAsset &&
          visual.progress < 1 && !reducedMotion()
        ) {
          setImage(elements.secondary, visual.previousAsset);
          elements.secondary.hidden = false;
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
        clip.muted = muted || !visual.hasAudio;
        clip.playsInline = true;
        clip.playbackRate = playbackRate;
        if (!Number.isFinite(clip.readyState) || clip.readyState >= 2) {
          clipsWithDecodedFrames.add(clip);
        }
        clip.style.opacity = clipsWithDecodedFrames.has(clip) ? '1' : '0';
        const targetSeconds = visual.mediaMs / 1000;
        const diagnostic = clipDiagnostics.get(visual.asset);
        const enteringClip = activeClipAsset !== visual.asset;
        const clipPlaying = playing && visual.progress < 1 && !clip.ended;
        const driftToleranceSeconds = clipPlaying ? 0.15 : 0.04;
        const seekTargetSeconds = Number.isFinite(clip.duration)
          ? Math.min(Math.max(0, clip.duration - 0.001), targetSeconds)
          : targetSeconds;
        if (
          Number.isFinite(clip.duration) &&
          !clip.seeking &&
          (enteringClip ||
            (!clipPlaying &&
              Math.abs((clip.currentTime || 0) - seekTargetSeconds) > driftToleranceSeconds))
        ) {
          clip.currentTime = seekTargetSeconds;
        }
        activeClipAsset = visual.asset;
        if (diagnostic) {
          recordClipSample(diagnostic, clip, {
            clipPlaying,
            enteringClip,
            presentationMs: visual.mediaMs,
            targetSeconds,
          });
        }
        if (clipPlaying && !clip.seeking) {
          if (clip.paused) {
            playClip(clip, diagnostic);
          }
        } else if (!clip.paused) {
          clip.pause();
        }
      } else if (visual.kind === 'scroll') {
        activeClipAsset = null;
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
        if (scene.pointer.pressed) {
          elements.pointer.dataset.pressed = 'true';
        } else {
          delete elements.pointer.dataset.pressed;
        }
      } else {
        delete elements.pointer.dataset.pressed;
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
      const transition = entryTransition;
      const animatedEntry = transition === 'fade' || transition === 'window-open';
      if (animatedEntry && scene.localMs < entryTransitionStartMs) {
        elements.layout.style.opacity = '0';
        elements.layout.style.transform = 'none';
        return;
      }
      const progress = clampUnit(
        (scene.localMs - entryTransitionStartMs) / entryTransitionDurationMs,
      );
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
        clipPreloads = new Map();
        clipsWithDecodedFrames = new Set();
        const firstVisualEvent = nextContext.payload.events.find(
          (event) => ['state', 'clip', 'scroll'].includes(event.kind),
        );
        entryTransitionStartMs = firstVisualEvent ? firstVisualEvent.at_ms : 0;
        entryTransitionDurationMs = Math.max(
          1,
          Math.min(300, nextContext.payload.duration_ms - entryTransitionStartMs),
        );
        const viewportConfig = nextContext.payload.viewport;
        const scale = viewportConfig.device_scale_factor || 1;
        decodedAssetBytes = Math.round(
          viewportConfig.width * viewportConfig.height * scale * scale * 4 * 4,
        );
        const browserPresentation = (
          nextContext.beat.browser || nextContext.presentation.browser || {}
        );
        const windowConfig = browserPresentation.window || {mode: 'none'};
        const chromeConfig = browserPresentation.chrome || {mode: 'hidden'};
        entryTransition = (
          nextContext.beat.transition_in
          ?? windowConfig.opening_transition
          ?? 'cut'
        );
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
          const source = assetSource(event.asset);
          clip.setAttribute('src', source);
          clips.set(event.asset, clip);
          registerClipDiagnostic(event.asset, clip, source);
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
      setMuted(nextMuted) {
        muted = Boolean(nextMuted);
        if (elements) {
          for (const clip of elements.clips.values()) {
            clip.muted = muted;
          }
        }
      },
      setPlaying(nextPlaying) {
        playing = Boolean(nextPlaying);
        if (!playing && elements) {
          for (const clip of elements.clips.values()) {
            if (!clip.paused) {
              clip.pause();
            }
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
          const existing = clipPreloads.get(clip);
          if (existing) {
            return existing;
          }
          let load;
          if (!Number.isFinite(clip.readyState) || clip.readyState >= 2) {
            load = Promise.resolve();
          } else if (typeof clip.addEventListener !== 'function') {
            if (typeof clip.load === 'function') {
              clip.load();
            }
            load = Promise.resolve();
          } else {
            load = new Promise((resolve) => {
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
          }
          clipPreloads.set(clip, load);
          return load;
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
        clipPreloads.clear();
        clipsWithDecodedFrames.clear();
        entryTransitionStartMs = 0;
        entryTransitionDurationMs = 300;
        entryTransition = 'cut';
        windowDecoration = {};
        resizeObserver = null;
        lastScene = null;
        activeClipAsset = null;
        for (const diagnostic of clipDiagnostics.values()) {
          diagnostic.disposed = true;
        }
        clipDiagnostics.clear();
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
    createVisualizationDomRenderer,
    createVisualizationRendererAdapter,
    createPresentationAudioDeck,
    createPresentationAudioController,
    createPresentationAudioTimeline,
    createPresentationShell,
    createCastAudioTimeline,
    createTerminalRendererAdapter,
    decodeAsciinemaCast,
    defaultAudioBoundaryEpsilonSeconds,
    defaultAudioDriftToleranceMs,
    validatePresentationManifest,
    visualizationSegments,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  global.CastPlayerCore = api;
}(typeof globalThis !== 'undefined' ? globalThis : window));
