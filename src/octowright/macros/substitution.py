# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import SplitResult, urlsplit

# The sink set, the opt-out and the expander live in ``octowright.credential_sinks``
# so the exported CLI runs the same rules; the first two are re-exported here.
from octowright.credential_sinks import CREDENTIAL_UNSAFE_KEYS as CREDENTIAL_UNSAFE_KEYS
from octowright.credential_sinks import Origin, dispatch_fields, expand_actions, url_origin
from octowright.credential_sinks import credential_sinks_blocked as credential_sinks_blocked
from octowright.defaults import new_tab_url
from octowright.macros.privacy import PLACEHOLDER_RE, is_credential_key

if TYPE_CHECKING:
    from octowright.session._protocols import SessionLike

SEMANTIC_LOCATOR_KEYS = (
    "role",
    "role_name",
    "label",
    "text",
    "test_id",
    "role_exact",
    "label_exact",
    "text_exact",
)
#: The keys that can actually RESOLVE an element. build_locator requires
#: exactly one of these; everything else in SEMANTIC_LOCATOR_KEYS is a modifier
#: (`role_name` narrows a role, the `*_exact` flags narrow the match), so
#: "has a semantic key" must never be read as "has a locator".
SEMANTIC_FINDER_KEYS = ("role", "label", "text", "test_id")
# Keys that carry no human-readable ARIA text and so are noise in a digest.
# The *_exact flags are modifiers on another key, never a name themselves.
NON_ARIA_NOISE_KEYS = ("role", "role_name", "test_id", "role_exact", "label_exact", "text_exact")
# Bookkeeping the recorder and the scenario layer stamp onto an event. None is
# an input to any session method, so all of them are stripped before dispatch.
# `persona` and `scenario_role` come from scenarios_pool, which stamps both onto
# every merged tail event; without them here, replaying a scenario-derived
# recording raises TypeError: navigate() got an unexpected keyword argument.
#
# `scenario_role` is spelled that way BECAUSE stripping cannot fix a collision.
# Writing the label to `role` collides: `role` is also the ARIA locator key
# on click/fill/click_by/fill_by/get_text_by — and `strip_non_aria_noise` returns
# those actions untouched precisely because `role` is their locator. So the label
# would both destroy a recorded ARIA role and inject one where there was none, with
# nothing downstream able to tell the two apart. Renaming at the source is the
# only fix; do not re-add a bare `role` here.
RECORDING_NOISE_KEYS = ("action", "ts", "kind", "profile", "instance_id", "persona", "scenario_role")


def normalise_parameters(parameters: list[str] | dict[str, str] | None) -> dict[str, str]:
    if parameters is None:
        return {}
    if isinstance(parameters, dict):
        return parameters
    return {f"params[{i}]": v for i, v in enumerate(parameters)}


def substitute_in_action(action: dict[str, Any], value_to_name: dict[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in action.items():
        if isinstance(value, str) and value in value_to_name:
            result[key] = "{{" + value_to_name[value] + "}}"
        else:
            result[key] = value
    return result


def action_kwargs(action: dict[str, Any]) -> dict[str, Any]:
    # The credential-fill guard's inputs go too: no session method takes them.
    return {key: value for key, value in dispatch_fields(action).items() if key not in RECORDING_NOISE_KEYS}


#: Actions whose locator IS the semantic keys, so stripping them would remove
#: the thing the action matches on. Enumerated rather than inferred, and it has
#: already been missed once: get_text_by was absent, so replaying one called
#: session.get_text_by() with no finder at all.
_SEMANTIC_ACTIONS = {"click", "fill", "click_by", "fill_by", "get_text_by", "upload_files"}


def strip_non_aria_noise(kind: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    if kind in _SEMANTIC_ACTIONS:
        return dict(kwargs)

    cleaned = dict(kwargs)
    for key in NON_ARIA_NOISE_KEYS:
        cleaned.pop(key, None)
    return cleaned


#: Arg names whose value is treated as a secret. Deliberately name-based: the
#: substituter sees opaque caller-supplied args and has no other signal, and
#: matching on the name is what lets ``{{order_id}}`` keep working in a URL
#: (the common parameterized-navigation pattern) while ``{{password}}`` does
#: not.
#: Delegated to the shared vocabulary rather than owned here. This regex used
#: to be a third, independently-maintained credential list, and it had drifted:
#: it knew ``otp`` (which neither redactor did) while missing ``passphrase``,
#: ``pw``, ``pwd``, ``authorization``, ``access_key`` and ``private_key`` -- so
#: ``{{passphrase}}`` could expand into a URL while ``{{password}}`` was
#: refused. The sink guard acts on the CREDENTIAL tier only, which is what
#: keeps identity args working in a parameterized URL.


def is_credential_arg(name: str) -> bool:
    return is_credential_key(name)


def _is_octowright_new_tab(parts: SplitResult) -> bool:
    """Whether *parts* is the daemon's own new-tab page, on any loopback spelling."""
    # Imported here: the exposure module brings the Starlette request stack,
    # and the substituter is loaded by callers that never serve HTTP.
    from octowright.http.exposure import is_loopback_host

    own = urlsplit(new_tab_url())
    try:
        port = parts.port
    except ValueError:
        return False
    return is_loopback_host(parts.hostname) and port == own.port and parts.path.rstrip("/") == own.path


def own_site_origins(session: SessionLike) -> set[Origin]:
    """The origins the operator, not the macro, pointed this session at.

    The launch URL and the persona ``base_url`` are chosen by whoever launched
    the browser, and both are captured at launch and never written again.
    ``session.url`` is NOT one of them: it follows every navigate, so reading
    it let a poisoned macro navigate to its own server and then name it.

    An origin, not a host: trusting ``localhost`` for a launch at
    ``http://localhost:3000`` also trusted whatever listened on
    ``localhost:45678``, and another local user can be that listener.

    octowright's own new-tab page (a launch with no URL) is not an app, though a
    local dev stack on ``localhost`` is. An ``OCTOWRIGHT_DEFAULT_URL`` naming
    the operator's app is operator-chosen like any launch URL, so only the
    daemon's page is excluded, not whatever the no-URL launch landed on.
    """
    origins: set[Origin] = set()
    for url in (session.launch_url, session.base_url):
        origin = url_origin(url)
        if origin is not None and not _is_octowright_new_tab(urlsplit(str(url))):
            origins.add(origin)
    return origins


def substitute(
    actions: list[dict[str, Any]],
    args: dict[str, Any],
    *,
    trusted_origins: frozenset[Origin] | set[Origin] = frozenset(),
) -> list[dict[str, Any]]:
    """Expand ``{{name}}`` placeholders, refusing a credential in a sink.

    *trusted_origins* (``own_site_origins(session)``) is the one exemption: a
    credential in the ``headers`` of an action whose pattern spells out one of
    them. The rules are ``octowright.credential_sinks``'s, shared with the
    exported CLI.
    """
    return expand_actions(
        actions, args, is_credential=is_credential_arg, placeholder=PLACEHOLDER_RE, trusted_origins=trusted_origins
    )
