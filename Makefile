.PHONY: help install test test-frontend lint format typecheck audit vulture xenon secrets-scan mutmut precommit precommit-install act-lint act-test ci clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-22s\033[0m %s\n", $$1, $$2}'

install: ## uv sync --all-groups (deps + dev tools)
	uv sync --all-groups

test: ## Run Python unit + integration tests (no live browsers)
	uv run --active pytest -q tests/

test-frontend: ## Run TypeScript frontend tests with coverage gating
	cd packages/octowright-frontend && npm test

lint: ## Ruff/format, mypy, ty, bandit, codespell, SPDX, LOC, vulture, xenon, secrets-scan
	uv run --active ruff check .
	uv run --active ruff format --check .
	uv run --active mypy src/octowright
	uv run --active ty check src/octowright
	uv run --active bandit -q -r src/octowright -s B101,B110,B112,B404,B405,B603,B607
	uv run --active codespell --skip="src/octowright/server/frontend/*,./src/octowright/server/frontend/*"
	uv run --active python scripts/check_spdx_headers.py
	uv run --active python scripts/check_max_loc.py
	uv run --active python scripts/check_agent_docs_sync.py
	uv run --active python scripts/check_vulture.py
	uv run --active python scripts/check_xenon.py
	bash ci/run_detect_secrets.sh

audit: ## Run pip-audit against the dependency tree (uses .ci/pip-audit-allow.txt)
	uv run --active python scripts/check_pip_audit.py

vulture: ## Dead-code scan (baseline-ratchet, no new findings allowed)
	uv run --active python scripts/check_vulture.py

xenon: ## Cyclomatic-complexity scan (baseline-ratchet, no new violations allowed)
	uv run --active python scripts/check_xenon.py

secrets-scan: ## Re-run detect-secrets across the repo against .secrets.baseline
	bash ci/run_detect_secrets.sh

mutmut: ## Mutation testing on critical parsing/dispatch modules (slow; opt-in)
	# mutmut 3.x copies the project to mutants/ before running pytest; src-layout
	# packages need PYTHONPATH=src so the copied tree can import octowright.
	PYTHONPATH=src uv run --active mutmut run

spdx-fix: ## Normalize SPDX headers in source files
	uv run --active python scripts/normalize_spdx_headers.py

diagrams: ## Render docs/architecture/*.puml to SVG (requires `plantuml`)
	bash scripts/render_diagrams.sh docs/architecture

export-demos: ## Regenerate demo/tutorial-export/ from demo/bundles/ (no re-recording)
	uv run --active python scripts/demos/sync_exports.py

# Default per-macro-action delay used when re-recording. Slower playback is
# easier to follow by eye in the captured video. Override with e.g.
# `make rerecord-real-site-demos RERECORD_SLOWMO_MS=0` for native speed.
RERECORD_SLOWMO_MS ?= 500

rerecord-real-site-demos: ## Re-record the Wikipedia-targeted bundles (cross-engine-trio, macro-replay-loop). Needs Playwright browsers + network.
	OCTOWRIGHT_MACRO_SLOWMO_MS=$(RERECORD_SLOWMO_MS) uv run --active python scripts/demos/record_demo.py cross-engine-trio
	OCTOWRIGHT_MACRO_SLOWMO_MS=$(RERECORD_SLOWMO_MS) uv run --active python scripts/demos/record_demo.py macro-replay-loop

rerecord-playground-demos: ## Re-record the playground-targeted bundles (seven-mix-orchestration, role-based-duo). Needs Playwright browsers.
	OCTOWRIGHT_MACRO_SLOWMO_MS=$(RERECORD_SLOWMO_MS) uv run --active python scripts/demos/with_playground.py seven-mix-orchestration
	OCTOWRIGHT_MACRO_SLOWMO_MS=$(RERECORD_SLOWMO_MS) uv run --active python scripts/demos/with_playground.py role-based-duo

format: ## Apply ruff format + ruff --fix
	uv run --active ruff format .
	uv run --active ruff check --fix .

typecheck: ## mypy only
	uv run --active mypy src/octowright

typecheck-ty-probe: ## Non-gating: probe broader ty coverage and collect remaining baseline errors
	uv run --active ty check src/octowright

precommit: ## Run pre-commit on all files
	uv run --active pre-commit run --all-files

precommit-install: ## Install the pre-commit hooks into .git/hooks
	uv run --active pre-commit install --install-hooks
	uv run --active pre-commit install --hook-type commit-msg

act-lint: ## Run the lint job locally via act
	env -u DOCKER_HOST act -j lint --rm

act-test: ## Run the test job locally via act (slow on Apple Silicon — Playwright install + amd64 emulation)
	env -u DOCKER_HOST act -j test --rm

ci: lint audit test ## Local equivalent of CI: lint + pip-audit + tests

clean: ## Remove caches + recordings + build artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage .coverage.* .hypothesis dist build *.egg-info
	rm -rf recordings profiles .ci
