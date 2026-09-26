# Геймификация для ВСМ

Новая QA-сводка описывает пассажирскую геймификацию. Её отношение к текущему
тренажёру **pending clarification**: замена продукта, отдельный контур или другой
кейс пока не подтверждены. Работающее демо ниже сохраняется.
[Сопоставление и открытые вопросы](docs/MEETUP_ALIGNMENT.md).

## 1. Что делает продукт

Учебное приложение для проводников ВСМ-400. Цель продукта — тренировать
принятие решений через нелинейные рабочие сценарии и разбор последствий.

Пользователь выбирает ситуацию, принимает решения в диалоге с пассажиром
и получает разбор: последствия, изменения loyalty/safety, проявленные
компетенции и доступные альтернативы. Завершённые попытки дают XP, уровень,
прогресс достижений и место в рейтинге бригады, депо или компании.
Есть внутренние уведомления и ограниченные по времени челленджи.

Все пять сценариев и пять учебных персон синтетические. Баллы условны;
медицинские и сервисные сюжеты не заменяют утверждённые регламенты.
Runtime-оценка детерминирована, LLM и внешние сервисы не нужны.
[Показ за 5–7 минут](docs/DEMO_SCRIPT.md) · [Пользовательский путь](docs/USER_FLOW.md).

## 2. Demo credentials

Пароль для входа в приложение **не нужен**. При `DEMO_AUTH_ENABLED=true`
интерфейс автоматически получает demo-токен для `demo-employee`.
В «Мой прогресс» вне активной попытки можно переключить персону.

| Persona ID      | Имя                  | Компания / депо / бригада |
| --------------- | -------------------- | ------------------------- |
| `demo-employee` | Учебный проводник 01 | demo-company / north / 01 |
| `demo-north-02` | Учебный проводник 02 | demo-company / north / 01 |
| `demo-north-03` | Учебный проводник 03 | demo-company / north / 02 |
| `demo-south-04` | Учебный проводник 04 | demo-company / south / 01 |
| `demo-other-05` | Учебный проводник 05 | demo-other / north / 01   |

Токен живёт 24 часа; все посетители одной персоны разделяют её историю.
Новый профиль начинает без выдуманных результатов. `POSTGRES_PASSWORD` ниже —
только пароль локальной БД, его нельзя вводить в UI. Demo auth не проверяет
личность сотрудника; не публикуйте это демо как производственную систему.

## 3. Быстрый запуск для жюри

Нужны Git, Docker с работающим Linux engine и Docker Compose v2 или новее с поддержкой
`up --wait`. На Windows запустите Docker Desktop. Node/Python на хосте
для этого способа не нужны. Первая сборка требует интернета для образов и пакетов.

```sh
git clone https://github.com/chidori31/vsm-training-platform.git
cd vsm-training-platform
```

В PowerShell:

```powershell
Copy-Item .env.example .env
notepad .env
```

В Linux/macOS вместо этих двух команд: `cp .env.example .env`, затем откройте
`.env` в редакторе. Задайте собственный непустой `POSTGRES_PASSWORD` (проще
использовать случайные буквы/цифры). Оставьте `DEMO_AUTH_ENABLED=true`.
`DATABASE_URL` для Docker менять не нужно. Далее из корня репозитория:

```sh
docker compose config --quiet
docker compose up --build -d --wait --wait-timeout 180
```

Откройте **http://127.0.0.1:8080/**. Ожидается каталог из пяти ситуаций.
Выберите «конфликт пассажиров» → «Начать сценарий» → «Спокойно выслушать обе
стороны» (за 30 секунд) → «Предложить общий вариант и проверить согласие».
Появится завершение и «Разбор решений» с двумя событиями.

Compose сам применяет миграции, импортирует сценарии и публикует два челленджа.
Дополнительные команды наполнения БД для первого запуска не нужны.

## 4. Docker: устройство и диагностика

| Сервис       | Назначение                                                  | Адрес по умолчанию         |
| ------------ | ----------------------------------------------------------- | -------------------------- |
| frontend     | Собранный React, Nginx и прокси API                         | http://127.0.0.1:8080/     |
| backend      | FastAPI                                                     | http://127.0.0.1:8000/docs |
| db           | PostgreSQL 17, именованный том                              | 127.0.0.1:5432             |
| bootstrap    | Alembic → импорт JSON → публикация challenges, затем exit 0 | Без порта                  |
| timer-worker | Автоматические timeout и внутренние события                 | Без порта                  |

Backend/worker стартуют после успешного bootstrap, frontend — после readiness
backend. Состояние `bootstrap: Exited (0)` нормально. Если миграция или импорт
неуспешны, зависимые сервисы нового запуска не стартуют. `/health` проверяет
процесс, `/ready` — соединение с БД; наличие схемы обеспечивается bootstrap.

```sh
docker compose ps -a
docker compose logs --tail=100 bootstrap backend timer-worker
docker compose exec backend alembic current
docker compose exec backend alembic check
```

Health: http://127.0.0.1:8000/health → `{"status":"ok"}`;
readiness: http://127.0.0.1:8000/ready → `{"status":"ready"}`.
Через Nginx доступны `/api/health` и `/api/ready`.

- Заняты порты: измените `FRONTEND_PORT`, `BACKEND_PORT`, `POSTGRES_PORT` в `.env`.
  Адреса браузера/Swagger меняются соответственно; внутренние порты не меняются.
- Demo login 404: проверьте `DEMO_AUTH_ENABLED=true`, повторите `docker compose up -d --wait`.
- Не совпадает пароль БД: изменение `.env` не меняет пароль уже созданного тома.
  Используйте прежний пароль или отдельно управляйте учётной записью PostgreSQL.
- Импорт отвергает изменённый сценарий: увеличьте его `version`, не переписывайте
  уже опубликованную пару `(id, version)`.
- Кампания истекла: повторный seed не продлевает сроки. Правила новой публикации —
  [RETENTION.md](docs/RETENTION.md).

Остановка: `docker compose down`. Том и результаты сохраняются. Повторный
`docker compose up --build -d --wait` сохраняет версии и сроки кампаний.
Не удаляйте том ради обновления приложения. Отдельное чистое демо можно поднять
с другим `-p` и свободными портами — [протокол проверки](docs/DEPLOYMENT_CHECK.md).
Все опубликованные порты привязаны к loopback. `.env` исключён из Git.

## 5. Локальная разработка

Нужны Python 3.12+, Node.js 22.12+ (в Docker используется 24), npm и PostgreSQL 17.
Сначала подготовьте `.env` по разделу 3. Можно оставить только БД в Docker:

```sh
docker compose up -d --wait db
```

Если полный Compose уже запущен, освободите backend-порт и отключите его worker:
`docker compose stop frontend backend timer-worker`. Команды ниже выполняются
из корня checkout; каждый терминал должен получить переменные подключения.
Backend самостоятельно `.env` не читает.

Терминал backend, PowerShell:

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
$env:DATABASE_URL='postgresql+psycopg:///'
$env:PGHOST='127.0.0.1'
$env:PGPORT='5432'
$env:PGDATABASE='vsm'
$env:PGUSER='vsm'
$env:PGPASSWORD=Read-Host 'POSTGRES_PASSWORD из .env' -MaskInput
$env:DEMO_AUTH_ENABLED='true'
alembic upgrade head
python -m app.scenarios import ../scenarios/demo
python -m app.retention_seed
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

`-MaskInput` требует PowerShell 7.1+; в Windows PowerShell 5.1 задайте
`$env:PGPASSWORD='ваш локальный пароль'` в личном терминале. Укажите ваши
`PGPORT/PGDATABASE/PGUSER`, если меняли значения Compose. Пароль передаётся
отдельно, поэтому URL-кодирование не требуется.

Linux/macOS: `python3 -m venv .venv`, `source .venv/bin/activate`, затем тот же
`pip install`. Вместо `$env:...` используйте `export DATABASE_URL='postgresql+psycopg:///'`,
`export PGHOST=127.0.0.1 PGPORT=5432 PGDATABASE=vsm PGUSER=vsm DEMO_AUTH_ENABLED=true`
и `read -rs PGPASSWORD; export PGPASSWORD`. Команды Alembic/CLI/uvicorn одинаковы.

Терминал worker: перейдите в `backend/`, активируйте тот же virtualenv,
задайте те же `DATABASE_URL` и пять переменных `PG*`, затем `python -m app.worker`.
`TIMER_POLL_SECONDS=1` по умолчанию. Worker обрабатывает timeout без браузера,
сверяет уведомления раз в минуту и восстанавливает просроченные события после рестарта.

Терминал frontend:

```sh
cd frontend
npm ci
npm run dev
```

Откройте адрес Vite (обычно http://127.0.0.1:5173/). `/api/` проксируется на
`API_PROXY_TARGET` из корневого `.env`, по умолчанию `http://127.0.0.1:8000`.
Не запускайте локальный worker параллельно с Compose worker без необходимости.

## 6. Архитектура

- `frontend/` — React/TypeScript/Vite, игровой экран, debrief, паспорт и события.
- `backend/app/domain/` — движок, scoring, gamification, debrief и retention;
  только стандартная библиотека Python, без HTTP/ORM и системных часов.
- `backend/app/application/` — транзакции, авторизация владельца, блокировки,
  серверное время, награды и read models.
- `backend/app/api/`, `scenarios/`, `persistence/` — FastAPI DTO, Pydantic JSON
  и SQLAlchemy; `backend/alembic/` — миграции.
- `scenarios/` — редактируемый контент; `infra/` — Nginx; `docs/` — документация.

React отправляет выбор, сервер вычисляет результат. Версия сценария закреплена
за попыткой. Блокировка строки согласует решение с timeout; уникальные ключи
защищают повторный старт, награды и уведомления.
[Компоненты и решения](docs/ARCHITECTURE.md) · [Sequence/state machine](docs/SCENARIO_ENGINE.md)
· [Механика шкал](docs/GAME_MECHANICS.md) · [Модель доверия](SECURITY.md).

Общий фундамент событий добавлен отдельно от этого рабочего потока:
`domain/events.py`, `domain/reward_ledger.py`, `application/event_ingestion.py`.
Он содержит контракты, проверку повторов и последовательный EARN-прототип;
не подключён к API, БД, worker, профилю или текущим XP.
Без проверяющего адаптера offline-claim отклоняется. Для реального использования
нужен атомарный durable store; его пока нет. SPEND/ADJUSTMENT не исполняются.
[Границы прототипа](docs/ARCHITECTURE.md#общий-фундамент-событий-после-qa).

## 7. Тесты

Backend, из `backend/` с активным virtualenv и установленным `.[dev]`:

```sh
ruff format --check .
ruff check .
mypy
pytest -q
```

Без `TEST_DATABASE_URL` PostgreSQL-тесты **пропускаются**. Для полного запуска
задайте те же переменные `PG*`, что при локальной разработке, и в PowerShell
`$env:TEST_DATABASE_URL='postgresql+psycopg:///'` (bash: `export TEST_DATABASE_URL='postgresql+psycopg:///'`),
затем повторите `pytest -q`. Тесты создают уникальные временные схемы и удаляют
только их; пользователь БД должен иметь CREATE SCHEMA. Проверка Git/.env требует
полного Git checkout и установленного Git. Запускайте тесты на хосте из checkout,
а не внутри production-образа backend.

Frontend, из `frontend/` после `npm ci`:

```sh
npm run format:check
npm run lint
npm run typecheck
npm run typecheck:e2e
npm test
npm run build
npx playwright install chromium
npm run test:e2e
```

E2E требуют готовый backend на 8000, включённый demo-вход, импортированные пять
сценариев и worker. По умолчанию Playwright сам поднимает Vite на 5175.
Для проверки именно собранного Docker frontend задайте
`$env:PLAYWRIGHT_BASE_URL='http://127.0.0.1:8080'` (bash: `export PLAYWRIGHT_BASE_URL=...`)
перед `npm run test:e2e`: Vite не запускается. На Windows с установленным Edge
можно пропустить скачивание Chromium и задать `$env:PLAYWRIGHT_CHANNEL='msedge'`.
E2E создают реальные синтетические попытки; используйте отдельную demo-БД,
если нужен чистый профиль для презентации.

Наборы покрывают ветви/условия, восстановление, гонки timeout/decision,
дедупликацию наград, подделки клиентских баллов, API, аналитику, retention,
доступность и полный браузерный путь до debrief.
[Последняя проверка чистого развёртывания](docs/DEPLOYMENT_CHECK.md).

## 8. Где лежат сценарии

`scenarios/demo/`: `passenger-conflict.json`, `medical-incident.json`,
`service-situation.json`, `boarding-assistance.json`, `lost-property.json`.
Строгий формат — [scenarios/README.md](scenarios/README.md), подсказки редактора —
`scenarios/scenario.schema.json`; источник схемы — `backend/app/scenarios/schema.py`.
JSON хранит граф, условия, эффекты и объяснения; БД хранит неизменяемые версии.

```sh
docker compose exec backend python -m app.scenarios validate /scenarios/demo
docker compose exec backend python -m app.scenarios import /scenarios/demo
```

## 9. Как добавить новую ветку

Скопируйте JSON в новый файл рядом с исходным и увеличьте `version` в копии.
Сохраните прежний файл: чистая БД должна получить и версии, на которые ссылаются
правила/кампании. В частности, demo challenges требуют boarding-assistance@1
и lost-property@1. Добавьте choice с уникальным в узле
`id`, текстом, `destination`, объяснением и при необходимости condition/effects;
создайте целевой node. У каждого узла должен быть путь к terminal, у timed node —
отдельный timeout outcome. Выполните validate/import из раздела 8 и перезагрузите
каталог. Старая попытка останется на прежней версии.
[Полный пример JSON и проверки](docs/SCENARIO_ENGINE.md#новая-ветка-за-несколько-минут).
Изменять ядро или React для обычной развилки не нужно.

## 10. Как добавить achievement

Достижения используют серверные правила. Пример «5 безопасных завершений»:

1. В `backend/app/domain/gamification.py` добавьте `BehaviorAchievement` в
   `ACHIEVEMENTS` с новым ID, описанием и target=5. В `progress_for` добавьте
   значение нового ID `min(5, safe)`: факт `safe_completion` уже сохраняется.
2. Создайте **новую** Alembic-миграцию с записью `achievement_definitions`:
   новый id, version=1, name, description, `condition={"predicates": []}`,
   `behavior_rule`, равный ID. Образец формы записи — миграция `20260926_04`;
   уже опубликованные миграции не редактируйте.
3. Добавьте unit-тест порога 4/5 и повторов, integration-тест единственной
   разблокировки/уведомления. Проверьте API прогресса и UI.
4. Пересоберите Compose: bootstrap применит новую миграцию. UI выводит список
   достижений из API и не требует отдельного компонента для нового ID.

`settle_profile` открывает достижения при обработке новой завершённой попытки.
Добавление определения само по себе не пересчитывает unlock для старого профиля;
если нужен backfill, проектируйте его явно и идемпотентно. Для нового типа факта
потребуются изменения `RewardFact`, расчёта, хранения и миграция. Изменение смысла
существующего правила требует отдельного решения о версиях/старых наградах;
не меняйте их молча. Подробнее — [GAMIFICATION.md](docs/GAMIFICATION.md).

## 11. API / OpenAPI

Базовый путь `/api/v1`; браузер обращается к нему через Nginx/Vite.
[Swagger UI](http://127.0.0.1:8000/docs), [ReDoc](http://127.0.0.1:8000/redoc),
[OpenAPI JSON](http://127.0.0.1:8000/openapi.json) открываются на backend-порту.
В Swagger выполните POST `/api/v1/auth/demo` с `{}`, скопируйте `access_token`
в Authorize. Не используйте пароль БД как токен.

Основной путь: POST `/sessions` с `Idempotency-Key` → GET `/sessions/{id}` →
POST `/sessions/{id}/decisions` → GET `/sessions/{id}/debrief`.
[Полный контракт, ошибки и пример PowerShell](docs/API.md).
HR/LMS — только валидируемый контракт; отправка результатов возвращает 501.

## 12. Ограничения

Общие demo identities, отсутствие production SSO/RBAC/rate limiting/TLS,
непроверенная специалистами методика, небольшие синтетические сценарии,
условный баланс наград, отсутствие внешних интеграций и провайдеров уведомлений.
Снимки воспроизводятся из истории; длинные циклические сценарии и большие
объёмы требуют отдельной оптимизации. Compose — локальное демо, не HA deployment.
Подробности и эксплуатационные границы: [LIMITATIONS.md](docs/LIMITATIONS.md)
и [SECURITY.md](SECURITY.md).

## 13. Дальнейшее развитие

1. Проверить сценарии, объяснения, шкалы и методику аналитики с профильными экспертами.
2. Добавить реальную аутентификацию, роли, аудит, защиту публичного сервиса и backups.
3. Согласовать HR/LMS-контракт с конкретным заказчиком и только затем интеграцию.
4. Добавить редактор/ревью публикаций, управление кампаниями и проверенный контент.
5. Измерить нагрузку, оптимизировать replay/read models, зафиксировать зависимости
   для воспроизводимых релизов и автоматизировать delivery/мониторинг.

Это план, а не реализованные функции. Пакет сдачи этапа 12 сохранён.
После QA сначала требуется решение о scope; пассажирская разработка не начинается
до его подтверждения. Общий фундамент не означает выбор продуктового направления.
[BUILD_PLAN](docs/BUILD_PLAN.md) сохраняет историю этапов.
[Первоначальное описание и PRODUCT SCOPE — pending clarification](docs/PRODUCT_SCOPE_HISTORY.md)
сохранены как история до уточнения владельца на этапе 2.
