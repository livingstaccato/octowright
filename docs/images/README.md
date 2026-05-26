# Image Assets

This directory is the canonical home for Octowright visual assets used by:

- `README.md` (PyPI/GitHub rendered banner)
- distributed skill docs
- plugin metadata/docs

## Layout

Assets are grouped by category — `brand/`, `otto/`, `favicon/`. Only
this README lives at the top level.

```
docs/images/
├── brand/                          # Octowright wordmark + logo (with text)
│   ├── octowright-banner.png       (512×512 source, branded mark)
│   ├── octowright-logo-128.png     (derived size)
│   ├── octowright-logo-256.png
│   └── octowright-logo-512.png
├── otto/                           # Otto the Octowright mascot
│   ├── otto.svg                    (vector source of truth)
│   ├── otto-avatar-64.png          (derived size)
│   ├── otto-avatar-128.png
│   ├── otto-avatar-256.png
│   └── otto-avatar-512.png
├── favicon/                        # Web/PWA/social icons
│   ├── favicon.ico
│   ├── favicon-icon-192.png
│   ├── favicon-icon-512.png
│   ├── apple-touch-icon.png
│   └── social-og-image.png         (manual, not regen-script output)
└── README.md
```

## Sources of truth

- `brand/octowright-banner.png` — branded mark (with wordmark). 128/256/512
  variants resize from this.
- `otto/otto.svg` — vector mascot. The avatar PNG ladder and the favicon
  PNGs all derive from this.
- `favicon/social-og-image.png` and `favicon/favicon.ico` are tracked but
  intentionally hand-curated — they are NOT outputs of the regeneration
  script and won't be overwritten if you re-run it.

## Naming convention

Use `name-purpose-size.ext` so files are readable at a glance. Avoid
numeric-only filenames (`logo-3.png`, `img-large.png`) — bake the purpose
into the filename so a reader can identify it without opening it.

Purpose tokens currently in use:

- `banner` — the wide-format branded mark used as a README hero
- `logo` — square branded variants (with wordmark)
- `logomark` — branded variants WITHOUT wordmark (reserved; not currently
  shipped, since `otto/otto-avatar-*` covers the no-text mascot need)
- `avatar` — round/square portrait crop of the mascot
- `icon` — small square render for web/PWA use
- `social-og-image` — the Open Graph share card

## Regeneration workflow

Regenerate all derived sizes from the masters:

```bash
scripts/generate_image_assets.sh
```

The script is cross-platform:

- macOS: uses `sips`
- Linux: uses ImageMagick (`magick` or `convert`)

If you replace `brand/octowright-banner.png` or `otto/otto.svg`, rerun the
script and commit the updated outputs in the appropriate subdirectory.
