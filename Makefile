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

# bootout alone only stops the running copy: the plist stays in LaunchAgents and
# launchd starts it again at the next login. Remove the plist too, or "stopped"
# is a promise this target cannot keep.
service-stop:
	@launchctl bootout gui/$$(id -u)/com.task-tracker 2>/dev/null || launchctl unload $(AGENT) 2>/dev/null || true
	@rm -f $(AGENT)
	@sleep 2
	@pkill -f "python -m bot.main" 2>/dev/null || true
	@if pgrep -f "python -m bot.main" >/dev/null 2>&1; then \
		echo "still running, forcing"; pkill -9 -f "python -m bot.main" || true; sleep 1; \
	fi
	@if pgrep -f "python -m bot.main" >/dev/null 2>&1; then \
		echo "FAILED to stop; something is still polling the token"; exit 1; \
	else \
		echo "stopped, and the agent is removed. It will not come back until: make service"; \
	fi

service-log:
	tail -f /tmp/task-tracker.log
