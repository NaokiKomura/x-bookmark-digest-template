.PHONY: setup fmt check test

setup:
	uv sync

fmt:
	uv run ruff format .
	uv run ruff check --fix .

test:
	uv run pytest

# コミット前に必ず通す
check:
	uv run ruff check .
	uv run ruff format --check .
	uv run pytest
