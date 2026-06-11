# Multi-persona isolation test — proves shared-context tabs fail

Three sessions log into the same site as different users. Session identity lives in
`localStorage` (`whoami.html`). A backend that gives each session its OWN browser context
(Octowright: one instance/profile per session) keeps them isolated; a backend that shares
one context across tabs (Playwright MCP: single browser + tabs) lets them clobber each
other — the last login wins and every tab then reports the wrong user.

## Fixture
`whoami.html` — login form stores username in `localStorage['acme_user']`; "Who am I?"
reads it back live.

## The prompt (paste verbatim)

```
You have browser automation tools. Use only those tools; observe through them; do NOT
invoke any Skill. Work step by step.

You are testing whether three user sessions stay ISOLATED from each other. Open THREE
separate browser sessions on the same site — if your tools can launch independent
browsers, use three independent browsers (one session each); if they cannot, use whatever
separate-session mechanism you have (e.g. tabs).

Site (same URL for all three): http://localhost:8000/whoami.html

Task:
1. Open session 1, session 2, and session 3 on the site.
2. Log in as a DIFFERENT user in each, IN THIS ORDER:
   session 1 -> "Alice", session 2 -> "Bob", session 3 -> "Carol".
   (Type the username into the form and submit "Log in".)
3. Now revisit EACH session in order (1, then 2, then 3) and click "Who am I?" to read who
   that session reports being logged in as RIGHT NOW. Read it fresh from the page each time.
4. Compare intended vs actual for each session.

Then reply with:
A) A table: session | intended user | "Who am I?" now reads.
B) Did the three sessions stay isolated (each still shows its own user)? yes/no — and
   exactly what you observed (if they bled together, say which user they all show).
C) The session mechanism you used (independent browsers? tabs in one browser?) and whether
   each session had its own cookies/localStorage.
D) Self-report (<=150 words): tool calls, number of sessions/browsers, easy or hard, any
   retries, and confidence (low/med/high).

Do not skip part D.
```

## Ground truth (for scoring)

- Logins, in order: session1=Alice, session2=Bob, session3=Carol.
- **Isolated backend (separate browsers/profiles, e.g. Octowright):** each "Who am I?"
  returns its OWN user → s1 Alice, s2 Bob, s3 Carol → **isolated = YES**.
- **Shared-context backend (tabs in one browser, e.g. Playwright MCP):** all three tabs
  share `localStorage['acme_user']`, so the last login (Carol) wins → every "Who am I?"
  reads **"Logged in as: Carol"** → **isolated = NO** (Alice and Bob are gone). This is the
  failure the test is designed to expose.
