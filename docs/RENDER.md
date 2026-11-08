# Запуск на Render (без компьютера, всё с телефона)

Render поднимает весь проект по файлу `render.yaml` из репозитория: база, Redis, API, worker (монтаж и планировщик)
и панель управления. Вы открываете панель в браузере телефона. Компьютер и командная строка не нужны.

> Часть сайтов (Google Cloud, Cloudflare) на телефоне неудобна. Включите в браузере
> «Версия для ПК» / «Desktop site», тогда всё откроется как на компьютере.

## Что платное
База данных, `yam-api`, `yam-worker` и `yam-web` работают на платных тарифах Render (Redis бесплатный).
Самый дорогой сервис worker: он монтирует видео. Актуальные цены смотрите на https://render.com/pricing.
Если рендер получается стабильно, в `render.yaml` у worker можно заменить `standard` на `starter`, чтобы удешевить.

## Шаг 1. Подготовьте ключи (до Render)
| Что | Ссылка | Что получите |
|---|---|---|
| Нейросеть | https://openrouter.ai/credits (пополнить), https://openrouter.ai/keys (Create Key) | `LLM_API_KEY` |
| Стоковые видео | https://www.pexels.com/api/ | `PEXELS_API_KEY` |
| Хранилище видео | https://dash.cloudflare.com/ → R2 → Create bucket, затем Manage API tokens (права Object Read & Write) | `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_ENDPOINT_URL` (`https://<account_id>.r2.cloudflarestorage.com`) |
| Пароль панели | придумайте сами, 20+ символов | `ADMIN_TOKEN` |

Хранилище R2 обязательно: на Render у сервисов отдельные временные диски, и без общего хранилища видео пропадают.
Система не даст включить автопубликацию, пока хранилище не настроено.

## Шаг 2. Создайте проект на Render
1. Зарегистрируйтесь на https://render.com (удобно через GitHub), добавьте способ оплаты.
2. https://dashboard.render.com/blueprints → **New Blueprint Instance**.
3. Подключите GitHub, выберите репозиторий `harringtonov-cloud/youtubeaimanager`, ветка `main`.
4. Render покажет список: база `yam-db`, Redis `yam-redis`, сервисы `yam-api`, `yam-worker`, `yam-web`. Нажмите **Apply**.
5. Первая сборка занимает 10–20 минут. Пока в панели ошибки про ключи, это нормально: ключей ещё нет.

## Шаг 3. Узнайте адреса
В Render откройте сервисы `yam-api` и `yam-web`. Вверху каждого виден адрес вида `https://yam-api-xxxx.onrender.com`. Запишите оба.

## Шаг 4. Доступ к YouTube (Google Cloud)
1. https://console.cloud.google.com → создайте проект.
2. Включите https://console.cloud.google.com/apis/library/youtube.googleapis.com и https://console.cloud.google.com/apis/library/youtubeanalytics.googleapis.com (кнопка Enable).
3. https://console.cloud.google.com/apis/credentials/consent: тип External, добавьте свой Google-аккаунт в Test users, затем **Publish app** (иначе токен истекает через 7 дней).
4. https://console.cloud.google.com/apis/credentials → Create credentials → OAuth client ID → **Web application**.
   Authorized redirect URI: `https://<адрес yam-api>/api/youtube/callback`
5. Скопируйте Client ID и Client secret.

## Шаг 5. Впишите ключи в Render
Render → **Environment Groups** → группа **yam-shared** → добавьте переменные (Add) и сохраните:

| Переменная | Значение |
|---|---|
| `ADMIN_TOKEN` | ваш пароль панели |
| `LLM_API_KEY` | ключ OpenRouter |
| `PEXELS_API_KEY` | ключ Pexels |
| `S3_ENDPOINT_URL`, `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` | из Cloudflare R2 |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | из Google Cloud |
| `FRONTEND_URL` | адрес `yam-web` |

Уже готово в группе и менять не нужно: `SECRET_KEY` (создан автоматически), `LLM_BASE_URL`, `LLM_MODEL`, `STORAGE_BACKEND=s3`, `S3_REGION`, `TTS_PROVIDER`, `TIMEZONE`.
После сохранения Render сам перезапустит `yam-api` и `yam-worker`. Адрес API система определяет сама.
Проверка: откройте `https://<адрес yam-api>/api/health`, там должно быть `"database": true, "redis": true`.

## Шаг 6. Первый запуск
1. Откройте адрес `yam-web` на телефоне и войдите по `ADMIN_TOKEN`.
2. **YouTube Connection** → Connect YouTube → выберите канал → разрешите доступ.
3. **AI Settings**: ниша, язык, время публикаций, часовой пояс → Save.
4. **Overview**: «Find ideas now», затем «Run planner now». Через 10–20 минут ролики появятся в **Video Studio**.
5. Проверьте первые 2–3 ролика, затем **AI Settings → Autonomous publishing → Check & turn ON**.

## Если что-то не работает
| Симптом | Что делать |
|---|---|
| Render при Apply показывает ошибку в `render.yaml` | пришлите мне текст ошибки целиком, я исправлю файл |
| Панель пишет «Backend is unreachable» | `yam-api` ещё собирается или упал: Render → yam-api → Logs |
| «Worker offline» | Render → yam-worker → Logs (часто не хватает ключа или памяти) |
| Рендер видео падает с нехваткой памяти | в `render.yaml` оставьте у worker `standard` или поднимите тариф |
| YouTube `needs_reauth` | приложение Google было в режиме Testing: опубликуйте его и подключите канал заново |
| Видео на YouTube приватные | ограничение Google для непроверенных API-проектов (см. docs/SETUP.md) |
