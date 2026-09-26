# План реализации операционной смены

Baseline: main 97d2469, рабочее дерево чистое. Предыдущая проверка: 509 pytest,
107 Vitest, 17 Playwright. Существующие сценарии не переписываются.

## 1. Домен и сервер

- Чистая детерминированная симуляция, JSON-схема/демо, unit-тесты времени,
  тумана информации, ветвления, NPC, оборудования, связи и последствий.
- PostgreSQL snapshot + pinned definition, command receipt, уникальный ledger;
  Alembic 09, worker, API ownership/strict schemas/revision/idempotency.
- Завершение, структурированный debrief, метрики/XP и профиль/leaderboard.
- Проверки восстановленного состояния, одинакового seed, catch-up, границ
  дедлайнов, конкуренции команд, отсутствия утечки hidden state.

## 2. Рабочий интерфейс

- Сделать «Смена» главной, перенести прежний экран в «Тренировки».
- Схема зон с пассажирами/обращениями, маршрут и ETA, выбор зоны/инцидента,
  контекстные глаголы, найденные факты, оборудование, ожидаемые ответы.
- Серверный polling, сохранение потерянного ACK команды, reconnect без паузы
  времени, loading/error, мобильная компоновка, keyboard/reduced-motion.
- Временная линия разбора с фактическими последствиями и альтернативами.

## 3. Проверка и сдача

- Unit/API/PostgreSQL/frontend critical тесты; Playwright новой смены и старого
  режима. Для временных интеграционных проверок внедрять часы в сервис теста,
  не добавлять пользовательский endpoint перемотки.
- Проверить Docker/migrations/worker, desktop и mobile, обновить README/API,
  USER_FLOW/DEMO_SCRIPT/LIMITATIONS/архитектуру, записать фактические результаты.
- Проверка diff/секретов, commit/push текущей ветки без AI-attribution, остановка.

## Контракт UI/API

GET /api/v1/simulations/current → {simulation: SimulationView | null}.
POST /simulations, body {}, Idempotency-Key → SimulationView.
GET /simulations/{id} → SimulationView.
POST /simulations/{id}/actions, body {command_id, expected_revision, action_id,
incident_id: string|null, zone_id: string|null}, Idempotency-Key = command_id.
GET /simulations/{id}/debrief → SimulationDebrief (только завершённая смена).

SimulationView:
- id, title, status: active|completed, revision, server_time (ISO), started_at
  (ISO), elapsed_seconds, duration_seconds, location (zone id).
- zones: [{id,title,kind}], stations: [{id,title,arrival_seconds,departure_seconds,
  status: upcoming|dwell|passed}], passengers: [{id,name,zone_id,observation}].
- incidents: [{id,title,kind,zone_id,passenger_id:null|string,status,severity,
  reported_at_seconds,discovered_at_seconds:null|number,first_reaction_seconds:
  null|number,observation,facts:string[],actions:ActionView[]}]. Только возникшие.
- actions: ActionView[] (доступные глобальные действия: переход/оборудование).
- equipment: [{id,title,zone_id,carried:boolean,available:boolean}].
- communications: [{id,incident_id,type,title,status:pending|answered,
  requested_at_seconds,expected_response_seconds,result:null|string}].
- metrics: {safety,service,regulation,prioritization,communication,
  average_reaction_seconds:null|number}; xp:number; journal: JournalEntry[].
ActionView: {id,label,description,enabled:boolean,reason:null|string,
 zone_id:null|string,incident_id:null|string}.
JournalEntry: {id,at_seconds,kind,incident_id:null|string,title,explanation,
 metric_changes:[{metric,delta,before,after}]}.
SimulationDebrief: {simulation:SimulationView, summary:string,
 incidents:[{id,title,outcome,reported_at_seconds,discovered_at_seconds:null|number,
 first_reaction_seconds:null|number,resolved_at_seconds:null|number,
 alternatives:string[]}],recommendations:string[],achievements:string[]}.

Все nullable поля присутствуют. Client elapsed — только визуальная оценка до
следующего GET; доступность действий и финальное состояние определяет сервер.
Тик без значимого события не меняет revision. Доступные названия действий и
инцидентов приходят из серверного контента, UI не кодирует правильные ответы.

## Завершение

Три части плана выполнены. Фактические проверки и ограничения: [SIMULATION_DELIVERY](SIMULATION_DELIVERY.md).
