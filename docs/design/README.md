# OmegaFlow Design Assets

These are the approved OmegaFlow logo and mascot source assets. All artwork is
transparent, vector-only SVG with accessible titles and descriptions.

## Assets

- `logo.svg` — primary two-color logo.
- `logo-mono.svg` — one-color fallback using `currentColor`.
- `mascot.svg` — canonical quiet mascot.
- `mascot-blink.svg` — expression variant; the silhouette remains unchanged.
- `mascot-camera.svg` — recording pose with one camera and a prominent play cue.

## Night Studio Palette

| Role | Color | Usage |
| --- | --- | --- |
| Brand indigo | `#8b7cff` | Logo and mascot silhouette, links, focus, active controls, played timeline |
| Playback cue | `#ffc247` | Play icons, current cue, scrubber thumb, timing emphasis |
| Ink | `#10131a` | Face, camera outline, dark foreground detail |
| Paper | `#f5f6fa` | Eyes, camera body, primary light text |
| Raised detail | `#19202c` | Lens detail and raised dark surfaces |

Indigo means interaction. Amber means playback or timing. Green is reserved for
success, and coral red is reserved for errors or destructive actions. Terminal
ANSI colors remain independent from the brand palette.

## Usage

- Keep generous clear space around the logo and mascot.
- Use transparent SVGs rather than adding a baked-in background.
- Add at most one expression or one prop to the canonical mascot.
- Do not add gradients or additional accent colors inside the mark.
- Do not let a mascot prop compete with the amber play cue.

## Integrated Surfaces

- Website navbar and favicon: `website/static/img/omegaflow-logo.svg` and
  `website/static/img/favicon.svg`.
- Website homepage: `website/static/img/omegaflow-mascot-camera.svg`.
- Social metadata: `website/static/img/omegaflow-social.svg` is the editable
  source and `website/static/img/omegaflow-social.png` is the published card.
- Recording player: the Night Studio interface palette and a linked logo in a
  dedicated top-bar column in `src/omegaflow/player/static/cast-player.html`.
  Keep the logo outside the scrolling narration element so responsive text
  layout and scrolling remain independent.
- GitHub and PyPI README: the canonical `docs/design/logo.svg` source asset.
