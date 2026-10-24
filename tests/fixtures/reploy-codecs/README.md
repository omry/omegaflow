# Public Reploy codec fixtures

These four JSONL files are byte-for-byte copies of the public controlled-session fixtures at Reploy commit `7dfc956ceaa18638260d380f53d12bc4701dcda2` (0.8.0.dev2). `provenance.json` records the source paths and SHA-256 values. The matching public contract is `website/docs/controlled-sessions.md` in that revision. No Reploy private transport or executable implementation is included.

Run `python -m pytest tests/test_reploy_codecs.py` for the complete C1 corpus. C1.1 covers every event/request golden and C1.2 adds all seven host results, including startup rejection, output publication, discarded output and host errors. The corpus checks malformed fields/values, recursive duplicates, UTF-8, JSONL framing, crossed outcomes, exact client line bounds and lossless host diagnostics. Host stdout has no documented client-stream size bound; tests preserve diagnostics beyond 1 MiB while the client stream retains its 1 MiB limit. `case-map.json` records stable boundary IDs and subsequent proof owners.

The host `ok` consistency check follows the pinned public producer. It preserves `controller_status` independently and does not add a controller-exit predicate. C2 owns lifecycle ordering and request authority; C3 owns stream transport; C8 owns actual host invocation and final host-result interpretation. These codecs do not activate the recording backend.
