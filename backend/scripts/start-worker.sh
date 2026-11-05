#!/bin/sh
set -e
# Migrations are idempotent and guarded by a Postgres advisory lock, so the API and the
# worker can both run them safely when they start at the same time.
echo "[start-worker] applying migrations"
alembic upgrade head
echo "[start-worker] starting Celery worker with embedded beat scheduler"
# Run exactly ONE worker replica: beat is embedded (-B). Task idempotency still protects
# against accidental duplicates.
exec celery -A app.workers.celery_app worker -B \
    --loglevel="${LOG_LEVEL:-INFO}" \
    --concurrency="${WORKER_CONCURRENCY:-2}" \
    -s /tmp/celerybeat-schedule
