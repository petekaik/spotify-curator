FROM python:3.12-slim

LABEL maintainer="Pomo" \
      project="spotify-curator" \
      description="Hermes Agent with Spotify Curator skill and tools"

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Hermes Agent
# Pin to v0.15.x for reproducibility — bump deliberately when ready
RUN pip install --no-cache-dir "hermes-agent>=0.15,<0.16"

# Spotify Curator code
WORKDIR /opt/project
COPY pyproject.toml README.md ./
COPY src ./src
COPY skills ./skills
RUN pip install --no-cache-dir -e ".[dev]"

# Persistent Hermes home (config, sessions, skills, memories, logs)
ENV HERMES_HOME=/opt/data
ENV HOME=/opt/data
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Hermes data dir
RUN mkdir -p /opt/data /opt/data/skills /opt/data/sessions /opt/data/logs /opt/data/memories
# Mount point for spotify-curator project code (we already COPYed it,
# but we also bind-mount at runtime for live editing)

# Tools dir
RUN mkdir -p /opt/project/scripts

# Healthcheck — Hermes gateway exposes an API server when started
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8642/health || exit 1

# Default command: launch Hermes with the spotify-curator skill preloaded,
# running the gateway so it can be controlled from Telegram/Discord/etc.
# Override with `docker compose run hermes-spotify-curator hermes chat -q "..."`
# for one-shot operations.
CMD ["hermes", "gateway", "run"]
