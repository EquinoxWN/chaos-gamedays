.PHONY: setup lint test plans audit bench ci

PY ?= python

setup:
	$(PY) -m pip install --upgrade pip && $(PY) -m pip install -e ".[dev]"

# ruff, ruff format and mypy --strict.
lint:
	$(PY) -m ruff check . && $(PY) -m ruff format --check . && $(PY) -m mypy

# Schema, rules, sealing, evaluation and CLI tests (single process).
test:
	$(PY) -m pytest -q

# The shipped plans pass every rule, their briefs are current and the lock file matches them.
plans:
	gameday validate experiments/
	gameday brief experiments/ --out-dir docs/gamedays --check
	gameday verify experiments/ --lock experiments/plans.lock.json

# Known vulnerabilities in the installed Python dependencies.
audit:
	$(PY) -m pip_audit --skip-editable --cache-dir .tmp/pip-audit

bench:
	@echo "M2: fault injection with Chaos Mesh under k6 load; no benchmark numbers in M1"

ci: setup lint test plans
