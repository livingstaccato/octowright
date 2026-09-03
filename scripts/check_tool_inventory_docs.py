# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Fail when the documented MCP tool inventory has drifted from the registry.

``docs/architecture/mcp-tool-inventory.md`` names every tool by capability
profile and README.md advertises the totals. Both were typed by hand, and both
had drifted: two registered tools (``browser_a11y_dragdrop``,
``macro_artifact_delete``) were missing from the all-only list, so that section
claimed 27 where the registry held 29, and README's own table said the full
surface was ``129`` two lines below a sentence saying 131.

The tool surface is a contract — an agent's schema is built from it — so this
guard measures the live registry the same way ``check_telemetry_docs.py``
measures emitted instruments.

**The measurement runs in a child interpreter with its config dirs
redirected.** ``plugins.discovery.enabled_names`` treats an empty
``OCTOWRIGHT_PLUGINS`` as *unset* and falls through to the operator's
``plugins.yaml``, so an in-process read would count 138 tools on a maintainer
whose config enables the terminal plugin and 131 in CI. That ambient-config
split has already produced one assertion that failed locally and passed in CI.
Redirecting ``XDG_CONFIG_HOME``/``APPDATA`` in a child is the same isolation
``ci/run_terminal_plugin_tests.sh`` applies for the same reason.

The ``terminals`` profile is deliberately NOT compared against the registry:
it is declared by a plugin this measurement switches off by construction. Its
documented size is checked against the totals line instead.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "docs" / "architecture" / "mcp-tool-inventory.md"
README = ROOT / "README.md"

# Emitted by the child: the core (plugin-free) tool surface, partitioned.
_PROBE = """
import json
from octowright.server import registered_tool_names
from octowright.server import profiles as p

names = set(registered_tool_names())
scoped = {k: sorted(v) for k, v in p.PROFILES.items()}
always = sorted(p.ALWAYS_ON_TOOLS)
union = set().union(*p.PROFILES.values())
print(json.dumps({
    "total": len(names),
    "profiles": scoped,
    "always_on": always,
    "all_only": sorted(names - union - set(always)),
}))
"""

# `### `core` (24)` / `### Always-on meta + Advisor (7)` / `### All-only (29) — ...`
_SECTION_RE = re.compile(r"^### (?:`(?P<profile>[a-z]+)`|(?P<label>Always-on[^(]*|All-only))\s*\((?P<count>\d+)")
# `| `core` | 24 | ...` and `| _(always-on)_ | 7 | ...`
_TABLE_RE = re.compile(
    r"^\|\s*(?:`(?P<profile>[a-z]+)`|_\((?P<label>always-on|all-only)\)_)\s*\|\s*(?P<count>\d+)\s*\|"
)
_TOOL_RE = re.compile(r"`([a-z][a-z0-9_]+)`")
_TOTALS_RE = re.compile(
    r"\*\*(?P<scoped>\d+)\*\* profile-scoped \+ \*\*(?P<always>\d+)\*\* always-on "
    r"\+ \*\*(?P<all_only>\d+)\*\* all-only = \*\*(?P<total>\d+) total\*\*"
)
_README_TOTAL_RE = re.compile(r"every core-install tool registers\.\s*\|\s*(?P<total>\d+)\s*\|")
# The same sentence is retyped per document ("... is 131 tools on a core install").
_PROSE_TOTAL_RE = re.compile(r"(?P<total>\d+) tools on a core install")
# History records the counts that were true when it was written; dragging those
# forward would falsify the record, so the changelog and the plan archive are
# scanned past rather than corrected.
_HISTORY = ("CHANGELOG.md", "docs/superpowers/")
# Directory names that never hold canonical documentation, matched as path
# SEGMENTS at any depth rather than as prefixes. Prefix matching skipped
# `CHANGELOG.md` at the root and then scanned the identical file one directory
# down: a git worktree under `.claude/worktrees/` is a second copy of this
# repository, so `make lint` started failing with "says 129 tools" against a
# historical changelog in a checkout nobody was editing. Any dot-prefixed
# directory is skipped for the same reason (`.venv`, `.git`, `.claude`).
_SKIPPED_DIRS = frozenset({"mutants", "node_modules"})
DIAGRAM = ROOT / "docs" / "architecture" / "mcp-tool-surface.puml"
_DIAGRAM_TITLE_RE = re.compile(r"^title .*\((?P<total>\d+) tools total", re.MULTILINE)
# `package "core (24)" as P_CORE` / `package "all-only (29)" as P_ALL`
_DIAGRAM_PACKAGE_RE = re.compile(r'^\s*package "(?P<name>[a-z-]+) \((?P<count>\d+)\)"', re.MULTILINE)


def core_surface() -> dict:
    """Measure the plugin-free registry in a child with isolated config dirs."""
    with tempfile.TemporaryDirectory() as isolated:
        # Inherited rather than built from scratch: a minimal env is not portable
        # (Windows needs SYSTEMROOT et al. before a subprocess can even open a
        # socket), and the isolation this needs is narrow — redirect the config
        # roots platformdirs consults, and drop the one env var that outranks
        # them.
        env = dict(os.environ)
        env.pop("OCTOWRIGHT_PLUGINS", None)
        env.update(
            HOME=isolated,
            USERPROFILE=isolated,
            XDG_CONFIG_HOME=isolated,
            APPDATA=isolated,
            OCTOWRIGHT_RECORDINGS_DIR=str(Path(isolated) / "recordings"),
        )
        out = subprocess.run(
            [sys.executable, "-c", _PROBE],
            capture_output=True,
            text=True,
            check=True,
            env=env,
            cwd=isolated,
        )
    return json.loads(out.stdout.strip().splitlines()[-1])


def _is_skipped_doc_path(rel: str) -> bool:
    """True for a repo-relative path the total scan must not read.

    Two rules, both segment-based: the historical records (which state what was
    true when written), and directories that are copies, caches or vendored
    trees rather than sources.
    """
    if rel.startswith(_HISTORY):
        return True
    parts = rel.split("/")
    return any(part.startswith(".") or part in _SKIPPED_DIRS for part in parts[:-1])


def docs_claiming_a_total() -> list[Path]:
    """Every non-historical markdown file asserting a core-install tool count."""
    found = []
    for path in ROOT.rglob("*.md"):
        rel = path.relative_to(ROOT).as_posix()
        if _is_skipped_doc_path(rel):
            continue
        if _PROSE_TOTAL_RE.search(path.read_text(encoding="utf-8")):
            found.append(path)
    return sorted(found)


def core_tool_names() -> set[str]:
    """Every tool a core install registers with no profile filter."""
    surface = core_surface()
    return set(surface["all_only"]).union(surface["always_on"], *surface["profiles"].values())


def _documented_sections(text: str) -> dict[str, tuple[int, list[str]]]:
    """Map each ``### `` heading to its declared count and the tools listed under it."""
    sections: dict[str, tuple[int, list[str]]] = {}
    key: str | None = None
    count = 0
    for line in text.splitlines():
        match = _SECTION_RE.match(line)
        if match:
            label = match.group("label")
            key = match.group("profile") or ("always_on" if label and label.startswith("Always-on") else "all_only")
            count = int(match.group("count"))
            sections[key] = (count, [])
            continue
        if key and line.startswith("`"):
            sections[key] = (count, _TOOL_RE.findall(line))
            key = None
    return sections


def _documented_table(text: str) -> dict[str, int]:
    """Map each profile-table row to the count it advertises."""
    rows: dict[str, int] = {}
    for line in text.splitlines():
        match = _TABLE_RE.match(line)
        if match:
            label = match.group("label")
            key = match.group("profile") or (label or "").replace("-", "_")
            rows[key] = int(match.group("count"))
    return rows


def _check_group(name: str, documented: tuple[int, list[str]] | None, expected: list[str]) -> list[str]:
    if documented is None:
        return [f"{name}: no `### ` section found in the inventory"]
    count, listed = documented
    problems: list[str] = []
    if count != len(expected):
        problems.append(f"{name}: heading says {count}, the registry has {len(expected)}")
    missing = sorted(set(expected) - set(listed))
    extra = sorted(set(listed) - set(expected))
    if missing:
        problems.append(f"{name}: registered but not listed: {', '.join(missing)}")
    if extra:
        problems.append(f"{name}: listed but not registered: {', '.join(extra)}")
    return problems


def diagram_problems(diagram_text: str, surface: dict) -> list[str]:
    """Check the PlantUML source's title total and per-profile package counts.

    The diagram is the one place a wrong count is *rendered into an image* and
    committed, which a reader trusts more than prose — and it had drifted
    furthest of anything here (a title reading 126, an ``all-only (27)``).
    Only the counts are checked; the tool names inside each box are an
    abbreviated, deliberately-grouped rendering, not a list.
    """
    found: list[str] = []
    title = _DIAGRAM_TITLE_RE.search(diagram_text)
    if title is None:
        found.append("diagram: the '(<n> tools total ...)' title is missing")
    elif int(title.group("total")) != surface["total"]:
        found.append(f"diagram title: says {title.group('total')} tools total, the registry has {surface['total']}")

    packages = {m.group("name"): int(m.group("count")) for m in _DIAGRAM_PACKAGE_RE.finditer(diagram_text)}
    expected = {name: len(tools) for name, tools in surface["profiles"].items()}
    expected["all-only"] = len(surface["all_only"])
    for name, count in sorted(expected.items()):
        if name in packages and packages[name] != count:
            found.append(f"diagram package '{name}': says {packages[name]}, the registry has {count}")
    return found


def problems(
    inventory_text: str,
    readme_text: str,
    prose_totals: dict[Path, int] | None = None,
) -> list[str]:
    """Every way the documents disagree with the live registry.

    ``prose_totals`` maps a document to the core-install total it claims; it is
    discovered from disk when not supplied, and injected by the tests.
    """
    surface = core_surface()
    sections = _documented_sections(inventory_text)
    found: list[str] = []

    groups = dict(surface["profiles"])
    groups["always_on"] = surface["always_on"]
    groups["all_only"] = surface["all_only"]
    for name, expected in sorted(groups.items()):
        found.extend(_check_group(name, sections.get(name), expected))

    table = _documented_table(inventory_text)
    for name, expected in sorted(groups.items()):
        if name in table and table[name] != len(expected):
            found.append(f"{name}: profile table says {table[name]}, the registry has {len(expected)}")

    totals = _TOTALS_RE.search(inventory_text)
    if totals is None:
        found.append("inventory: the '<n> profile-scoped + <n> always-on + <n> all-only' totals line is missing")
    else:
        for group, label, actual in (
            ("scoped", "profile-scoped", len(set().union(*surface["profiles"].values()))),
            ("always", "always-on", len(surface["always_on"])),
            ("all_only", "all-only", len(surface["all_only"])),
            ("total", "total", surface["total"]),
        ):
            claimed = int(totals.group(group))
            if claimed != actual:
                found.append(f"inventory totals: {label} says {claimed}, the registry has {actual}")

    readme_total = _README_TOTAL_RE.search(readme_text)
    if readme_total is None:
        found.append("README: the capability-profile table's 'all (or unset)' row is missing")
    elif int(readme_total.group("total")) != surface["total"]:
        found.append(
            f"README: the 'all (or unset)' row says {readme_total.group('total')}, the registry has {surface['total']}"
        )

    if prose_totals is None:
        prose_totals = {}
        for path in docs_claiming_a_total():
            for match in _PROSE_TOTAL_RE.finditer(path.read_text(encoding="utf-8")):
                prose_totals[path] = int(match.group("total"))
    for path, claimed in sorted(prose_totals.items()):
        if claimed != surface["total"]:
            rel = path.relative_to(ROOT).as_posix() if path.is_absolute() else str(path)
            found.append(f"{rel}: says {claimed} tools on a core install, the registry has {surface['total']}")

    found.extend(diagram_problems(DIAGRAM.read_text(encoding="utf-8"), surface))
    return found


def main() -> int:
    found = problems(INVENTORY.read_text(encoding="utf-8"), README.read_text(encoding="utf-8"))
    if found:
        print("MCP tool inventory docs out of sync with the registry:")
        for problem in found:
            print(f"  {problem}")
        print("Run `uv run octowright selftest` and update docs/architecture/mcp-tool-inventory.md + README.md.")
        return 1
    print("OK: the documented tool inventory matches the registered tool surface")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
