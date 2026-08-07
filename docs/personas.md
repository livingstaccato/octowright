# Personas

A **persona** is a named identity that owns persistent browser profiles across
one or more engines, plus metadata: display name, default URL, startup macros,
credential references, and free-form app metadata.

Think of a persona as *"dante — my Discord power user across all three engines"*,
and a profile as *one engine-specific piece of that identity*.

The persona tools (`persona_create`, `persona_get`, `persona_list`,
`persona_delete`, `persona_credentials_check`, `profile_list`, `profile_delete`,
`profile_cleanup`) belong to the `personas` capability profile. By default
every tool registers; under `--profile=core` they are not visible — combine
with `--profile=core,personas` to keep them. See
[getting-started.md](getting-started.md#slimming-the-llm-tool-surface).

## On-disk layout

Personas live under the Octowright config dir. POSIX uses the XDG config dir
`${XDG_CONFIG_HOME:-~/.config}/octowright/profiles/`; Windows uses
`%APPDATA%\octowright\profiles\`. Override with `OCTOWRIGHT_PROFILES_DIR`:

```text
<octowright-config>/profiles/
├── dante/
│   ├── profile.yaml          # persona metadata
│   ├── webkit/               # dante's WebKit browser state
│   ├── firefox/              # dante's Firefox state
│   └── chromium/             # dante's Chromium state
└── tim/
    ├── profile.yaml
    └── webkit/
```

Each engine subdirectory is a Playwright **persistent context** — cookies,
localStorage, IndexedDB, and service-worker state survive close/relaunch.

## profile.yaml

The metadata file declares everything Octowright needs to launch the persona
and resolve its credentials at use-time:

```yaml
name: dante
display_name: Dinosaur Dante
default_url: https://discord.com/app
default_macros: [discord-login]
emoji: 🐬                         # optional title-bar override
credentials:
  email_env: DANTE_EMAIL
  password_cmd: "op read op://Personal/dante/password"
app:
  discord_user_id: "1234"
  role: player
```

Field reference:

| Field | Purpose |
|---|---|
| `name` | Slug used for the directory and CLI lookups. Must match the parent dir name. |
| `display_name` | Human-readable label shown in dashboard cards and window titles. |
| `default_url` | URL `browser_launch profile=dante` opens when no `url` is given, **and** the context's Playwright `base_url` — see "Host-relative macros" below. |
| `default_macros` | List of macro names to run automatically after launch (e.g. login flow). |
| `emoji` | Override for the auto-picked title-bar persona emoji. |
| `credentials` | References to env vars (`*_env`) or shell commands (`*_cmd`) — never the secrets themselves. |
| `app` | Domain metadata for macros and scenarios. Mostly free-form, with one key Octowright reads itself: `app.hosts`, a list that `resolve` scores a persona against when suggesting one for a URL. |

## Host-relative macros

A macro is the **behaviour**; the persona is the **where**. Keep origins out of
macros and let the persona supply one:

```yaml
# persona: buyer-proving
default_url: https://proving.account.undef.games/
```

```yaml
# macro: buyer-orders — no origin anywhere in it
actions:
  - navigate: "/orders"
  - expect_url: "/orders"
```

`default_url` is handed to the browser context as Playwright's `base_url`, so
`navigate` resolves a relative path against the persona's origin and
`expect_url` accepts the same relative form. The same macro then replays against
a local stack, a staging deployment or production by launching it as a different
persona — no macro edit, no per-environment copies of the same behaviour.

Absolute URLs are unaffected, so existing macros keep working. A profile that is
not a saved persona, or a persona with no `default_url`, gets no `base_url`;
relative navigation then fails in Playwright, which is the honest outcome —
nothing declared where the macro was meant to point.

## Credentials workflow

Credentials are stored as **references**, never secrets. Each entry uses one of
two suffixes:

- `<name>_env: VAR_NAME` — read from the named environment variable at use-time.
- `<name>_cmd: "command argv-form"` — exec the command directly and capture
  stdout (typical for password managers like `op`, `pass`, `bw`). The cmd
  is `shlex.split` and run with `shell=False` — no `/bin/sh` is involved.

  Pipes / redirection / subshells in the raw cmd are refused. To use a
  pipeline, write the cmd as `bash -c "..."` — bash becomes a normal argv
  token whose `-c` argument carries the shell logic the cmd author signed
  off on. The trust boundary stays explicit because the persona YAML
  itself names the shell binary.

### Pre-flight check

Before launching a scenario whose startup macros need logins, validate every
reference resolves:

```bash
# As MCP tool
persona_credentials_check name=dante
```

The report lists each credential, its source (env var or shell command), the
reference itself, and a per-field `ok` / `error` status. **Resolved secret
values are never included in the report.**

This catches the classic "logged in 6 of 7 windows, then discovered the env var
was unset on #7" failure mode before any browser launches.

## Tools

| Tool | Purpose |
|---|---|
| `persona_list` | Enumerate every persona on disk. |
| `persona_get` | Fetch a single persona's metadata. |
| `persona_create` | Scaffold a new persona dir with `profile.yaml`. |
| `persona_delete` | Remove a persona (and all its engine profiles). |
| `persona_credentials_check` | Pre-flight credential resolution without launching a browser. |
| `profile_list` / `profile_delete` | Lower-level: enumerate or wipe per-engine profile dirs. |
| `browser_suggest_for_url` | Pre-launch ranking: which saved persona owns this URL? |

## CLI

```bash
uv run octowright persona list
uv run octowright persona show <name>
uv run octowright persona create <name> --display "Display Name" --url "https://octowright.com"
uv run octowright persona delete <name>
```

## Related

- [macros.md](macros.md) — `default_macros` typically references a recorded login flow.
- [scenarios.md](scenarios.md) — scenarios reference personas by `name` in the participant list.
- [troubleshooting.md](troubleshooting.md#persona-credential-resolution-problems) — resolving credential failures.
