# Бесплатный запуск (без компьютера, всё с телефона)

Схема, где за всё это не нужно платить:

| Часть | Где | Зачем |
|---|---|---|
| **Работа системы** (идеи, сценарии, монтаж, публикация, аналитика) | **GitHub Actions**, каждые 30 минут | заменяет платный worker. Репозиторий публичный, поэтому минуты безлимитны |
| База данных | **Supabase** (бесплатно) | PostgreSQL |
| Хранилище видео и обложек | **Supabase Storage** (бесплатно) | S3-совместимый бакет |
| Панель управления и API | **Render, бесплатный тариф** | открываете панель на телефоне |
| Нейросеть | **OpenRouter** | дёшево (копейки) или бесплатные модели, см. ниже |

> Это бесплатно, но с компромиссами. Прочитайте раздел «Ограничения» внизу.

Включите в браузере телефона «Версия для ПК» (Desktop site), так сайты Google и Supabase удобнее.

---

## Шаг 1. Придумайте два секрета и запишите
* `ADMIN_TOKEN` — ваш пароль в панель (20+ символов).
* `SECRET_KEY` — другая длинная случайная строка. **Одинаковая** в Render и в GitHub, потом не менять.

## Шаг 2. Supabase (база + хранилище)
1. https://supabase.com/dashboard → **New project**. Придумайте пароль базы (запишите) и выберите регион поближе.
2. **База.** В проекте нажмите **Connect** → вкладка **Session pooler** → скопируйте строку `postgresql://postgres.xxxx:[YOUR-PASSWORD]@...pooler.supabase.com:5432/postgres`, подставьте свой пароль вместо `[YOUR-PASSWORD]`.
   Это значение `DATABASE_URL`. Нужен именно Session pooler: прямое подключение работает только по IPv6, а GitHub и Render его не поддерживают.
3. **Хранилище.** Storage → **New bucket** → имя `media`, оставьте **приватным**. Это `S3_BUCKET`.
4. Storage → **S3 Connection** (или Project Settings → Storage): там показаны **Endpoint** (`https://<проект>.supabase.co/storage/v1/s3`) и **Region**. Это `S3_ENDPOINT_URL` и `S3_REGION`.
   Нажмите **New access key**: получите `S3_ACCESS_KEY_ID` и `S3_SECRET_ACCESS_KEY` (секрет показывают один раз).

## Шаг 3. Ключи нейросети и видео
| Что | Ссылка | Значение |
|---|---|---|
| Нейросеть | https://openrouter.ai/keys (Create Key) | `LLM_API_KEY` |
| Стоковые видео (бесплатно) | https://www.pexels.com/api/ | `PEXELS_API_KEY` |

Платить за нейросеть необязательно: на OpenRouter есть бесплатные модели (в списке моделей включите фильтр **Free**, у названия будет `:free`).
Тогда впишите её название в `LLM_MODEL`. Минусы: низкий суточный лимит запросов и менее стабильный JSON-ответ.
По умолчанию стоит дешёвая платная модель `openai/gpt-4o-mini`. Пополните баланс на https://openrouter.ai/credits на несколько долларов.

## Шаг 4. Render: панель и API
1. https://render.com → зарегистрируйтесь (удобно через GitHub).
2. https://dashboard.render.com/blueprints → **New Blueprint Instance** → репозиторий `harringtonov-cloud/youtubeaimanager`, ветка `main`.
3. Render покажет форму с секретами. Впишите:
   `DATABASE_URL`, `ADMIN_TOKEN`, `SECRET_KEY`, `LLM_API_KEY`, `S3_ENDPOINT_URL`, `S3_REGION`, `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`.
   В `GOOGLE_CLIENT_ID` и `GOOGLE_CLIENT_SECRET` пока поставьте любой символ, настоящие впишете на шаге 6.
4. **Apply**. Сборка идёт 5–15 минут.
5. Откройте сервисы `yam-api` и `yam-web`: вверху у каждого есть адрес вида `https://yam-api-xxxx.onrender.com`. Запишите оба.
6. В сервисе `yam-web` → **Environment** → добавьте `BACKEND_URL` = адрес `yam-api` (с `https://`).
   В сервисе `yam-api` → **Environment** → добавьте `FRONTEND_URL` = адрес `yam-web`. Сохраните.

## Шаг 5. Секреты для GitHub (работа системы)
Откройте https://github.com/harringtonov-cloud/youtubeaimanager/settings/secrets/actions → **New repository secret**. Добавьте по одному:

| Имя | Значение |
|---|---|
| `DATABASE_URL` | как на шаге 2 |
| `SECRET_KEY` | **такой же**, как в Render |
| `LLM_API_KEY` | ключ OpenRouter |
| `PEXELS_API_KEY` | ключ Pexels |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | с шага 6 |
| `S3_ENDPOINT_URL`, `S3_REGION`, `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` | как на шаге 2 |
| `LLM_MODEL` (необязательно) | если не `openai/gpt-4o-mini` |
| `TIMEZONE` (необязательно) | по умолчанию `Asia/Tashkent` |

`ADMIN_TOKEN` в GitHub не нужен.

## Шаг 6. Google Cloud (доступ к YouTube)
1. https://console.cloud.google.com → создайте проект.
2. Включите https://console.cloud.google.com/apis/library/youtube.googleapis.com и https://console.cloud.google.com/apis/library/youtubeanalytics.googleapis.com (Enable).
3. https://console.cloud.google.com/apis/credentials/consent: External, добавьте свой аккаунт в Test users, затем **Publish app** (иначе токен истекает через 7 дней).
4. https://console.cloud.google.com/apis/credentials → Create credentials → OAuth client ID → **Web application**. Authorized redirect URI: `https://<адрес yam-api>/api/youtube/callback`.
5. Скопируйте Client ID и secret. Впишите их в **Render** (yam-api → Environment) и в **GitHub Secrets**.

## Шаг 7. Первый запуск
1. Запустите работу системы: https://github.com/harringtonov-cloud/youtubeaimanager/actions → слева **Runner** → **Run workflow**. Первый запуск создаёт таблицы в базе. Дальше она идёт сама каждые 30 минут.
2. Откройте адрес `yam-web` на телефоне (первая загрузка до минуты: бесплатный сервер просыпается). Войдите по `ADMIN_TOKEN`.
3. **YouTube Connection** → Connect YouTube → выберите канал → разрешите доступ.
4. **AI Settings**: ниша, язык, время публикаций, часовой пояс → Save.
5. **Overview** → «Find ideas now». В следующих запусках Runner система сама сделает сценарии и ролики (смотрите Video Studio).
6. Проверьте первые 2–3 ролика, затем **AI Settings → Autonomous publishing → Check & turn ON**.

---

## Ограничения бесплатного варианта (честно)
* **Публикация с задержкой.** Работа идёт циклами раз в 30 минут, поэтому ролик выходит через 0–30 минут после времени слота. GitHub иногда задерживает запуск по расписанию ещё сильнее.
* **Панель просыпается долго.** Бесплатный сервер Render засыпает через 15 минут без запросов, первая загрузка медленная.
* **Кнопки «Run now».** Лёгкие задачи (идеи, аналитика) выполняются сразу. Монтаж видео делает только Runner, поэтому ролики появляются при ближайшем запуске.
* **Расписание GitHub отключается**, если в репозитории 60 дней нет никаких изменений (коммитов). Тогда зайдите в Actions и нажмите **Enable workflow**.
* **Репозиторий должен остаться публичным.** В приватном бесплатных минут ограниченное количество и их не хватит. Секреты видны только вам, но журнал запусков Actions публичный. Не размещайте в коде ничего личного.
* **Правила GitHub.** Условия использования Actions ограничивают запуск задач, не связанных с проектом репозитория. Это серая зона: проверьте актуальные условия сами. Если GitHub ограничит Actions, перейдите на платный вариант (docs/RENDER.md).
* **Голос Edge TTS** может не отвечать с адресов GitHub. Если озвучка не работает, смотрите Dashboard → System Logs.
* **Supabase.** Бесплатный проект приостанавливается после недели без активности, но Runner работает каждые 30 минут. Лимит на файл 50 МБ: ролики Shorts вписываются.
* Видео на YouTube могут быть приватными до проверки API-проекта (см. docs/SETUP.md).
