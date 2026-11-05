#!/bin/sh
set -e
echo "[start-api] applying migrations"
alembic upgrade head
echo "[start-api] starting API on port ${PORT:-8000}"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips="*"
