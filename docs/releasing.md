# Releasing Octowright

Create every GitHub release as a draft first. Its title must exactly equal its
`vX.Y.Z` tag. Write a body that explains each problem and its result in
self-contained prose; do not include issue numbers or issue links.

The policy is deliberately lexical: the validator refuses any standalone
`#<digits>` token, including one used as a numeric literal. This keeps issue
numbers out of release-note text entirely.

Prepare the proposed body in `/tmp/octowright-release-notes.md`, then validate
the body and every committed release-note source before creating the draft:

```bash
uv run python scripts/github_release_notes.py check-body /tmp/octowright-release-notes.md
uv run python scripts/github_release_notes.py check-local
uv run python scripts/github_release_notes.py create-draft vX.Y.Z /tmp/octowright-release-notes.md
```

Review the draft in GitHub before publication: confirm its title, body, tag,
and attached assets are correct. Publishing the release triggers
`.github/workflows/release.yml`, which builds the distributions and publishes a
full release to PyPI or a prerelease to TestPyPI.

After publication, audit the remote release metadata:

```bash
uv run python scripts/github_release_notes.py audit-remote
```
