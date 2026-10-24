# Public Reploy codec fixtures

These three JSONL files are byte-for-byte copies of the public controlled-session fixtures at Reploy commit `7dfc956ceaa18638260d380f53d12bc4701dcda2` (0.8.0.dev2). `provenance.json` records the source paths and SHA-256 values. The matching public contract is `website/docs/controlled-sessions.md` in that revision. No Reploy private transport or executable implementation is included.

C1.1 validates individual event/request messages with `python -m pytest tests/test_reploy_codecs.py`. The tests cover every golden plus adversarial fields, typed values, nested results and exact line bounds. `case-map.json` keeps the stable boundary IDs and subsequent proof owners. The host-result fixtures and cumulative C1 gate belong to C1.2. Lifecycle ordering and request authority belong to C2. These codecs do not activate the recording backend.
