# GitHub Release Note Normalization Design

## Goal

Make Octowright's release history understandable without requiring a reader to
open an issue, and make every GitHub release name follow one stable convention.

Success means:

- all 40 existing GitHub releases are named exactly after their tag,
  `vX.Y.Z`;
- no GitHub release body uses an issue number as content or as a secondary
  reference;
- the current changelog and curated upgrade highlights contain no issue-number
  references;
- already useful historical prose is preserved instead of being rewritten for
  novelty;
- future release publication refuses context-free issue shorthand and always
  derives the release title from the tag;
- octowright.com is regenerated from the resulting curated highlights and
  deployed with every release through v0.25.0.

## Current State

The public repository has 40 GitHub releases with non-empty bodies totaling
about 19,000 words. Most are already self-contained. Five release bodies
contain issue-number references:

- v0.19.3;
- v0.19.4;
- v0.23.0;
- v0.24.0;
- v0.25.0.

The title convention also drifted. Recent releases are named
`octowright X.Y.Z`, older releases mix bare tags and tag-plus-subtitle names,
and the desired convention is now exactly `vX.Y.Z` for every release.

The 46 committed curated highlight files already contain no issue numbers and
are self-contained. Four sections in the current root `CHANGELOG.md` still
contain issue references. Tagged changelogs are immutable historical objects
and will not be rewritten.

## Chosen Approach

Perform a surgical, source-backed migration rather than reauthoring all 19,000
words or regenerating the bodies from tagged changelogs.

1. Fetch every existing GitHub release and retain its current body.
2. Rewrite the five bodies that contain issue numbers so the problem,
   behavior change, and resolution stand on their own.
3. Reapply all 40 audited bodies while setting every release title to its tag.
4. Remove issue-number references from the current changelog without removing
   the surrounding explanation.
5. Add a tested release helper and validation guard so future releases use the
   tag as the title and reject issue-number references before a draft is
   created.
6. Regenerate octowright.com's release data from the unchanged curated
   highlight source, then test and deploy the website.

This preserves context that was already good, changes only prose that violates
the new policy, and leaves a reproducible enforcement path for the next
release.

## Release-Note Policy

Every note must explain the user-visible problem and result in plain language.
An issue number is not acceptable as a headline, sentence, parenthetical,
footnote, known-gap identifier, or link. Issue numbers disappear from release
notes entirely.

Repository issues may still be used for engineering coordination. They are
simply not part of the public release-note contract.

Release titles are exactly equal to their tags. Descriptive subtitles move
into the body instead of the title, so list views, feeds, and automation expose
one predictable name format.

## Repository Changes

### Changelog cleanup

Rewrite only the issue-bearing portions of the current `CHANGELOG.md`:

- preserve the behavior and migration details;
- replace issue-only known-gap bullets with descriptive prose;
- remove parenthetical issue references after otherwise descriptive entries;
- leave tagged repository history untouched.

The curated highlight JSON does not require prose changes, but a guard will
scan it so the invariant remains true.

### Release helper

Add one Python entry point for GitHub release preparation. It will:

- accept a `vX.Y.Z` tag and a Markdown notes file;
- require the release title to be the tag rather than accepting caller-supplied
  title text;
- reject notes containing issue-number patterns before invoking GitHub;
- create a draft release with `gh release create --draft --verify-tag`, leaving
  publication as an explicit human action;
- expose a validation-only path used by tests and local release preparation.

Draft-first behavior keeps the existing PyPI trigger safe: package publication
still begins only when the reviewed GitHub release is published.

### Local and remote audit

Add a release-note audit that can validate:

- local `CHANGELOG.md` and every curated highlight JSON contain no issue-number
  references;
- a supplied release body is non-empty and contains no prohibited reference;
- when run against GitHub, every release name equals its tag and every body is
  non-empty and issue-number-free.

The local checks join the repository's normal quality gate. The remote audit
is an explicit release/maintenance check because CI should not depend on live
GitHub API state.

## Historical Migration

The migration will begin with a read-only snapshot of all 40 release IDs,
tags, titles, and bodies. That snapshot is used to prepare the edits and to
verify that no body is lost.

For the 35 bodies already satisfying the content policy, the body is reapplied
unchanged while its title is normalized. For the five affected bodies, only
the issue-bearing lines are rewritten. The new wording must preserve any
upgrade instruction, compatibility warning, known limitation, or security
detail in the original.

Before any remote write, validation must prove:

- exactly the same 40 tags are present;
- every proposed title equals its tag;
- every proposed body is non-empty;
- no proposed body contains an issue-number pattern;
- the 35 unaffected bodies are byte-for-byte unchanged.

Updates are applied one release at a time with `gh release edit`. Afterward,
the live API is fetched again and compared to the approved proposal. Editing a
published release does not move tags, rebuild artifacts, or publish packages.

## Website Data Flow

octowright.com continues to use its existing independent presentation source:

1. Octowright's curated `upgrade/highlights/*.json` files provide concise,
   self-contained notes.
2. The site generator rewrites `data/releases.json` newest first.
3. Hugo renders the Releases page.
4. The build stamp resolves published v0.25.0 for the top-right badge and
   version-pinned repository links.

No root changelog import or visual redesign is introduced. The website sync
happens after the source and remote release-note audits pass.

## Error Handling and Recovery

- A malformed tag, missing tag, empty body, issue-number reference, failed
  `gh` command, or release-count mismatch stops the operation before the next
  phase.
- The pre-migration snapshot is retained outside the repository until final
  verification, providing the exact previous body for a targeted rollback.
- A failed remote update is retried only for that tag after reading its live
  state; the migration never assumes an earlier write succeeded.
- Website deployment stops if tests, link checks, PR checks, Cloudflare
  deployment, or production verification fails.

## Testing and Verification

Repository tests will cover:

- accepted and rejected issue-reference patterns;
- release-tag validation;
- the helper's exact `gh release create` arguments, including the title/tag
  identity, draft mode, and tag verification;
- validation occurring before any subprocess side effect;
- current changelog and highlight data satisfying the policy.

The implementation will also run Octowright's relevant focused tests and full
quality gates. The remote migration will use dry-run validation before writes
and a fresh API audit afterward. The site repository will run its complete
pytest suite, Hugo production build, rendered-HTML assertions, PR link checks,
Cloudflare deployment, and live production checks for the v0.25.0 badge and
the 0.23.0 through 0.25.0 release entries.

## Scope Boundaries

In scope:

- all 40 GitHub release titles and bodies;
- current Octowright changelog and release tooling;
- release-note policy tests and documentation;
- the already-approved octowright.com v0.25.0 synchronization and deployment.

Out of scope:

- rewriting immutable tags or historical commits;
- changing release assets or package artifacts;
- republishing PyPI distributions;
- closing or deleting repository issues;
- redesigning octowright.com;
- rewriting already self-contained historical release prose merely to change
  style.
