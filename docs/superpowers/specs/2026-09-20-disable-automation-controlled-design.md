# Chromium AutomationControlled Launch Option

## Context

Google sign-in currently rejects a default Playwright Chromium session with
“This browser or app may not be secure.” Controlled testing isolated the
deciding signal:

- raw Playwright with no Octowright scripts or extension exposed
  `navigator.webdriver === true` and was rejected;
- raw Playwright launched with
  `--disable-blink-features=AutomationControlled` exposed
  `navigator.webdriver === false` and advanced to the account challenge; and
- current Octowright with its title tag, badge, viewport overlay, extension,
  and bindings still advanced to the account challenge when launched with the
  same Chromium flag.

Octowright's page injections are therefore not the cause. The useful
capability is a narrow, explicit Chromium launch setting. Although Google
authentication motivated the work, the option must remain general because the
browser-level behavior can affect other sites too.

## Goals

- Add a safe first-class launch option named
  `disable_automation_controlled`.
- Let callers opt into the tested Chromium flag without enabling arbitrary
  `launch_args` and their code-execution risk.
- Keep existing launches unchanged by default.
- Make the effective choice visible in launch recordings and preserve it
  across legitimate relaunch paths.
- Fail clearly when the Chromium-only option is requested for another engine.

## Non-goals

- Do not automatically enable the option for Google or any hostname.
- Do not switch the behavior during a live browser session; Chromium resolves
  the feature at process launch.
- Do not disable Octowright's title tag, badge, viewport overlay, extension,
  bindings, or other observability features.
- Do not claim that the option makes automation generally undetectable.
- Do not enable or weaken the existing arbitrary `launch_args` gate.

## Public API

Add the following optional field to the browser launch surface and
`LaunchOptions`:

```python
disable_automation_controlled: bool = False
```

Example:

```python
browser_launch(
    kind="chromium",
    profile="notebooklm-owner",
    url="https://accounts.google.com/",
    disable_automation_controlled=True,
)
```

The name deliberately describes the Chromium feature being changed. It avoids
the overly narrow `authentication_mode` and the misleadingly broad
`compatibility_mode` or `stealth` labels.

## Behavior and Data Flow

1. The MCP `browser_launch` handler accepts the boolean and passes it through
   `LaunchOptions` into `BrowserPool.launch`.
2. `LaunchOptions.validate` rejects
   `disable_automation_controlled=True` unless `kind == "chromium"`. The
   rejection occurs before profile allocation or browser launch.
3. Chromium argument construction appends the fixed argument
   `--disable-blink-features=AutomationControlled` when the field is true.
   False leaves the current argument list untouched.
4. Caller-supplied `launch_args`, when separately authorized, retain the
   existing ordering rule and are appended after Octowright's fixed arguments.
5. The launch JSONL event records the boolean. Older recordings that lack it
   resolve to false.
6. Safe relaunch paths restore the recorded boolean, and in-memory
   handoff/crash-reopen paths preserve it through `to_pool_kwargs`. Unlike an
   arbitrary executable path or argument list, this fixed boolean does not
   introduce a code-execution primitive.

The setting applies for the entire Chromium process lifetime. A user who wants
it only for sign-in launches a persistent profile with the option enabled,
completes authentication, closes that browser, and relaunches the same profile
normally. The saved cookies survive; the next browser returns to the default
automation behavior.

## Errors and Observability

- Firefox or WebKit plus `disable_automation_controlled=True` raises an
  `InvalidRequestError` naming the Chromium-only constraint.
- The launch result and browser behavior otherwise follow existing launch
  handling; no site-specific fallback or silent retry is added.
- Recordings expose the requested boolean so a diagnostic can distinguish a
  standard launch from one using the compatibility flag.
- Documentation must state only the measured effect:
  `navigator.webdriver` is false on the supported Chromium build. It must not
  promise acceptance by any particular site.

## Testing

Unit coverage will verify:

- the default is false and adds no argument;
- true adds exactly `--disable-blink-features=AutomationControlled`;
- Firefox and WebKit reject true before launch;
- the MCP handler forwards the field;
- `to_pool_kwargs`, launch recording, and launch-record restoration preserve
  the field; and
- recordings created before the field existed default to false.

A live-browser test will launch a local `data:` page twice and assert:

- the default Chromium launch reports `navigator.webdriver === true`; and
- a launch with `disable_automation_controlled=True` reports
  `navigator.webdriver === false`.

The test suite will not automate Google sign-in. That would be external,
account-dependent, and unsuitable for deterministic CI.

## Documentation

Update the browser launch tool description and the relevant launch/persona
documentation with:

- the field's Chromium-only scope;
- its false default;
- its process-lifetime behavior;
- the persistent-profile sign-in workflow; and
- the explicit warning that it changes one browser-exposed signal rather than
  providing general stealth.
