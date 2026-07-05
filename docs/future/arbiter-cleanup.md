# Arbiter Cleanup Ledger

Use this ledger for Arbiter references or Arbiter-specific logic that cannot be
removed during the first OmegaFlow Studio migration pass.

Every entry should include:

- file
- reason it remains
- owner or scope
- follow-up cleanup action

## Current Audit

- `README.md`: mentions Arbiter only as migration provenance. This is
  intentionally temporary project context, not product behavior.
- `examples/arbiter-install/`: intentionally Arbiter-specific compatibility
  proof. Arbiter/Reploy/mail-lab logic may remain here while proving the
  extracted tool can rebuild Arbiter's install recording.

## Deferred Cleanup

- Arbiter production rebuild: the standalone compatibility proof currently runs
  `examples/arbiter-install` as a Studio dry run. A full rebuild still belongs
  in Arbiter after it installs `/home/omry/dev/omegaflow` editable into the
  Arbiter virtualenv, because the production recording publishes into Arbiter's
  website and exercises Docker/Reploy setup.
