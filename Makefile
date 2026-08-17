.PHONY: test lint fmt run sync

sync:
	uv sync

test:
	uv run ruff check .
	uv run mypy
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff format .
	uv run ruff check --fix .

run:
	uv run python -m bot.main
