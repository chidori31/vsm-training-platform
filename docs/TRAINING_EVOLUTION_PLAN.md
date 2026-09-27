# Развитие учебной платформы

Согласованный объём: действия с длительностью, воспроизведение и альтернативная
попытка, персональная практика, инструктор, проверяемые критерии, редактор
содержания, вводный / рабочий / демонстрационный режимы.

## Ограничения и решения

- Опубликованные engine v1 и snapshots `/simulations` сохраняют семантику.
  Новая оболочка движка, таблицы и API `/training` используют собственную версию.
  Она может использовать неизменённые чистые функции v1 для последствий действий.
- Сервер задаёт время, длительности, seed, критерии и награды. Клиент передаёт
  намерение и ожидаемую ревизию. Ожидание и прерывание не останавливают мир.
- Учебная смена — 1200 секунд; знакомство — 180; демонстрация — 240.
  Отдельная целевая практика — 180 секунд. Сжатые режимы имеют самостоятельное
  расписание событий; это не ускорение клиентских часов.
- Альтернативная попытка создаётся только из завершённого прогона и воспроизводит
  известное состояние на выбранной секунде. Она не начисляет XP и не изменяет
  исходный результат. Награды выдаются только за рабочий режим, один раз.
- Сохранённая версия содержания, схема и seed принадлежат конкретному прогону.
  Публикация новой версии не меняет уже начатые смены или задания группы.
- Критерии — демонстрационная учебная методика, а не утверждённый регламент
  перевозчика. У каждого есть ID, версия, наблюдаемое основание и объяснение.
  XP отражает активность; освоение навыка вычисляется отдельно по свидетельствам.
- Демо-роли назначаются сервером: сотрудник, инструктор, методист. Сотрудник не
  читает чужие прогоны; инструктор — только назначенные своей группе.
- Только синтетические данные. Никаких внешних провайдеров, LLM-оценки,
  выдуманных исследований, нормативов или личных данных.
- Сохраняем текущую ветку и историю Git; commit/push разрешены пользователем.

## Задача 1. Новая операционная модель и контракт

Backend: новые `domain/training.py`, `application/training.py`,
`persistence/training.py`, `api/training.py`, `training/schema.py` (или близкая
модульная структура), миграция после 20260927_09. Старые API совместимы.

Чистая модель оборачивает v1: фиксированные длительности move/take/inspect/talk/
verify/contact/restrict/assist/record. Один pending action; последствия наступают
по серверному завершению. При отмене затраченное время потеряно, частичный эффект
явно указан в журнале; завершение по deadline имеет приоритет перед отменой.
Мир продолжает обрабатывать остальные события. Разные service/conflict/health/
safety/station действия получают предметные названия. Подтверждённая помощь
не должна требовать невозможного действия. История новых команд воспроизводится
до любого времени, включая выполняемое действие. Строгие лимиты, версии, row lock,
idempotency и сохранение таймерных последствий при ошибочном запросе обязательны.

Содержание версионируется отдельно: стартовая копия demo-v1 в строгом v2 формате,
с расписанием режимов, длительностями, репликами и объяснениями. Новое API
возвращает только известные игроку факты, ни будущие события, ни причины.

Контракт `/api/v1/training`:

- GET `/current` -> `{simulation: TrainingView | null}`.
- POST `/runs`, Idempotency-Key, `{mode: work|tutorial|demo|practice,
  competency_id?: string, assignment_id?: string}` -> TrainingView (201/200).
- GET `/runs/{id}` -> TrainingView; POST `/runs/{id}/actions`, Idempotency-Key,
  тот же ActionRequest что `/simulations` (cancel action_id для прерывания).
- GET `/runs/{id}/debrief` -> прежняя структура SimulationDebrief плюс
  `assessment: {criteria: Evidence[], methodology_version, source_notice}`.
- GET `/runs/{id}/replay?at_seconds=N` -> TrainingView в точке времени;
  только завершённый собственный прогон, не изменяет серверный clock.
- POST `/runs/{id}/fork`, Idempotency-Key, `{at_seconds:N}` -> TrainingView.
- GET `/runs/{id}/comparison` -> `{source_id, source_metrics, current_metrics,
  differences:[{metric,delta}], source_resolved, current_resolved}` для завершённой
  альтернативы. Сравнение описывает сценарную модель, не реальный прогноз.
- GET `/learning` -> `{competencies:[{id,title,score:null|number,evidence_count,
  status: insufficient|developing|steady,strong:boolean}],
  patterns:[{id,title,count,explanation}], recommendations:[{competency_id,title,
  explanation}], statistics:{completed_runs,completed_scenarios}, source_notice}`.
  Свидетельства из новых/старых симуляций и графовых сценариев; не считать XP
  освоением, не усиливать освоение бесконечным повтором одного кейса.
- GET `/history?limit=20&offset=0` -> `{items:[{id,title,mode,status,started_at,
  source_id}],total,limit,offset}`.

TrainingView расширяет прежний SimulationView обязательными полями:
`engine_version:2`, `mode`, `source_id:null|string`, `assignment_id:null|string`,
`reward_eligible:boolean`, `pending_action:null|{id,action_id,label,incident_id,
zone_id,started_at_seconds,completes_at_seconds,interruptible}`, `replay:boolean`.
Action имеет `duration_seconds:number`. Реплики доступны в observation и журнале.
Evidence: `{id,competency_id,title,met:boolean|null,explanation,source,
source_version,evidence_event_ids:string[]}`. Неизмеримый критерий = null.

Timer worker обрабатывает и новые прогоны. Рабочие результаты входят в профиль и
реальный leaderboard; остальные режимы не увеличивают XP/ачивки. Юнит-тесты:
длительность, параллельная эскалация, отмена/завершение, восстановление, replay без
утечки, fork/сравнение, три режима, критерии и повторяемость рекомендаций.
PG/API-тесты: ownership, race/duplicate, no client score/clock, durable rejection,
однократное начисление, независимость исходника, старые snapshots.

## Задача 2. Инструктор и методист

Последовательно после задачи 1. `/training/access` -> `{role:employee|instructor|
methodist,group_id:null|string}`. Серверный allowlist синтетических персон;
инструктор и методист добавлены в demo persona selector с понятными именами.
Роль нельзя передать через API. Методист имеет доступ к редактору; инструктор
к учебной группе. Все решения доступа проверяются сервером, не только в UI.

Контракты инструкторской части `/training`:
- GET `/assignments` -> `{items:[{id,title,mode,created_at,member_count,
  completed_count}]}`; сотруднику только назначения своей группы.
- POST `/assignments`, Idempotency-Key, `{title,mode:work|demo}` -> assignment.
  Содержание и seed назначаются сервером и фиксируются одинаково всей группе.
- GET `/assignments/{id}` -> `{id,title,mode,members:[{employee_id,display_name,
  run_id:null|string,status,metrics:null|Metrics}], difficulties:[{title,count}]}`.
- GET `/instructor/runs/{id}` -> SimulationDebrief (завершённый доступный прогон).
- GET/POST `/instructor/runs/{id}/comments` -> `{items:[{id,event_id,text,
  author_name,created_at}]}` / `{event_id,text}` с Idempotency-Key. Сотрудник
  читает комментарии к собственному прогону. Ссылка event_id обязана существовать.

Редактор `/training/content`:
- GET -> `{items:[{id,version,title,status:draft|published,revision}]}`.
- POST `/drafts`, Idempotency-Key, `{source_id?:string}` -> ContentDocumentView.
- GET `/{id}` -> `{id,version,title,status,revision,document}`.
- PUT `/{id}`, `{expected_revision,document}` -> ContentDocumentView.
- POST `/{id}/validate` -> `{valid,errors:string[],warnings:string[],
  timeline:[{at_seconds,title,kind}]}`.
- POST `/{id}/publish`, Idempotency-Key, `{expected_revision}` -> ContentDocumentView.
  Только валидный новый snapshot; опубликованное содержание неизменяемо.
  Проверки ссылок, длительности и окон станции; предупреждение пересечений.
  Показать field-level ошибки, не stacktrace. Полный JSON экспортируется через
  GET; UI предоставляет ограниченные предметные поля, не произвольный eval.

Тесты: запрет чужих групп и learner-write, одинаковый seed+версия, duplicate,
конфликт редактирования, immutable publish, валидация и pinning активного прогона.

## Задача 3. Пользовательский интерфейс

Общий визуальный язык транспортного сервиса. Новый главный экран учебной смены
использует проверенный вагон/планшет/диспетчерскую. Три режима с честной
длительностью и описанием; целевые практики — из персональных рекомендаций.
Показывать длительность до клика, выполняемое действие и остаток; доступное
прерывание; параллельные уведомления не скрывают действие. Звук только по
явному переключателю пользователя, с визуальным эквивалентом.

Debrief: критерии + доказательства, временная шкала и slider реконструкции,
кнопка альтернативы и сравнение с исходным прогоном. Replay явно отделён от
текущей смены. Персональная траектория показывает достаточность данных,
причину рекомендации, запуск практики и возвращение к рабочей смене.

Ролевые вкладки «Группа» / «Содержание» показываются по server access.
Инструктор: создать назначение, таблица реальных участников/результатов,
разбор и комментарий к выбранному действию. Методист: список версий, создать
черновик, формы расписания/фактов/оборудования/объяснений, validate+preview,
публикация. Ошибки, loading, отмена устаревших запросов, reconnect, клавиатура,
mobile, reduced motion обязательны.

Frontend critical tests: pending action/cancel, stale response, replay/fork,
режимы/критерии, отказ в роли, редактор/validation, сохранение ошибки и повтор.
E2E: demo start → длительные действия → server completion → debrief → replay →
fork; learner cannot instructor-write; methodist publish → назначение группы.

## Задача 4. Проверка, эксплуатация и сдача

Обновить README, API, архитектуру, DEMO_SCRIPT, LIMITATIONS, руководство
инструктора/методиста и методику критериев. Описать synthetic personas,
версионирование, восстановление, границы демо-прав, rollback без удаления данных.
Составить сценарий пользовательского пилота; не заявлять проведение исследования.
Unit/API/PG/frontend/E2E, lint/type/build, чистый Docker Compose и миграции.
Независимая проверка новой модели и всего изменения. Исправить найденное.
Затем commit и push в разрешённую пользователем текущую ветку; остановиться.
