# Геймификация для ВСМ

## Подтверждённый технический фундамент

На первом этапе реализованы технический фундамент, экран проверки связи
с backend, HTTP health/readiness и конфигурация PostgreSQL/Alembic.
Игровые сценарии, профили и интеграции ещё не реализованы.

## PRODUCT SCOPE — pending clarification

Продуктовый scope не зафиксирован. Исходное официальное ТЗ описывает обучающий
симулятор для проводников, а материалы QA-сессии обсуждают пассажирскую
геймификацию. До уточнения владельцем репозитория ни одна из этих аудиторий
не закреплена в архитектуре. Текущий этап ограничен нейтральным техническим
фундаментом; следующий этап самостоятельно не начинается.

Не создаются EmployeeProfile, PassengerProfile, Scenario Engine, Miles/«Вёрсты»,
Achievements, Loyalty Program, HR/LMS integration и ticketing integration.
Подробности противоречия и сохранённый исходный план — в `docs/BUILD_PLAN.md`.
Существующие имена репозитория и пакетов сохранены как технические идентификаторы,
а не как утверждение продуктовой концепции.

### Исходное описание задачи (сохранено; требует уточнения)

Учебное приложение для проводников ВСМ-400. Цель продукта — тренировать
принятие решений через нелинейные рабочие сценарии и разбор последствий.

## Структура

- `frontend/` — React, TypeScript, Vite, Vitest.
- `backend/` — FastAPI, Pydantic, SQLAlchemy, Alembic, pytest.
- `infra/` — Nginx, проксирующий `/api/` в backend.
- `scenarios/` — сохранённый каталог исходного задания; развитие приостановлено.
- `docs/BUILD_PLAN.md` — архитектура, границы и этапы разработки.
- `compose.yaml` — PostgreSQL, backend и frontend для локального запуска.

## Зависимости

Node.js 22.12+ (рекомендуется 24), npm, Python 3.12+, PostgreSQL 17.
Для контейнерного запуска — Docker с работающим Linux engine и Compose v2.
Без БД работают экран приложения и `/health`; `/ready` возвращает 503.

## Backend локально

Из корня репозитория:

```sh
cd backend
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows PowerShell вместо предыдущей строки:
# .\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Проверка: <http://127.0.0.1:8000/health> → `{"status":"ok"}`.
OpenAPI: <http://127.0.0.1:8000/docs>.

Для БД установите `DATABASE_URL` в окружении backend. Например, в PowerShell:
`$env:DATABASE_URL='postgresql+psycopg://vsm:YOUR_LOCAL_PASSWORD@localhost:5432/vsm'`.
В bash используйте `export DATABASE_URL='...'`. Спецсимволы пароля в URL нужно
кодировать. Backend не загружает `.env` автоматически; Compose загружает его.
`GET /ready` возвращает `{"status":"ready"}` после успешного `SELECT 1`.

## Frontend локально

В отдельном терминале из корня:

```sh
cd frontend
npm ci
npm run dev
```

Откройте адрес Vite из терминала (обычно <http://127.0.0.1:5173>).
Vite проксирует `/api/health` в `http://127.0.0.1:8000/health`.
Другой адрес задаётся `API_PROXY_TARGET` в корневом `.env` или окружении.
Экран показывает ожидание, успех или ошибку с повторной проверкой.
Таймаут запроса — 5 секунд. Успех означает доступность backend, а не БД.

## Docker Compose

Скопируйте `.env.example` в `.env` (`Copy-Item .env.example .env` в PowerShell
или `cp .env.example .env` в bash). Задайте собственный `POSTGRES_PASSWORD`.
Затем из корня:

```sh
docker compose config --quiet
docker compose up --build -d --wait
docker compose exec backend alembic upgrade head
```

Приложение: <http://localhost:8080>, backend: <http://localhost:8000/health>,
готовность БД: <http://localhost:8000/ready>. Порты привязаны к loopback.
Compose передаёт пароль БД отдельно через `PGPASSWORD`, без сборки URL.
Корневой `DATABASE_URL` используется только при ручном локальном запуске.
На первом этапе доменных миграций нет; Alembic готов для следующих этапов.

Логи: `docker compose logs -f`. Остановка: `docker compose down`.
Данные сохраняются в именованном томе. Изменение пароля в `.env` не меняет
пароль уже инициализированной БД. Этот Compose не предназначен для production.

## Проверки

Frontend из `frontend/`:

```sh
npm run format
npm run format:check
npm run lint
npm run typecheck
npm test
npm run build
```

Backend из `backend/` с активированным virtualenv:

```sh
ruff format .
ruff format --check .
ruff check .
mypy
pytest
```

Unit-тесты не требуют PostgreSQL. Контейнерная проверка `/ready` проверяет
реальное соединение с PostgreSQL. Playwright предусмотрен как будущий инструмент
E2E; его добавление и состав проверок зависят от уточнения scope и отдельного
задания на следующий этап.

Не сохраняйте `.env`, ключи, пароли или персональные данные в Git.
