.PHONY: lint lint-fix test coverage

VENV := ./env

lint:
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .

lint-fix:
	$(VENV)/bin/ruff check . --fix
	$(VENV)/bin/ruff format .

test:
	$(VENV)/bin/python -m pytest tests/ -v

coverage:
	$(VENV)/bin/python -m pytest tests/ -v --cov=app --cov-report=term-missing --cov-report=html
