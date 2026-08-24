# For any container host — fly.io, Railway, a Synology, a Pi with docker.
#
# The database lives on a mounted volume at /data, never in the image: a
# redeploy must not take your tasks with it.

FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends sqlite3 ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first, so a code change does not re-resolve them.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY bot ./bot
COPY scripts ./scripts

# Long polling: no port is exposed and nothing listens.
ENV DB_PATH=/data/tasks.db
VOLUME ["/data"]

CMD ["uv", "run", "--no-dev", "python", "-m", "bot.main"]
