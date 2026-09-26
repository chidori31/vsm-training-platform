# REST API /api/v1

## Границы

Цель: версионированный REST API поверх существующего движка. FastAPI отвечает
за валидацию, аутентификацию и представление; application services владеют
транзакциями и запросами, чистый домен — правилами переходов и баллов.

- `/api/v1`: auth/demo, profiles, scenarios, sessions, decisions, results,
  achievements, leaderboard, analytics, challenges, notifications и контракт HR/LMS.
- Demo-вход явно включается `DEMO_AUTH_ENABLED=true`. Доступен фиксированный
  список синтетических персон; непрозрачные bearer-токены действуют 24 часа,
  в PostgreSQL сохраняются только их SHA-256 хеши. Это не идентификация сотрудника.
- Старт требует `Idempotency-Key`, владелец берётся из токена.
  Уникальный ключ пользователя и созданная сессия сохраняются одной транзакцией.
  Повтор возвращает ту же сессию; другой payload с тем же ключом даёт 409.
- Решения сохраняют существующий `decision_id` и `expected_sequence`.
  Timeout по-прежнему фиксируется до HTTP-конфликта. Чужая сессия даёт 404.
- Ошибки имеют `error.code/message/details` и опциональное `data`
  с актуальным состоянием при конфликте решения. Валидация не отражает сырые входы.
- Коллекции: `limit=20` (1–100), `offset=0`, `items/total/limit/offset`,
  стабильная сортировка. Лидерборд сценария сортирует лучший результат по шкале;
  рейтинг подразделения — накопленный XP из наград завершённых попыток.
- Достижения и разблокировки читаются из таблиц; этап 8 добавляет автоматическую
  выдачу, XP, уровни и прогресс. Аналитика вычисляется по сохранённым попыткам.
- HR/LMS: строгая схема учебного результата, описание направления обмена,
  endpoint-заглушка возвращает 501 после валидации, без сети и записи данных.
- Неверсионированный API этапа 5 заменяется; health/readiness сохраняются.
  Nginx/Vite сохраняют `/api/v1`, прежний `/api/health` продолжает работать.

## Реализованный порядок этапа 6

1. Тесты OpenAPI, общего формата ошибок и контракта HR/LMS; API schema/error layer.
2. PostgreSQL identity, idempotency и read models, миграция; demo auth,
   ownership и конкурентный старт с проверками в изолированной тестовой БД.
3. Тесты полного API-прохождения, текущего узла, результата, страниц,
   каталога, достижений, лидерборда и аналитики; тонкие маршруты и сервисы.
4. Обновление документации/прокси; Ruff, mypy, полный pytest с PostgreSQL,
   frontend проверки, Docker/migration и smoke через Nginx.
5. Проверка diff; один commit `feat: expose gameplay and integration API`,
   push текущей ветки и остановка.

## Адреса и авторизация

Базовый путь одинаков для backend, Vite и Nginx: `/api/v1`.
Локальный backend — `http://127.0.0.1:8000`, Compose/Nginx —
`http://127.0.0.1:8080`. Swagger UI — `http://127.0.0.1:8000/docs`,
ReDoc — `/redoc`, машинная спецификация — `/openapi.json` на backend.

Полный Compose автоматически применяет миграции и импортирует demo-сценарии.
Для локального backend выполните команды из README. Для demo-входа
задайте `DEMO_AUTH_ENABLED=true`: Compose читает эту переменную из `.env`
(она включена в `.env.example`), ручной backend — из своего окружения.
При отсутствии флага вход отключён. В Swagger нажмите Authorize и вставьте
значение `access_token`; схема BearerAuth добавляет заголовок сама.

Все маршруты данных требуют `Authorization: Bearer <access_token>`.
Исключения — demo login и список demo-персон, публичные health/readiness и контракт HR/LMS.
Токен истекает через 24 часа по времени PostgreSQL и переживает рестарт backend.
Флаг отключает выдачу новых demo-токенов; уже выданные действуют до истечения.
Токены не возвращаются повторно из БД и не записываются в открытом виде.

Без тела POST demo-вход принадлежит `demo-employee`, сохраняя совместимость.
Необязательное тело `{"persona_id":"demo-north-02"}` выбирает одну из пяти
персон сервера; произвольный ID и дополнительные поля отклоняются с 422.
Список доступен через GET `/auth/demo/personas` только при включённом demo-входе.
У каждой персоны собственные результаты; все участники, выбравшие одну персону,
видят её общую историю. Это не production-аутентификация и не проверка личности.
Пароли, роли, SSO, logout/revocation и лимиты запросов — будущая работа.
Сессии старого API сохраняются, но доступны через v1 только владельцу с
совпадающим ID; произвольные прежние `employee_id` не присваиваются demo-профилю.

## Маршруты

Все пути ниже начинаются с `/api/v1`.

| Метод и путь                                      | Назначение / ответ                                                                                      |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| POST `/auth/demo`                                 | Необязательное тело persona_id; 200: access_token, token_type, expires_at, profile                      |
| GET `/auth/demo/personas`                         | Фиксированный список синтетических персон и их подразделений                                            |
| GET `/auth/me`                                    | Текущий профиль: id, display_name                                                                       |
| GET `/profiles/me`                                | Тот же профиль                                                                                          |
| GET `/profiles/me/progress`                       | XP, уровень, организация, компетенции, прогресс достижений; опциональный session_id для награды попытки |
| GET `/profiles/me/achievements`                   | Страница сохранённых разблокировок пользователя                                                         |
| GET `/scenarios`                                  | Страница версий: id, version, title, competency_ids                                                     |
| GET `/scenarios/{scenario_id}/versions/{version}` | Полный строгий ScenarioDocument выбранной версии                                                        |
| POST `/sessions`                                  | Старт; обязательный Idempotency-Key, тело scenario_id + scenario_version                                |
| GET `/sessions/{session_id}`                      | Актуальное состояние после согласования timeout                                                         |
| POST `/sessions/{session_id}/decisions`           | Принять решение или подтвердить его повтор                                                              |
| GET `/sessions/{session_id}/decisions`            | Страница истории решений в порядке принятия                                                             |
| GET `/sessions/{session_id}/result`               | Итог завершённой попытки и её снимок                                                                    |
| GET `/sessions/{session_id}/debrief`              | Обучающая временная линия, шкалы, компетенции, альтернативы и рекомендации                              |
| GET `/results`                                    | Страница итогов завершённых попыток текущего пользователя                                               |
| GET `/achievements`                               | Страница определений с condition или behavior_rule                                                      |
| GET `/leaderboard`                                | Лучшие завершённые попытки пользователей для версии сценария                                            |
| GET `/leaderboard/organization`                   | Рейтинг XP в scope=brigade/depot/company, limit/offset                                                  |
| GET `/analytics/me`                               | Сводка сохранённых попыток текущего пользователя                                                        |
| GET `/analytics/me/competencies`                  | Прогресс и наблюдения компетенций, повторяющиеся проблемы, статистика сценариев                         |
| GET `/challenges`                                 | Персональный прогресс, сроки и состояния демо-челленджей                                                |
| GET `/notifications`                              | Страница входящих, read/unread и история истёкших событий                                               |
| PUT `/notifications/{notification_id}/read`       | Идемпотентное изменение состояния прочтения                                                             |
| GET `/integrations/hr-lms/contract`               | Публичное описание и JSON Schema будущего обмена                                                        |
| POST `/integrations/hr-lms/training-results`      | Публичная проверка контракта, затем 501                                                                 |

Тела start/decision запрещают дополнительные поля и не приводят строки/boolean
к числам. Идентификатор сценария соответствует схеме контента: до 64 символов,
строчные латинские буквы, цифры, `_`, `-`, первая — буква.
Версия — положительное целое до 2147483647. `employee_id`, `now`, баллы,
эффекты и политика из HTTP-запроса не принимаются.

Состояние содержит `session` (снимок v2), `server_time`, `deadline`,
`expected_sequence`, `current_node` (id/text/terminal/time_limit_seconds)
и `available_choices` (id/text). Доступность вариантов вычисляет домен;
клиент не должен повторно реализовывать условия. Каталог версий предназначен
для учебного демо и открывает полный граф, включая эффекты.

Результат содержит `summary` и `session`. В summary: идентификаторы,
completed_at, duration_seconds, decision_count, обе независимые шкалы
и массив компетенций. До завершения возвращается 409 result_not_ready.
Если GET результата обнаружил истёкший срок, timeout сначала сохраняется;
переход в ещё один активный узел всё равно даёт result_not_ready.

## Идемпотентность и таймер

Старт: новый ключ даёт 201, повтор того же payload — 200; оба ответа имеют
`Location: /api/v1/sessions/{id}`. Ключ — непустая строка до 128 символов,
область уникальности — пользователь. Повтор возвращает актуальное состояние
той же попытки и не начинает таймер заново. Изменение сценария/версии с тем же
ключом даёт 409 idempotency_conflict. Неуспешный старт не резервирует ключ.
Ключи и связь с сессией сохраняются атомарно и переживают рестарт.

Решение принимает `decision_id`, `node_id`, `choice_id`,
`expected_sequence`. Номер равен длине истории на момент принятия, включая
таймауты. Новый ID для нового действия; для повтора отправьте исходные поля
без изменений. Ответ 200 содержит outcome accepted/duplicate и
acknowledged_decision_id. Повтор не начисляет баллы и возвращает текущее состояние.
Префикс `timeout:` зарезервирован для серверных ID.

Сервер читает время PostgreSQL после блокировки сессии. При `now >= deadline`
выбор уже не принимается; worker или запрос применяет timeout и фиксирует его.
Конфликт решения — 409 с единым error и актуальным состоянием в data.
Если worker уже сделал переход, устаревший выбор может дать decision_rejected;
если срок обработал сам запрос — decision_timed_out. Оба не применяют выбор.
Подробная семантика таймера и clamp — в [GAME_MECHANICS.md](GAME_MECHANICS.md).

## Ошибки

Формат одинаков для маршрутизации, валидации, аутентификации и прикладных ошибок:

```json
{
  "error": {
    "code": "session_not_found",
    "message": "Session not found",
    "details": []
  },
  "data": null
}
```

| HTTP | code                                                                                           |
| ---- | ---------------------------------------------------------------------------------------------- |
| 401  | unauthorized; заголовок WWW-Authenticate: Bearer                                               |
| 404  | not_found, session_not_found, scenario_not_found, demo_auth_disabled                           |
| 405  | method_not_allowed                                                                             |
| 409  | idempotency_conflict, decision_rejected, decision_timed_out, result_not_ready, domain_conflict |
| 422  | validation_error; details содержит location/message/type, без сырых входных значений           |
| 500  | internal_error; без traceback                                                                  |
| 501  | integration_not_configured                                                                     |
| 503  | database_unavailable; без URL и учётных данных                                                 |

Несуществующие и чужие сессии одинаково дают 404. На ошибки решений клиент
может отобразить data и продолжить с новым expected_sequence. Не повторяйте
изменённый payload под прежним idempotency key/decision_id.

## Пагинация, лидерборд и аналитика

Все коллекции принимают `limit` (1–100, по умолчанию 20) и `offset`
(0–2147483647, по умолчанию 0), отвечают `items/total/limit/offset`.
Offset за концом даёт пустой items с исходным total. Каталог сортируется
по id/version; история — по порядку решений; результаты — по updated_at DESC,
затем session ID; разблокировки — по времени, затем ID.

Лидерборд `/leaderboard` требует `scenario_id` и `scenario_version`; metric —
`safety_rating` (по умолчанию) или `passenger_loyalty`. Для каждого
зарегистрированного профиля выбирается его лучшая завершённая попытка по этой
шкале. При равенстве выбирается более раннее завершение, затем session ID.
Ранги учитывают равенство баллов: 1, 1, 3. Вторая шкала возвращается отдельно,
общий балл не вычисляется. Активные попытки и другие версии исключены.

Аналитика показывает total/active/completed_sessions, decision_count
(включая timeout), timeout_count и средние конечные Loyalty/Safety по завершённым
попыткам. Средняя компетенция учитывает только завершённые попытки, где она
объявлена; это не долговременный прогресс профиля. Без результатов средние
шкалы null, массив компетенций пуст. Read models используют сохранённое состояние;
в отличие от GET конкретной сессии, они не запускают массовую обработку timeout.

Достижения читаются из achievement_definitions, разблокировки — из
achievement_unlocks. Миграция 04 добавляет четыре определения поведенческих
достижений, без пользовательских наград. У них `condition: null`, а `behavior_rule`
содержит ID правила; прежние декларативные условия остаются поддержанными.
Выдача происходит при начислении результата. API редактирования правил отсутствует.
Для небольшого демо результаты, аналитика и leaderboard собираются из
проверенных снимков в памяти; limit ограничивает ответ, не стоимость агрегации.
Материализованные проекции и масштабирование требуют отдельного этапа.

## Прогресс и рейтинг подразделений — этап 8

GET `/profiles/me/progress` возвращает `id`, `display_name`, `organization`
(company/depot/brigade IDs и имена), `xp`, `level`, `level_start_xp`, `next_level_xp`,
`completed_sessions`, `rule_version`, `competencies`, `achievements` и `reward`.
Компетенции — массив `{competency_id, value}` накопленных очков. Каждый элемент
achievements содержит `id/name/description`, `current/target`, `unlocked`,
`unlocked_at` и `session_id` основания. Для закрытого достижения последние два
поля null; открытое навсегда сохраняет current=target.

Параметр `session_id` возвращает награду только собственной завершённой попытки:
`reward: {session_id, xp, competencies, unlocks}`. `unlocks` — ID достижений,
открытых именно этой попыткой. Без параметра, для чужой, активной или отсутствующей
попытки reward=null; чужой результат не раскрывается. Начисление происходит в
транзакции завершения, включая worker. Чтение также идемпотентно учитывает старые
проверенные результаты без наград. Активные попытки XP не дают.

GET `/leaderboard/organization?scope=brigade&limit=20&offset=0` возвращает:

```json
{
  "scope": "brigade",
  "group_name": "Бригада 01",
  "assigned": true,
  "items": [
    {
      "rank": 1,
      "employee_id": "demo-north-02",
      "display_name": "Учебный проводник 02",
      "xp": 55,
      "level": 1,
      "completed_sessions": 1,
      "is_me": true
    }
  ],
  "total": 1,
  "limit": 20,
  "offset": 0
}
```

Это пример формы ответа, не стартовые данные. Scope по умолчанию brigade;
другие варианты depot/company. Принадлежность берётся из серверного профиля,
фильтруются также все предки подразделения. Суммируются сохранённые награды
завершённых попыток; профиль без результатов отсутствует в рейтинге. Равные XP
дают одинаковый ранг (1, 1, 3), employee_id стабилизирует порядок. Ранг глобальный,
не начинается заново на странице. Если подразделение не назначено, assigned=false,
items=[], total=0; отсутствующая принадлежность не объединяет разные профили.

XP, уровень, очки, организация и unlock не принимаются от клиента.
Правила v1 и ограничения демо — в [GAMIFICATION.md](GAMIFICATION.md).

## Обучающий разбор и аналитика — этап 9

GET `/sessions/{session_id}/debrief` читает только собственную завершённую попытку.
Чужая/несуществующая даёт 404, незавершённая — 409 result_not_ready. Как и у result,
чтение сначала согласует просроченный таймаут существующим SessionService.

Ответ содержит `rule_version: 1`, идентификаторы и название сценария, completed_at,
summary с количеством решений/таймаутов и итоговой дельтой обеих шкал, а также
упорядоченные `decisions`. Каждое решение содержит текст ситуации и выбора,
следующий узел и последствие, время, обе шкалы до/после с объяснением, компетенции,
альтернативы и suggestion. У альтернатив есть `available`; их эффекты вычислены
на состоянии до исходного выбора. Timeout-альтернативы показывают, что было
доступно до истечения срока. Указаны только непосредственные последствия.
`elapsed_seconds` — записанный интервал после входа в узел, включая задержку
обработки сервером; он не измеряет чистое время размышления.

GET `/analytics/me/competencies` возвращает `rule_version: 1`, числа всех,
завершённых и активных попыток, decision_count/timeout_count завершённых,
`competencies`, `strengths`, `weaknesses`, `patterns`, `scenarios`.
Навык содержит earned_points/net_delta, число положительных и отрицательных
наблюдений, opportunities/practiced_sessions, status и хронологический trend.
Status: insufficient_data, strength, growth_area или developing. Паттерн содержит
count, session_count, recurring и учебный совет; повтор требует двух разных попыток.
Статистика сценариев разделяет ID/версию и считает средние только по завершениям.

Оба маршрута требуют bearer-токен, не принимают employee_id и не используют LLM.
Результаты воспроизводятся из проверенных структурированных снимков v2; повтор
чтения не создаёт решений/наград. Новая миграция для этапа 9 не требуется.
Старый `/analytics/me` сохранён: его средний итог компетенции не подменяется
накопленными очками нового ответа. Полный состав полей, пороги и ограничения —
в [DEBRIEF_ANALYTICS.md](DEBRIEF_ANALYTICS.md).

## Пример PowerShell

После запуска Compose с включённым demo-входом:

```powershell
$base = 'http://127.0.0.1:8080/api/v1'
$login = Invoke-RestMethod -Method Post "$base/auth/demo"
$headers = @{ Authorization = "Bearer $($login.access_token)" }
Invoke-RestMethod "$base/scenarios?limit=10" -Headers $headers

$startHeaders = $headers.Clone()
$startHeaders['Idempotency-Key'] = [guid]::NewGuid().ToString()
$body = @{ scenario_id = 'demo-service-situation'; scenario_version = 1 } | ConvertTo-Json
$session = Invoke-RestMethod -Method Post "$base/sessions" -Headers $startHeaders -ContentType 'application/json' -Body $body
$sessionId = $session.session.id

# Отправьте до истечения 40 секунд; повторяйте с тем же decision_id только тот же выбор.
$choice = @{ decision_id = [guid]::NewGuid().ToString(); node_id = 'request'; choice_id = 'explain'; expected_sequence = 0 } | ConvertTo-Json
Invoke-RestMethod -Method Post "$base/sessions/$sessionId/decisions" -Headers $headers -ContentType 'application/json' -Body $choice
$finish = @{ decision_id = [guid]::NewGuid().ToString(); node_id = 'alternative'; choice_id = 'offer'; expected_sequence = 1 } | ConvertTo-Json
Invoke-RestMethod -Method Post "$base/sessions/$sessionId/decisions" -Headers $headers -ContentType 'application/json' -Body $finish
Invoke-RestMethod "$base/sessions/$sessionId/result" -Headers $headers
```

## Контракт HR/LMS

TrainingResultExport версии 1 содержит event_id, employee_reference, session_id,
scenario_id/version, completed_at (с часовым поясом), passenger_loyalty,
safety_rating и competencies. Шкалы ограничены 0–100; компетенции уникальны по ID.
employee_reference — будущая связь с идентификатором внешней системы;
источник и правила сопоставления сейчас не назначены.

GET contract возвращает direction platform_to_hr_lms, status contract_only
и полную JSON Schema. POST training-results — диагностическая заглушка контракта:
невалидный payload даёт 422, валидный — 501 integration_not_configured.
Он не подтверждает доставку, не хранит payload, не создаёт очередь и не вызывает
сеть. event_id предназначен для будущей дедупликации у адаптера/получателя;
сейчас нет ни получателя, ни обещания exactly-once доставки.

## Совместимость и проверки

Неверсионированные `/sessions` этапа 5 удалены. Payload старта больше не
принимает employee_id; конфликт решения использует общий error/data.
Health/readiness остаются доступны без токена, включая прежний /api/health
через proxy. Формат сохранённых сессий остаётся v2, сценариев — v1.
Миграция 20260926_03 добавляет таблицы, не переписывая прежние попытки.
Миграция 20260926_04 добавляет членство в подразделениях, session_rewards и
поведенческие определения. Форматы сценария/снимка остаются прежними.

API-тесты: `tests/test_api_contract.py`, `tests/test_timed_api.py`,
`tests/timed_sessions/test_rest_api.py`. Интеграционные тесты выполняются
на реальном PostgreSQL в изолированных схемах: конкурентный start, повтор после
рестарта, rollback ключа, ownership, timeout, результаты, страницы, достижение,
независимые лидерборды и аналитика. Полные команды проверок — в README.
`tests/timed_sessions/test_gamification_persistence.py` проверяет начисления,
сохранение unlock, иерархию рейтинга, backfill, rollback и конкурентные завершения
и старты терминальных сценариев; чистые правила — `tests/domain/test_gamification.py`.
`tests/domain/test_debrief.py` и `tests/timed_sessions/test_learning_persistence.py`
проверяют разбор, условия альтернатив, неизбежный timeout, пороги наблюдений,
воспроизводимость и доступ только к собственной сохранённой истории.

## Этап 10: челленджи и внутренние уведомления

Все запросы требуют BearerAuth. `limit` 1–100 (по умолчанию 20), `offset` ≥ 0.

- `GET /api/v1/challenges`: `items`, `total`, `limit`, `offset`, `server_time`.
  Элемент: id/title/description, starts_at/expires_at, target/progress,
  status (`scheduled|active|completed|expired`), scenarios (id/version/title/completed).
  Прогресс только текущего пользователя; участие автоматическое.
- `GET /api/v1/notifications`: та же пагинация + `unread_count` (непрочитанные
  и ещё актуальные сообщения во всём ящике). История включает истёкшие сообщения.
  Элемент: id/kind/title/body, created_at/expires_at/read_at, expired.
  kind: `new_scenario|challenge_started|challenge_ending|achievement_unlocked`.
- `PUT /api/v1/notifications/{id}/read`, тело `{"read": true}` или `{"read": false}`.
  Возвращает обновлённое сообщение. Идемпотентная установка состояния;
  повторный true не меняет read_at. Чужой/несуществующий id → 404
  `notification_not_found`; строки вместо boolean → 422.

Нет публичного API публикации кампаний. Оператор запускает `python -m app.retention_seed`
после импорта новых сценариев. Окна не перезапускаются при повторе команды.
Даты авторитетны на сервере; при `completed_at == expires_at` прохождение уже
не засчитывается. Подробные критерии и доставка: [RETENTION.md](RETENTION.md).

## Ограничения безопасности (этап 11)

Тела `/api/v1/` ограничены 64 KiB, в том числе при потоковой передаче без
Content-Length. Превышение → 413 `request_too_large` в общем JSON-формате.
Все ответы versioned API имеют `Cache-Control: no-store` и `X-Content-Type-Options: nosniff`.
Идентификаторы команды и Idempotency-Key: 1–128 символов, непустые, без ASCII
управляющих символов. expected_sequence: целое 0–2147483647; bool не принимается.
Клиентские поля score/loyalty/safety/xp/effects/destination/employee_id/now в
командах игры отвергаются как лишние (422). У лидербордов нет write-endpoint.
Подробная модель доверия и границы демо: [SECURITY.md](../SECURITY.md).
