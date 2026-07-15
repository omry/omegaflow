# Release fragments

Add one UTF-8 Markdown fragment for each user-visible change. Use an issue
number when one exists, such as `123.feature.md`. For work without an issue,
use an orphan name such as `+browser-capture.feature.md`.

Supported fragment types:

- `feature`: new user-facing behavior
- `bugfix`: corrected behavior
- `doc`: documentation changes
- `misc`: maintenance and release-engineering changes

Preview the next release notes with:

```bash
towncrier build --draft --version VERSION
```
