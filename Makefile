.PHONY: install uninstall test gates mypy ruff secrets verify

VENV := .venv

secrets:
	$(VENV)/bin/python scripts/secrets.py

install:
	@if [ "$$(id -u)" = "0" ]; then scripts/install.sh; else sudo scripts/install.sh; fi

uninstall:
	@if [ "$$(id -u)" = "0" ]; then scripts/uninstall.sh; else sudo scripts/uninstall.sh; fi

test:
	$(VENV)/bin/python -m pytest tests/ --ignore=tests/e2e -q

# V1.1: full verification — unit tests + E2E Playwright (isolated server on test DB).
# NEXTLIFE_WEB_TOKEN is auto-set by conftest; no production credentials needed.
verify:
	$(VENV)/bin/python -m pytest tests/ --ignore=tests/e2e -q
	$(VENV)/bin/python -m pytest tests/e2e/ -v

gates:
	$(VENV)/bin/python scripts/check_secrets.py
	$(VENV)/bin/python scripts/check_hardcode.py

mypy:
	$(VENV)/bin/python -m mypy src

ruff:
	$(VENV)/bin/ruff check src scripts
