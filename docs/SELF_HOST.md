# Запуск без Railway и Vercel: на своём компьютере или VPS (Docker)

Одна команда поднимает всё: PostgreSQL, Redis, API, worker (монтаж + планировщик) и Dashboard.
Видео и обложки хранятся в Docker-томе, отдельное хранилище (S3) не нужно.

> Чтобы система публиковала сама, компьютер/сервер должен работать круглосуточно.
> Если он выключен, публикации ждут и выйдут после включения. Контейнеры сами запускаются вместе с Docker.

## 1. Установите Docker
* Windows / Mac: Docker Desktop, https://www.docker.com/products/docker-desktop/ (после установки запустите его)
* Linux / VPS: `curl -fsSL https://get.docker.com | sh`

## 2. Скачайте проект
GitHub → https://github.com/harringtonov-cloud/youtubeaimanager → зелёная кнопка **Code** → **Download ZIP** → распакуйте.
Либо `git clone https://github.com/harringtonov-cloud/youtubeaimanager`.

## 3. Создайте файл `.env`
В папке проекта скопируйте `.env.example` в новый файл с именем `.env` и заполните только эти строки:

```
ADMIN_TOKEN=придумайте-длинный-пароль-для-входа-в-панель
SECRET_KEY=придумайте-другую-длинную-случайную-строку
LLM_API_KEY=ключ-из-https://openrouter.ai/keys
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=openai/gpt-4o-mini
PEXELS_API_KEY=ключ-из-https://www.pexels.com/api/
GOOGLE_CLIENT_ID=из-Google-Cloud (шаг 5)
GOOGLE_CLIENT_SECRET=из-Google-Cloud (шаг 5)
TIMEZONE=Asia/Tashkent
```

Остальные строки (`DATABASE_URL`, `REDIS_URL`, `S3_*` и т. д.) оставьте как есть: Docker подставляет свои значения сам.
`SECRET_KEY` после подключения YouTube менять нельзя.

## 4. Запустите
В папке проекта:

```
docker compose up -d --build
```

Первая сборка занимает несколько минут. Проверка: откройте http://localhost:3000, введите `ADMIN_TOKEN`.
Статус: `docker compose ps`. Логи: `docker compose logs -f worker`. Остановить: `docker compose down`.

## 5. Google Cloud (доступ к YouTube)
1. https://console.cloud.google.com → создайте проект.
2. Включите **YouTube Data API v3**: https://console.cloud.google.com/apis/library/youtube.googleapis.com
   и **YouTube Analytics API**: https://console.cloud.google.com/apis/library/youtubeanalytics.googleapis.com
3. Экран согласия: https://console.cloud.google.com/apis/credentials/consent. Тип External, добавьте свой аккаунт в Test users, затем **Publish app** (иначе токен истекает через 7 дней).
4. Учётные данные: https://console.cloud.google.com/apis/credentials → Create credentials → OAuth client ID → **Web application**.
   Authorized redirect URI: `http://localhost:8000/api/youtube/callback`
   (Google разрешает `localhost` без HTTPS. На VPS с доменом укажите его и поменяйте `BACKEND_PUBLIC_URL`/`FRONTEND_URL` в `.env`.)
5. Client ID и secret впишите в `.env`, затем `docker compose up -d` (перезапуск применит значения).

## 6. Первый запуск в панели
1. http://localhost:3000 → вход по `ADMIN_TOKEN`.
2. **YouTube Connection → Connect YouTube** → выберите канал → разрешите доступ.
3. **AI Settings**: ниша, язык, время публикаций, часовой пояс → Save.
4. **Overview** → «Find ideas now», затем «Run planner now». Через 10–20 минут ролики появятся в **Video Studio**.
5. Проверьте первые 2–3 ролика. Затем **AI Settings → Autonomous publishing → Check & turn ON**.

## Если используете VPS (доступ с телефона и других устройств)
Откройте порты 3000 (панель) и 8000 (API) или поставьте перед ними reverse proxy с HTTPS (Caddy/nginx).
В `.env` укажите `BACKEND_PUBLIC_URL` и `FRONTEND_URL` с реальным адресом (`http://IP:8000`, `http://IP:3000` или домены с HTTPS).
Для Google OAuth на VPS нужен домен с HTTPS, потому что Google не принимает голый IP как redirect URI.
Закройте панель от посторонних: доступ защищён только `ADMIN_TOKEN`, поэтому делайте его длинным.

## Обновление и копии
* Обновить код: `git pull` (или скачать ZIP заново) → `docker compose up -d --build`
* Данные хранятся в томах Docker (`pgdata`, `mediadata`) и переживают перезапуск и обновление. Не выполняйте `docker compose down -v`: это удалит базу.
