# Video Output Contract

Status: current manifest-bundle contract.

Every terminal, browser, and mixed recording is published as one atomic bundle
under `website/static/omegaflow-videos/<id>/presentation/`:

- `recording.presentation.json`
- `recording.recording.json`
- `signatures.json`, the canonical hash and byte-size index for the bundle
- beat-local terminal `.cast` and browser `.json` payloads
- referenced browser media with stable, semantic filenames
- optional per-take narration audio with stable filenames, plus metadata and
  timestamp sidecars

Asset filenames remain stable across rebuilds. The player fetches the signature
sidecar without browser caching and uses each file's signature as its cache key.
This keeps generated diffs readable without serving stale media.

The initial player assets are owned by the Python package under
`omegaflow/player/static/`. A website target may receive copied player
assets plus generated video assets. Website-published video assets are
committed; non-website generated videos are ignored by default.
