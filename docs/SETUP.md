# Первоначальная настройка (один раз)

После этих шагов система работает сама: ищет идеи, пишет сценарии, монтирует Shorts, делает обложки и SEO,
публикует по расписанию, собирает аналитику и учится на ней. Браузер можно закрывать.

Архитектура деплоя:

| Часть | Где | Что это |
|---|---|---|
| Dashboard | **Vercel** (root: `frontend`) | Next.js, ходит в API серверным прокси |
| API | **Railway**, сервис `api` (root: `backend`) | FastAPI, миграции при старте |
| Worker | **Railway**, сервис `worker` (root: `backend`) | Celery + планировщик (beat), FFmpeg |
| Postgres, Redis | **Railway** (плагины) | состояние и очередь |
| Файлы (видео/обложки) | **S3-совместимое хранилище** | Cloudflare R2 / Backblaze B2 / Railway Bucket / AWS S3 |

> ⚠️ Хранилище S3 **обязательно** на Railway: API и Worker — разные контейнеры с временными дисками.
> Пока `STORAGE_BACKEND=local` на Railway, включить автопубликацию система не даст (проверка готовности).

---

## 1. Секреты и переменные окружения

Секреты хранятся **только** в переменных окружения Railway/Vercel. В GitHub их нет (`.env` в `.gitignore`).

### Railway: сервисы `api` и `worker` (одинаковые значения в обоих)

| Переменная | Обязательно | Что это / где взять |
|---|---|---|
| `DATABASE_URL` | да | `${{Postgres.DATABASE_URL}}` (ссылка на плагин Postgres) |
| `REDIS_URL` | да | `${{Redis.REDIS_URL}}` (ссылка на плагин Redis) |
| `ADMIN_TOKEN` | да | Длинная случайная строка — ваш пароль в Dashboard. `python -c "import secrets;print(secrets.token_urlsafe(40))"` |
| `SECRET_KEY` | да | Другая случайная строка: шифрует YouTube-токен в БД. **Не меняйте после подключения** (иначе придётся переподключить YouTube) |
| `BACKEND_PUBLIC_URL` | да | Публичный URL сервиса `api`, например `https://yam-api.up.railway.app` |
| `FRONTEND_URL` | да | URL Dashboard на Vercel, например `https://yam.vercel.app` |
| `TIMEZONE` | нет | Часовой пояс расписания, например `Asia/Tashkent` (можно менять и в AI Settings) |
| `LLM_BASE_URL` | да | `https://openrouter.ai/api/v1` или `https://api.openai.com/v1` |
| `LLM_API_KEY` | да | Ключ OpenRouter / OpenAI |
| `LLM_MODEL` | да | например `openai/gpt-4o-mini` (OpenRouter) или `gpt-4o-mini` (OpenAI) |
| `LLM_FALLBACK_MODEL` | нет | запасная модель |
| `GOOGLE_CLIENT_ID` | да | OAuth-клиент Google (шаг 3) |
| `GOOGLE_CLIENT_SECRET` | да | OAuth-клиент Google (шаг 3) |
| `STORAGE_BACKEND` | да | `s3` |
| `S3_ENDPOINT_URL` | для R2/B2 | R2: `https://<account_id>.r2.cloudflarestorage.com` |
| `S3_BUCKET` | да | имя бакета |
| `S3_ACCESS_KEY_ID` | да | ключ доступа к бакету |
| `S3_SECRET_ACCESS_KEY` | да | секрет доступа к бакету |
| `S3_REGION` | нет | `auto` для R2 |
| `PEXELS_API_KEY` | рекомендуется | бесплатный ключ https://www.pexels.com/api/ — лицензионные стоковые видео |
| `PIXABAY_API_KEY` | нет | бесплатный ключ https://pixabay.com/api/docs/ |
| `TTS_PROVIDER` | нет | `edge` (по умолчанию, бесплатно, без ключа) или `openai` |
| `TTS_API_KEY`, `TTS_BASE_URL`, `TTS_MODEL`, `TTS_VOICE` | только для `openai` | |
| `YOUTUBE_API_KEY` | нет | для чтения трендов YouTube (без него тренды берутся из Google Trends и Wikipedia) |

Без `PEXELS_API_KEY`/`PIXABAY_API_KEY` видео всё равно создаются: на анимированных сгенерированных фонах.

### Vercel: проект Dashboard

| Переменная | Значение |
|---|---|
| `BACKEND_URL` | публичный URL сервиса `api` на Railway (тот же, что `BACKEND_PUBLIC_URL`) |

---

## 2. Railway (Backend + Worker)

1. Railway → **New Project** → **Deploy from GitHub repo** → `harringtonov-cloud/youtubeaimanager`.
2. Добавьте плагины **PostgreSQL** и **Redis** (New → Database).
3. Первый сервис → переименуйте в `api`. Settings → **Root Directory** = `backend`. Config-as-code берётся из `backend/railway.toml` автоматически. Settings → Networking → **Generate Domain**. Это и есть `BACKEND_PUBLIC_URL`.
4. New → **GitHub Repo** (тот же репозиторий) → сервис `worker`. Root Directory = `backend`, Settings → **Config-as-code → Config file path** = `/backend/railway.worker.toml`. Домен для worker не нужен. Реплика должна быть **одна** (в нём встроен планировщик).
5. В обоих сервисах задайте переменные из таблицы выше. Новые значения → **Deploy**.
6. Проверка: `https://<ваш-api-домен>/api/health` → `{"status":"ok","database":true,"redis":true,...}`.

## 3. Google Cloud (YouTube OAuth)

1. https://console.cloud.google.com → создайте проект.
2. **APIs & Services → Library**: включите **YouTube Data API v3** и **YouTube Analytics API**.
3. **OAuth consent screen**: тип **External**, заполните название и email. Scopes: `.../auth/youtube`, `.../auth/youtube.upload`, `.../auth/yt-analytics.readonly`. Добавьте свой Google-аккаунт в **Test users**.
4. Нажмите **Publish app** (статус *In production*). Иначе, пока приложение в режиме *Testing*, Google отзывает refresh-токен через 7 дней, и автономность сломается. При входе Google покажет «приложение не проверено»: **Advanced → Go to … (unsafe)**. Для личного канала это нормально.
5. **Credentials → Create credentials → OAuth client ID → Web application**.
   Authorized redirect URI: `https://<ваш-api-домен>/api/youtube/callback` (точное значение показано в Dashboard → YouTube Connection).
6. Скопируйте Client ID и Client secret в переменные Railway `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`.

### ⚠️ Важное ограничение YouTube API
Видео, загруженные через `videos.insert` из **непроверенного** API-проекта, YouTube может принудительно делать **приватными**
(«locked as private»). Если после публикации ролики остаются приватными, подайте форму
**YouTube API Services — Audit and Quota Extension** (https://support.google.com/youtube/contact/yt_api_form).
Это ограничение на стороне Google, из кода его обойти нельзя. До одобрения вы можете вручную менять видимость
в YouTube Studio, а система будет работать как обычно.
Также действует суточная квота (по умолчанию 10 000 единиц; загрузка видео ≈ 1600), то есть примерно 6 загрузок в день. Система учитывает квоту и ждёт её сброса.

## 4. Хранилище файлов (пример: Cloudflare R2)

1. Cloudflare → R2 → **Create bucket** (например `yam-media`).
2. R2 → **Manage API tokens** → создайте токен с правами Object Read & Write на этот бакет.
3. Задайте в Railway: `STORAGE_BACKEND=s3`, `S3_ENDPOINT_URL=https://<account_id>.r2.cloudflarestorage.com`, `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_REGION=auto`.

## 5. Vercel (Dashboard)

1. Vercel → **Add New → Project** → тот же репозиторий.
2. **Root Directory** = `frontend`. Framework: Next.js (определится сам).
3. Environment Variables: `BACKEND_URL` = URL сервиса `api`.
4. Deploy. Скопируйте адрес проекта в переменную Railway `FRONTEND_URL` и сделайте Redeploy сервиса `api`.

## 6. Первый запуск (в Dashboard)

1. Откройте Dashboard → войдите по `ADMIN_TOKEN`.
2. **YouTube Connection** → **Connect YouTube** → выберите аккаунт и канал → разрешите доступ. Вернётесь в Dashboard со статусом *connected*.
3. **AI Settings**: задайте нишу, язык, время публикаций (`09:00, 18:00`), часовой пояс. **Save**.
4. На **Overview** нажмите «Find ideas now» → «Run planner now». Через несколько минут в **Video Studio** появятся ролики в работе.
   Посмотрите первые 2–3 видео и обложки (Video Studio → клик по видео). Пока автопубликация выключена, система делает не больше нескольких готовых видео про запас.
5. **AI Settings → Autonomous publishing → Check & turn ON.** Система выполнит живую проверку (YouTube, LLM, worker, хранилище, расписание) и включит автопубликацию только если всё в порядке. Если что-то не прошло, увидите, что именно.

Готово. Дальше всё идёт само: планировщик каждые 5 минут держит календарь заполненным, публикует по расписанию, каждые 3 часа
собирает статистику, а ночью AI анализирует результаты и корректирует следующие идеи и сценарии.

Хотите контролировать каждый ролик? Включите «Require my approval before publishing»: видео будут ждать вашего Approve.

---

## Локальный запуск (необязательно)

```bash
cp .env.example .env     # заполните ADMIN_TOKEN, SECRET_KEY, LLM_API_KEY, ...
docker compose up --build
# Dashboard: http://localhost:3000   API: http://localhost:8000/api/health
```

## Если что-то не работает

| Симптом | Причина / решение |
|---|---|
| Overview: «Worker offline» | сервис `worker` не запущен или упал: смотрите его логи; `REDIS_URL` должен совпадать с `api` |
| «LLM_API_KEY is not set» | задайте переменную в **обоих** сервисах |
| YouTube: `needs_reauth` | токен отозван/истёк. Если приложение было в режиме Testing, опубликуйте его и переподключите |
| «Auto-publish cannot be enabled» | в ответе перечислены непройденные проверки (YouTube, хранилище и т. д.) |
| Видео загружаются, но приватные | непроверенный API-проект (см. ограничение выше) |
| Логи | Dashboard → System Logs; все ошибки и ретраи там же |
