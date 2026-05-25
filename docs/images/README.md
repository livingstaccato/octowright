# Image Assets

This directory is the canonical home for Octowright visual assets used by:

- `README.md` (PyPI/GitHub rendered banner)
- distributed skill docs
- plugin metadata/docs

## Source of truth

- `octowright-banner.png`: original full-size banner

## Generated size ladder

Use these square variants for icons/thumbnails:

- `octowright-logo-128.png`
- `octowright-logo-256.png`
- `octowright-logo-512.png`

## Naming Convention

Avoid numeric-only filenames. Use `name-purpose-size.ext` so files are readable
at a glance.

Current examples:

- `otto/otto-avatar-64.png`
- `otto/otto-avatar-128.png`
- `otto/otto-avatar-256.png`
- `otto/otto-avatar-512.png`
- `favicon/favicon-icon-192.png`
- `favicon/favicon-icon-512.png`
- `favicon/social-og-image.png`

## Regeneration workflow

Regenerate all derived sizes from the banner source:

```bash
scripts/generate_image_assets.sh
```

The script is cross-platform:

- macOS: uses `sips`
- Linux: uses ImageMagick (`magick` or `convert`)

If you replace the source banner, rerun the script and commit the updated outputs in this directory.
