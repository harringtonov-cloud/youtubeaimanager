# YouTube AI Manager

Автономная система управления YouTube-каналом (Shorts): AI сам находит идеи, пишет сценарии,
монтирует вертикальные видео, делает обложки и SEO, публикует по расписанию и учится на аналитике.

* **Frontend:** Next.js + TypeScript + Tailwind (Vercel)
* **Backend:** Python + FastAPI (Railway)
* **Worker/Scheduler:** Celery + Redis (Railway)
* **DB:** PostgreSQL
* **Video:** FFmpeg
* **AI:** любой OpenAI-compatible API (в т.ч. OpenRouter)

Подробности — в [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Инструкция первоначальной настройки
и деплоя будет дописана в разделе «Setup» по мере завершения этапов.

## Структура

```
backend/    FastAPI + Celery + FFmpeg пайплайн
frontend/   Next.js дашборд
docs/       документация
```

## Безопасность

Все ключи — только в переменных окружения (см. `.env.example`). Файл `.env` в `.gitignore`.
