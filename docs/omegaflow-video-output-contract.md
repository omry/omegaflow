# OmegaFlow Video Output Contract

Status: initial migration contract.

An OmegaFlow Video is currently published as a set of website assets:

- baseline cast: `website/static/omegaflow-videos/<id>/<id>.cast`
- retimed cast: `website/static/omegaflow-videos/<id>/<id>.retimed.cast`
- recording fingerprint: `website/static/omegaflow-videos/<id>/<id>.recording.json`
- timeline metadata: `website/static/omegaflow-videos/<id>/<id>.timeline.jsonl`
- optional audio: `website/static/omegaflow-videos/<id>/<id>.mp3`
- optional audio metadata: `website/static/omegaflow-videos/<id>/<id>.json`
- optional beat timestamp files:
  `website/static/omegaflow-videos/<id>/<id>.<beat>.timestamps.json`

The initial player assets are owned by the Python package under
`omegaflow_studio/player/static/`. A website target may receive copied player
assets plus generated video assets. Website-published video assets are
committed; non-website generated videos are ignored by default.

The final OmegaFlow Video file format is intentionally not locked yet.
