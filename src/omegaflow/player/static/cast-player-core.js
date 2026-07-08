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

  const api = {
    createCastAudioTimeline,
    defaultAudioBoundaryEpsilonSeconds,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  global.CastPlayerCore = api;
}(typeof globalThis !== 'undefined' ? globalThis : window));
