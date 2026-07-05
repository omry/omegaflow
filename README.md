# OmegaFlow Studio

OmegaFlow Studio is a tool for scripted terminal and video flows. Operators
author an OmegaFlow Studio script, then build a website-ready OmegaFlow Video
from that source.

This repository is being migrated from Arbiter's media studio into a standalone
project. The initial migration target is a Python package named
`omegaflow-studio`, a `studio` CLI, a Docusaurus website for `omegaflow.dev`,
and a first getting-started recording under `studio/recordings/`.

## Development

```bash
python -m build
pytest
pnpm --dir website build
studio recording=getting-started action=build
```

The current repository also preserves older OmegaFlow design work under
`docs/future/`.

## Deployment

The website is configured for `https://omegaflow.dev` and includes a GitHub
Pages workflow at `.github/workflows/deploy-website.yml`. The workflow builds
`website/` with pnpm and deploys `website/build`; `website/static/CNAME`
contains the custom domain. GitHub Pages and DNS still need to be configured in
the `omry/omegaflow` repository before the domain is live.
