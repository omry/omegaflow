# OmegaFlow

OmegaFlow is a tool for scripted terminal and video flows. Operators
author an OmegaFlow script, then build a website-ready video from that source.

The project includes a Python package named `omegaflow`, an `omegaflow` CLI,
a Docusaurus website for `omegaflow.dev`, a quickstart demo recording, and
tutorial chapter scaffolding under `recordings/`.

## Development

```bash
nox -s tests
nox -s schema_docs
nox -s package
pnpm --dir website build
omegaflow recording=quickstart-demo action=build
```

The current repository also preserves older OmegaFlow design work under
`docs/future/`.

## Deployment

The website is configured for `https://omegaflow.dev` and includes a GitHub
Pages workflow at `.github/workflows/deploy-website.yml`. The workflow builds
`website/` with pnpm and deploys `website/build`; `website/static/CNAME`
contains the custom domain.
