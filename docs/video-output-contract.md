# Video Output Contract

Status: current manifest-bundle contract.

Every terminal, browser, and mixed recording is published as one atomic bundle
under `website/static/omegaflow-videos/<id>/presentation/`:

- `recording.presentation.json`
- `recording.recording.json`
- beat-local terminal `.cast` and browser `.json` payloads
- referenced browser media
- optional per-take, content-addressed narration audio plus metadata and
  timestamp sidecars

The initial player assets are owned by the Python package under
`omegaflow/player/static/`. A website target may receive copied player
assets plus generated video assets. Website-published video assets are
committed; non-website generated videos are ignored by default.
