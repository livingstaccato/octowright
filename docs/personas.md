# Personas

A persona is a named identity layer over per-engine browser state.

Storage layout:

```text
~/.config/undef/profiles/
  <persona>/
    profile.yaml
    chromium/
    firefox/
    webkit/
```

## Core Tools

- `persona_list`
- `persona_get`
- `persona_create`
- `persona_delete`
- `persona_credentials_check`
- `profile_list`
- `profile_delete`
- `browser_suggest_for_url`

## Credentials Workflow

Credential references are metadata (`*_env`, `*_cmd`) and are resolved at use-time.
Secrets are not stored in `profile.yaml`.

Before startup-macro or scenario runs requiring auth, call:

- `persona_credentials_check`

This surfaces missing env vars/command failures early.

## CLI

```bash
uv run octowright persona list
uv run octowright persona show <name>
uv run octowright persona create <name> --display "Name" --url "https://example.com"
uv run octowright persona delete <name>
```
