# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Aggregate token-comparison benchmark runs (Playwright MCP vs Octowright).

Reads a CSV of per-run results and prints a markdown comparison: per
(model, backend) averages, plus the per-model Octowright-vs-Playwright delta.

CSV columns (header required):

    model,backend,total_tokens,wall_clock_s,tool_calls,correct

  - backend: "playwright" or "octowright" (case-insensitive; a "-mcp" suffix is ok)
  - correct: 1 / 0  (also accepts true/false, yes/no, pass)
  - one row per run; do >=3 trials per (model, backend) and this averages them.
  - lines whose model starts with "#" are treated as comments.

Usage:
    python aggregate.py [runs.csv]      # defaults to ./runs.csv next to this file
    # If the CSV doesn't exist, a fill-in template is written for you.
"""

from __future__ import annotations

import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path

TEMPLATE = """model,backend,total_tokens,wall_clock_s,tool_calls,correct
# one row per run; backend = playwright | octowright ; correct = 1|0
# (delete these example rows once you start logging real runs)
claude-opus-4-8,playwright,0,0,0,1
claude-opus-4-8,octowright,0,0,0,1
"""


def _norm_backend(b: str) -> str:
    b = b.strip().lower().removesuffix("-mcp").removesuffix(" mcp").strip()
    if b.startswith("playwright"):
        return "playwright"
    if b.startswith("octowright"):
        return "octowright"
    return b


def _truthy(v: str) -> bool:
    return v.strip().lower() in {"1", "true", "yes", "y", "pass"}


def _load(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            model = (r.get("model") or "").strip()
            if not model or model.startswith("#"):
                continue
            try:
                rows.append(
                    {
                        "model": model,
                        "backend": _norm_backend(r["backend"]),
                        "tokens": float(r["total_tokens"]),
                        "secs": float(r["wall_clock_s"]),
                        "calls": float(r["tool_calls"]),
                        "correct": _truthy(r["correct"]),
                    }
                )
            except (KeyError, ValueError) as exc:
                print(f"skipping bad row {r}: {exc}", file=sys.stderr)
    return rows


def _agg(rs: list[dict]) -> dict:
    return {
        "n": len(rs),
        "tokens": statistics.mean(x["tokens"] for x in rs),
        "secs": statistics.mean(x["secs"] for x in rs),
        "calls": statistics.mean(x["calls"] for x in rs),
        "correct_pct": 100.0 * sum(x["correct"] for x in rs) / len(rs),
    }


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else Path(__file__).with_name("runs.csv")
    if not path.exists():
        path.write_text(TEMPLATE, encoding="utf-8")
        print(f"wrote template {path} — fill it in (drop the example rows) and re-run.")
        return 0

    rows = _load(path)
    if not rows:
        print("no usable rows in", path)
        return 1

    by_cell: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        by_cell[(r["model"], r["backend"])].append(r)
    cell = {k: _agg(v) for k, v in by_cell.items()}

    print("## Per (model, backend) — averages\n")
    print("| model | backend | trials | avg tokens | avg s | avg tool calls | correct % |")
    print("|---|---|--:|--:|--:|--:|--:|")
    for model, backend in sorted(cell):
        a = cell[(model, backend)]
        print(
            f"| {model} | {backend} | {a['n']} | {a['tokens']:.0f} | "
            f"{a['secs']:.1f} | {a['calls']:.1f} | {a['correct_pct']:.0f}% |"
        )

    print("\n## Octowright vs Playwright (per model)\n")
    print("| model | pw tokens | octo tokens | Δ tokens | Δ % | Δ tool calls |")
    print("|---|--:|--:|--:|--:|--:|")
    for m in sorted({model for model, _ in cell}):
        pw = cell.get((m, "playwright"))
        oc = cell.get((m, "octowright"))
        pw_t = f"{pw['tokens']:.0f}" if pw else "—"
        oc_t = f"{oc['tokens']:.0f}" if oc else "—"
        if not (pw and oc):
            print(f"| {m} | {pw_t} | {oc_t} | _(need both backends)_ | | |")
            continue
        dt = oc["tokens"] - pw["tokens"]
        dpct = (100.0 * dt / pw["tokens"]) if pw["tokens"] else float("nan")
        dcalls = oc["calls"] - pw["calls"]
        print(f"| {m} | {pw_t} | {oc_t} | {dt:+.0f} | {dpct:+.1f}% | {dcalls:+.1f} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
