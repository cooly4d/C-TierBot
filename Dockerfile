# C-TierBot container image, built by Coolify from this repo.
# Python 3.13 matches production (audioop-lts in requirements.txt is a 3.13+ backport).
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DB_PATH=/app/data/leaderboard.db \
    CONSOLE_PORT=8765

WORKDIR /app

# Dependencies first, so a code-only change reuses this layer.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Code, fonts and item icons (.dockerignore keeps secrets, the local DB, logs and tests out).
COPY . .

# leaderboard.db lives in /app/data: Coolify mounts persistent storage there. No VOLUME line, so a
# missing mount shows up as an empty DB instead of a silent throwaway volume on every redeploy.

CMD ["python", "leaderboard_bot.py"]
