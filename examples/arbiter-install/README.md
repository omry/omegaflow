# Arbiter Install Compatibility Proof

This directory preserves the first real-world migration proof for OmegaFlow
Studio: rebuilding Arbiter's install-and-bootstrap recording through the
extracted tool.

Files here are intentionally Arbiter-specific. They may mention Arbiter,
Reploy, IMAP, SMTP, local mail labs, Arbiter config files, and Arbiter website
paths. Those details must not move into `src/omegaflow_studio/` unless they are
generalized first.

The standalone migration proof is:

```bash
studio recording=arbiter-install action=build dry_run=true
```

The full production rebuild still belongs in Arbiter because it publishes into
Arbiter's website and exercises Docker/Reploy setup. During migration, Arbiter
consumes this checkout through an editable install from `/home/omry/dev/omegaflow`
into Arbiter's virtualenv.
