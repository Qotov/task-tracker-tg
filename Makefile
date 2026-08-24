.PHONY: test lint fmt run sync service service-stop service-log

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

# --- keeping it running on this Mac -----------------------------------------

AGENT = $(HOME)/Library/LaunchAgents/com.task-tracker.plist

service:
	@pkill -f "python -m bot.main" 2>/dev/null || true
	cp deploy/com.task-tracker.plist $(AGENT)
	@launchctl bootstrap gui/$$(id -u) $(AGENT) 2>/dev/null || launchctl load $(AGENT)
	@sleep 3
	@echo "started; follow it with: make service-log"

service-stop:
	@launchctl bootout gui/$$(id -u)/com.task-tracker 2>/dev/null || launchctl unload $(AGENT)
	@echo "stopped. It will not come back until: make service"

service-log:
	tail -f /tmp/task-tracker.log
