# Personas

A **persona** is a named identity that owns persistent browser profiles across
one or more engines, plus metadata: display name, default URL, startup macros,
credential references, and free-form app metadata.

Think of a persona as *"dante — my Discord power user across all three engines"*,
and a profile as *one engine-specific piece of that identity*.

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
display_name: Dante Alighieri
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
| `default_url` | URL `browser_launch persona=dante` opens when no `url` is given. |
| `default_macros` | List of macro names to run automatically after launch (e.g. login flow). |
| `emoji` | Override for the auto-picked title-bar persona emoji. |
| `credentials` | References to env vars (`*_env`) or shell commands (`*_cmd`) — never the secrets themselves. |
| `app` | Free-form dict for domain metadata (Octowright never reads it; macros and scenarios can). |

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
uv run octowright persona create <name> --display "Display Name" --url "https://example.com"
uv run octowright persona delete <name>
```

## Related

- [macros.md](macros.md) — `default_macros` typically references a recorded login flow.
- [scenarios.md](scenarios.md) — scenarios reference personas by `name` in the participant list.
- [troubleshooting.md](troubleshooting.md#persona-credential-resolution-problems) — resolving credential failures.
