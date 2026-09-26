# Проверка развёртывания с чистой БД

Дата: 26 сентября 2026. Среда: Windows, Docker Desktop (Linux engine),
Docker Compose 5.1.0. Проверялся рабочий пакет этапа 12. Использованы новые
контейнеры, сеть и пустой том отдельного проекта `vsm-submission-check`.
Рабочий проект `vsm-training` и его PostgreSQL не очищались. Docker build
использовал доступный кеш слоёв; это проверка чистых данных/контейнеров,
не проверка полностью offline или побитно воспроизводимой сборки.

## Как повторить

Из корня checkout создайте отдельный файл `.env.check` по `.env.example`.
Он исключён правилом `.env.*` из Git. Задайте новый локальный пароль и:

```dotenv
POSTGRES_PORT=15432
BACKEND_PORT=18000
FRONTEND_PORT=18080
DEMO_AUTH_ENABLED=true
```

Убедитесь, что эти порты свободны и имя проекта ещё не занято. Не переиспользуйте
проект с нужными данными для проверки пустой БД.

```sh
docker compose --env-file .env.check -p vsm-submission-check config --quiet
docker compose --env-file .env.check -p vsm-submission-check up --build -d --wait --wait-timeout 180
docker compose --env-file .env.check -p vsm-submission-check ps -a
docker compose --env-file .env.check -p vsm-submission-check logs bootstrap
docker compose --env-file .env.check -p vsm-submission-check exec backend alembic current
docker compose --env-file .env.check -p vsm-submission-check exec backend alembic check
```

Ожидается: bootstrap exit 0, `Imported: 5; unchanged: 0`,
`Published challenges: 2`, Alembic `20260926_06 (head)`, отсутствие новых
upgrade operations. Приложение — http://127.0.0.1:18080/;
Swagger — http://127.0.0.1:18000/docs.

Выполните DEMO_SCRIPT или браузерные тесты. Из `frontend/` в PowerShell:

```powershell
$env:PLAYWRIGHT_BASE_URL='http://127.0.0.1:18080'
$env:PLAYWRIGHT_CHANNEL='msedge'
npm run test:e2e
```

Для Chromium установите его через `npx playwright install chromium` и не задавайте
PLAYWRIGHT_CHANNEL. В bash переменные задаются через export. Этот режим обращается
к собранному Nginx frontend и не запускает Vite.

Для проверки сохранения остановите контейнеры без удаления тома, затем запустите
с теми же параметрами:

```sh
docker compose --env-file .env.check -p vsm-submission-check down
docker compose --env-file .env.check -p vsm-submission-check up -d --wait --wait-timeout 180
```

Bootstrap теперь должен вывести `Imported: 0; unchanged: 5` и
`Published challenges: 0`. Результаты, unlock и исходные сроки кампаний сохраняются.
Новый срок появляется только у новой кампании с новым ID.

После проверки можно удалить **только этот выделенный тестовый проект** вместе
с его синтетическими данными:

```sh
docker compose --env-file .env.check -p vsm-submission-check down --volumes
```

Эту команду нельзя применять к проекту с данными, которые нужно сохранить.

## Результаты

- Первый запуск одной командой из пустой БД: успешно; ручные миграции/seed не нужны.
- Health/readiness через Nginx, demo auth, 5 версий сценариев и 2 challenges: успешно.
- Alembic head и schema check: успешно.
- JSON из инструкции добавления ветки: строгая валидация и переход
  в новый terminal `clarified` успешно; исходный v1 сохранён.
- 16 Playwright E2E через Docker/Nginx, включая две сцены → debrief,
  восстановление, timeout, gamification, retention, mobile/keyboard: успешно.
- 348 pytest с реальным PostgreSQL, без пропусков; 93 Vitest: успешно.
- Ruff format/check, mypy, Prettier, ESLint, TypeScript и frontend build: успешно.

- Worker сохранил medical timeout без GET сессии: статус completed и
  `__timeout__` проверены напрямую в PostgreSQL после deadline.
- После `down`/`up` сравнение данных совпало точно: 5 версий, 2 окна challenges,
  16 наград и 10 unlock; токен и завершённая сессия остались доступны.
  Повторный bootstrap: 0 импортов, 5 unchanged, 0 новых challenges.

Это протокол конкретного прогона, а не обещание неизменного количества
прохождений/наград после дальнейшего использования демо.

## Повторная проверка внутреннего учебного сервиса — 26.09.2026

Создан отдельный проект `vsm-service-verify` с новым томом PostgreSQL и портами
55433/18001/18081. `docker compose ... up -d --build --wait` успешен: bootstrap
импортировал 5 базовых и 12 новых версий, создал 2 кампании. Backend/frontend/db
готовы, worker работает, `alembic check` не обнаружил расхождений на head 08.

Рабочий проект обновлён без удаления данных. При повторном bootstrap:
5+12 unchanged, 0 новых кампаний; readiness и schema check успешны.
Локально ранее применённое промежуточное CHECK 07 синхронизировано атомарно,
без изменения данных; чистая установка сразу получает окончательное ограничение.

Полный backend: 509 pytest с PostgreSQL, без пропусков, включая downgrade до base / upgrade.
Frontend: 107 Vitest. Ruff check/format, mypy, ESLint, Prettier, TypeScript (+E2E типизация)
и production build проходят. Итог браузерной проверки записан в BUILD_PLAN.

Проверены 212 исходных/новых файлов: `.env` не отслеживается, известные шаблоны
приватных ключей/GitHub/AWS токенов не обнаружены. Это ограниченная проверка
шаблонов, не независимый аудит всех возможных секретов.
