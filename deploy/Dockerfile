# IHSG MA Screener bot — container image (§8).
# Multi-arch friendly (works on Oracle Cloud ARM Ampere and x86 GCP).
FROM python:3.11-slim

# Asia/Jakarta so the 16:30 WIB scan schedule and "today's bar" logic are right.
ENV TZ=Asia/Jakarta \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# tini = clean signal handling / zombie reaping for a long-running process.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tini tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first for layer caching.
COPY requirements.txt .
RUN pip install -r requirements.txt

# App code. .dockerignore keeps .env, .venv, data/, caches out of the image.
COPY . .

# Data + logs are mounted as volumes (see docker-compose.yml).
RUN mkdir -p /app/data /app/logs
ENV LOG_FILE=/app/logs/bot.log \
    DB_PATH=/app/data/cache.sqlite

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "bot.main"]
