"""Portable in-page audio capture for realtime browser fragments."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MAX_CAPTURED_AUDIO_BYTES = 2_000_000


PAGE_AUDIO_CAPTURE_INIT_SCRIPT = r"""
(() => {
  if (globalThis.__omegaflowPageAudioCapture) return;
  const AudioContextClass = globalThis.AudioContext || globalThis.webkitAudioContext;
  if (!AudioContextClass || !globalThis.MediaRecorder) {
    globalThis.__omegaflowPageAudioCapture = {
      start: async () => { throw new Error('page audio capture is unsupported'); },
      stop: async () => { throw new Error('page audio capture is unsupported'); },
    };
    return;
  }

  let context = null;
  let bus = null;
  const mediaSources = new WeakMap();
  const appMediaElements = new WeakSet();
  const capturedNodeOutputs = new WeakMap();
  const capturedContexts = new WeakMap();
  const nativeConnect = AudioNode.prototype.connect;
  const nativeDisconnect = AudioNode.prototype.disconnect;
  const nativeCreateMediaElementSource =
    AudioContextClass.prototype.createMediaElementSource;
  let active = null;
  let captureFailure = null;

  function ensureCaptureGraph() {
    if (context) return;
    context = new AudioContextClass();
    bus = context.createGain();
  }

  function connect(node, destination) {
    return nativeConnect.call(node, destination);
  }

  function captureWebAudioOutput(node, destination, output = 0) {
    const owner = node && node.context;
    if (
      !owner || owner === context || destination !== owner.destination ||
      typeof owner.createMediaStreamDestination !== 'function'
    ) {
      return;
    }
    ensureCaptureGraph();
    let capture = capturedContexts.get(owner);
    if (!capture) {
      const destinationNode = owner.createMediaStreamDestination();
      const imported = context.createMediaStreamSource(destinationNode.stream);
      connect(imported, bus);
      capture = {destinationNode, imported};
      capturedContexts.set(owner, capture);
    }
    let outputs = capturedNodeOutputs.get(node);
    if (!outputs) {
      outputs = new Set();
      capturedNodeOutputs.set(node, outputs);
    }
    if (!outputs.has(output)) {
      nativeConnect.call(node, capture.destinationNode, output, 0);
      outputs.add(output);
    }
  }

  AudioNode.prototype.connect = function(destination, ...rest) {
    const result = nativeConnect.call(this, destination, ...rest);
    captureWebAudioOutput(this, destination, rest.length > 0 ? rest[0] : 0);
    return result;
  };

  AudioNode.prototype.disconnect = function(...args) {
    const owner = this && this.context;
    const capture = owner ? capturedContexts.get(owner) : null;
    const outputs = capturedNodeOutputs.get(this);
    const result = nativeDisconnect.apply(this, args);
    if (!capture || !outputs) return result;
    if (args.length === 0) {
      capturedNodeOutputs.delete(this);
      return result;
    }
    if (typeof args[0] === 'number') {
      outputs.delete(args[0]);
      if (outputs.size === 0) capturedNodeOutputs.delete(this);
      return result;
    }
    if (args[0] !== owner.destination) return result;
    const selected = args.length > 1 ? [args[1]] : [...outputs];
    for (const output of selected) {
      if (!outputs.has(output)) continue;
      try {
        nativeDisconnect.call(this, capture.destinationNode, output, 0);
      } catch (_error) {
        // The native disconnect already established the application's state.
      }
      outputs.delete(output);
    }
    if (outputs.size === 0) capturedNodeOutputs.delete(this);
    return result;
  };

  AudioContextClass.prototype.createMediaElementSource = function(element) {
    const source = nativeCreateMediaElementSource.call(this, element);
    appMediaElements.add(element);
    const attached = mediaSources.get(element);
    if (attached) {
      try {
        nativeDisconnect.call(attached.source, bus);
      } catch (_error) {
        // The capture stream may already have ended.
      }
      mediaSources.delete(element);
    }
    return source;
  };

  function attachMedia(element) {
    if (mediaSources.has(element) || appMediaElements.has(element)) return;
    try {
      ensureCaptureGraph();
      const captureStream = element.captureStream || element.mozCaptureStream;
      if (typeof captureStream !== 'function') {
        throw new Error('media-element stream capture is unsupported');
      }
      const stream = captureStream.call(element);
      const connectAudioTrack = () => {
        if (
          mediaSources.has(element) ||
          appMediaElements.has(element) ||
          stream.getAudioTracks().length === 0
        ) {
          return;
        }
        const source = context.createMediaStreamSource(stream);
        connect(source, bus);
        mediaSources.set(element, {source, stream});
      };
      connectAudioTrack();
      if (!mediaSources.has(element)) {
        stream.addEventListener('addtrack', (event) => {
          if (event.track && event.track.kind === 'audio') {
            try {
              connectAudioTrack();
            } catch (error) {
              captureFailure = error;
            }
          }
        });
      }
    } catch (error) {
      captureFailure = error;
    }
  }

  const nativePlay = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function(...args) {
    const result = nativePlay.apply(this, args);
    if (active) attachMedia(this);
    return result;
  };

  function base64(bytes) {
    let binary = '';
    const chunkSize = 0x8000;
    for (let offset = 0; offset < bytes.length; offset += chunkSize) {
      binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
    }
    return btoa(binary);
  }

  globalThis.__omegaflowPageAudioCapture = {
    async start() {
      if (active) throw new Error('page audio capture is already active');
      captureFailure = null;
      ensureCaptureGraph();
      await context.resume();
      document.querySelectorAll('audio,video').forEach((element) => {
        if (!element.paused && !element.ended) attachMedia(element);
      });
      const destination = context.createMediaStreamDestination();
      connect(bus, destination);
      const mimeType = 'audio/webm;codecs=opus';
      if (!MediaRecorder.isTypeSupported(mimeType)) {
        bus.disconnect(destination);
        throw new Error('Opus MediaRecorder is unsupported');
      }
      const recorder = new MediaRecorder(destination.stream, {mimeType});
      const chunks = [];
      recorder.addEventListener('dataavailable', (event) => {
        if (event.data.size > 0) chunks.push(event.data);
      });
      recorder.start(100);
      active = {chunks, destination, mimeType, recorder};
    },
    async stop() {
      if (!active) throw new Error('page audio capture is not active');
      const capture = active;
      active = null;
      const stopped = new Promise((resolve, reject) => {
        capture.recorder.addEventListener('stop', resolve, {once: true});
        capture.recorder.addEventListener(
          'error',
          (event) => reject(event.error || new Error('page audio recorder failed')),
          {once: true},
        );
      });
      capture.recorder.stop();
      await stopped;
      bus.disconnect(capture.destination);
      if (captureFailure) {
        throw new Error(`page audio capture failed: ${captureFailure.message}`);
      }
      const blob = new Blob(capture.chunks, {type: capture.mimeType});
      const bytes = new Uint8Array(await blob.arrayBuffer());
      if (bytes.length === 0) throw new Error('page audio recorder produced no data');
      if (bytes.length > 2000000) {
        throw new Error('page audio recorder exceeds the size budget');
      }
      return {
        data: base64(bytes),
        mime_type: capture.mimeType,
      };
    },
  };
})();
"""


@dataclass(frozen=True)
class CapturedPageAudio:
    path: Path
    source_start_ms: int
    source_end_ms: int
    encoded_bytes: int


def start_page_audio_capture(page: Any) -> None:
    """Start the document-local recorder installed before page scripts."""

    page.evaluate("() => globalThis.__omegaflowPageAudioCapture.start()")


def stop_page_audio_capture(
    page: Any,
    *,
    fragments_dir: Path,
    source_start_ms: int,
    source_end_ms: int,
) -> CapturedPageAudio:
    """Stop and persist one private Opus capture."""

    result = page.evaluate(
        "() => globalThis.__omegaflowPageAudioCapture.stop()"
    )
    if (
        not isinstance(result, dict)
        or result.get("mime_type") != "audio/webm;codecs=opus"
        or not isinstance(result.get("data"), str)
    ):
        raise RuntimeError("page audio recorder returned an invalid result")
    maximum_base64_length = ((MAX_CAPTURED_AUDIO_BYTES + 2) // 3) * 4
    if len(result["data"]) > maximum_base64_length:
        raise RuntimeError("page audio recorder exceeds the size budget")
    try:
        content = base64.b64decode(result["data"], validate=True)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("page audio recorder returned invalid data") from exc
    if not content:
        raise RuntimeError("page audio recorder produced no data")
    if len(content) > MAX_CAPTURED_AUDIO_BYTES:
        raise RuntimeError("page audio recorder exceeds the size budget")
    digest = hashlib.sha256(content).hexdigest()
    path = fragments_dir / f"audio-{digest}.webm"
    if path.is_symlink():
        raise RuntimeError("page audio capture path is unsafe")
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError("page audio capture path is unsafe")
    else:
        path.write_bytes(content)
        path.chmod(0o600)
    return CapturedPageAudio(
        path=path,
        source_start_ms=source_start_ms,
        source_end_ms=source_end_ms,
        encoded_bytes=len(content),
    )
