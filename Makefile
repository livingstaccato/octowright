.PHONY: help install test lint format typecheck precommit precommit-install act-lint act-test ci clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-22s\033[0m %s\n", $$1, $$2}'

install: ## uv sync --all-groups (deps + dev tools)
	uv sync --all-groups

test: ## Run unit + integration tests (no live browsers)
	uv run --active pytest -q tests/

lint: ## Ruff lint, ruff format check, mypy, codespell
	uv run --active ruff check .
	uv run --active ruff format --check .
	uv run --active mypy src/octowright
	uv run --active codespell

format: ## Apply ruff format + ruff --fix
	uv run --active ruff format .
	uv run --active ruff check --fix .

typecheck: ## mypy only
	uv run --active mypy src/octowright

precommit: ## Run pre-commit on all files
	uv run --active pre-commit run --all-files

precommit-install: ## Install the pre-commit hooks into .git/hooks
	uv run --active pre-commit install --install-hooks
	uv run --active pre-commit install --hook-type commit-msg

act-lint: ## Run the lint job locally via act
	act -j lint --rm

act-test: ## Run the test job locally via act (slow on Apple Silicon — Playwright install + amd64 emulation)
	act -j test --rm

ci: lint test ## Local equivalent of CI: lint + tests

clean: ## Remove caches + recordings + build artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage .coverage.* .hypothesis dist build *.egg-info
	rm -rf recordings profiles .ci
