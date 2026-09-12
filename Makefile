.DEFAULT_GOAL := help
.PHONY: help setup test build smoke check-installed check-launchers

ifeq ($(OS),Windows_NT)
SCOPE_SMOKE = pwsh -NoLogo -NoProfile -File scripts/smoke.ps1 --shell powershell
else
SCOPE_SMOKE = sh scripts/smoke.sh
endif

help:
	@echo "Targets: setup, test, build, smoke, check-installed, check-launchers"
	@echo "All commands also work directly with uv; make is optional."

setup:
	uv sync --locked

test:
	uv run pytest -q

build:
	uv build

smoke:
	$(SCOPE_SMOKE)

check-installed:
	uv sync --locked --no-editable
	uv run --no-sync python -I scripts/check-package.py
	uv run --no-sync python -I scripts/check-demo.py

check-launchers:
	uv run --no-sync python -I scripts/check-launcher.py
