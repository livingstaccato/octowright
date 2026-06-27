---
name: project-security-audit-backlog
description: Security findings from the 2026-06-26 5-surface audit — Tier 1+2 SHIPPED 2026-06-26; Tier 3 nits remain
metadata:
  type: project
---

Parallel read-only pen-audit of octowright on 2026-06-26 (5 surfaces: HTTP, leader/bridge, disk-write, credentials, SSRF/DoS). HTTP front door is solid (Host classifier fails closed; every sensitive route gated).

**Tier 1 + Tier 2 SHIPPED 2026-06-26** (all TDD + live-verified, each its own commit), each behind a config knob:
1. SSRF — `octowright.ssrf.check_navigation_url` wired into `_reject_unsafe_url`; `OCTOWRIGHT_SSRF_POLICY=block-private` (off default) blocks literal non-public IPs/localhost/metadata; `OCTOWRIGHT_SSRF_ALLOW` allowlist. (No DNS-rebind coverage — literal-IP/known-name only.)
2. Cred-leak sinks — `_redact_sink_value` scrubs `press_key`/`evaluate`/`select_option` under `OCTOWRIGHT_REDACT_INPUTS=all`.
3. Recordings 0600 / parent 0700 via `recorder._recordings_private`, `OCTOWRIGHT_RECORDINGS_PRIVATE` (on default).
4. Bridge token — `LeaderInfo.token` (0600 lockfile) required by `http.bridge_auth.BridgeTokenGuard` on /mcp, presented by `proxy_runtime.resolve_leader_token`; `OCTOWRIGHT_BRIDGE_REQUIRE_TOKEN` (on default). **Closes cross-user/sandbox /mcp RCE only — NOT same-user (attacker reads the lockfile) and NOT lockfile-poisoning MITM (attacker writes the token).**
5. Cap + memory floor moved into `browser_pool.limits`, enforced at `roster.spawn_roster` chokepoint so `scenario_start` can't bypass.
6. Download containment — `session/downloads._safe_download_name` + `reject_unsafe_path` (remote Content-Disposition filename).

**Tier 3 — still OPEN (nits / lower-value):**
- `cli/restart` kills the lockfile-recorded PID + sweeps the lockfile-recorded port, both attacker-writable → same-user process-kill / cross-project DoS primitive at user-invoked restart.
- `goldens.py` / `captures.py` use non-atomic `write_text` (same-uid symlink TOCTOU); route through `atomic_write_text` like screenshots/macros.
- Unbounded JSONL recording growth — no size ceiling / rotation (disk-fill DoS on a long-lived session).
- `user:pass@host` userinfo NOT stripped from the navigate telemetry span (`_sanitize_url_for_span` strips only query).
- `browser_mock_route` regex → ReDoS (session-bounded, low).
- WS loopback-Origin allowance lets another *local* app (that knows a session id) read live JSONL (documented trade-off).

**Residual same-user risk** (inherent to the trust model, not closeable): a hostile same-user process can read the 0600 lockfile (token) and rewrite it (MITM). Documented in AGENTS.md "Bridge capability token".

Verified-safe (don't re-flag): file:// blocked, yaml.safe_load only, SSH password never persisted, init-script JSON-escaped, upload allowlist, Host classifier fails closed. Relates to the H4/H5 stability track.
