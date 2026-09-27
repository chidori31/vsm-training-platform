import { useEffect, useRef, useState } from "react";
import type { ReadResource } from "../career/contracts";
import type { WriteResource } from "../shift/contracts";
import { ApiError, friendlyError } from "../runner/api";
import {
  clock,
  incidentStage,
  metricNames,
  parseCurrentSimulation,
  parseSimulation,
  parseSimulationDebrief,
  type Action,
  type Simulation,
  type SimulationDebrief,
} from "./contracts";
import "./simulation.css";
import {
  parseTraining,
  parseTrainingCurrent,
  parseTrainingDebrief,
  modeNames,
  type Training,
  type TrainingDebrief,
  type TrainingMode,
} from "../training/contracts";
import {
  ModeChooser,
  ActionProgress,
  Assessment,
  ReplayPanel,
  ComparisonPanel,
  LearningPanel,
  MyAssignments,
  LearnerComments,
  SoundSwitch,
  TutorialGuide,
} from "../training/TrainingTools";

type Command = { path: string; body: Record<string, unknown>; key: string };
function loadPending(identityId: string, training: boolean): Command | null {
  try {
    const v = JSON.parse(
      sessionStorage.getItem(
        `vsm.${training ? "training" : "simulation"}.command.${identityId}`,
      ) ?? "null",
    ) as Command | null;
    if (
      v &&
      typeof v.key === "string" &&
      v.key.length <= 128 &&
      typeof v.path === "string" &&
      (training
        ? /^\/training\/runs(?:\/[\w-]+\/(?:actions|fork))?$/
        : /^\/simulations(?:\/[\w-]+\/actions)?$/
      ).test(v.path) &&
      v.body &&
      typeof v.body === "object"
    )
      return v;
  } catch {
    /* Storage is optional; server receipts remain authoritative. */
  }
  return null;
}
function savePending(
  identityId: string,
  command: Command | null,
  training: boolean,
) {
  try {
    const key = `vsm.${training ? "training" : "simulation"}.command.${identityId}`;
    if (command) sessionStorage.setItem(key, JSON.stringify(command));
    else sessionStorage.removeItem(key);
  } catch {
    /* Retry remains available in this mounted view. */
  }
}

function ProfessionalMetrics({ simulation }: { simulation: Simulation }) {
  return (
    <dl className="sim-metrics" aria-label="Профессиональные показатели">
      {Object.entries(metricNames).map(([key, label]) => (
        <div key={key}>
          <dt>{label}</dt>
          <dd>
            {simulation.metrics[key as keyof typeof simulation.metrics]}
            <small>/100</small>
          </dd>
        </div>
      ))}
      <div>
        <dt>Реакция · среднее</dt>
        <dd>
          {simulation.metrics.average_reaction_seconds === null
            ? "—"
            : `${Math.round(simulation.metrics.average_reaction_seconds)} с`}
        </dd>
      </div>
    </dl>
  );
}
function ActionButton({
  action,
  blocked,
  onAction,
}: {
  action: Action;
  blocked: boolean;
  onAction: (action: Action) => void;
}) {
  return (
    <div className="sim-action">
      <button
        disabled={blocked || !action.enabled}
        onClick={() => onAction(action)}
        title={action.description}
      >
        {action.label}
        {action.duration_seconds !== undefined &&
          action.duration_seconds > 0 && (
            <span className="training-action-duration">
              {" "}
              · {action.duration_seconds} с
            </span>
          )}
      </button>
      <small>{!action.enabled ? action.reason : action.description}</small>
    </div>
  );
}
function BlackBox({ value }: { value: SimulationDebrief }) {
  const [filter, setFilter] = useState<string>("all");
  const entries = value.simulation.journal.filter(
    (e) => filter === "all" || e.incident_id === filter,
  );
  return (
    <section className="sim-blackbox" aria-labelledby="sim-debrief-title">
      <div className="sim-heading">
        <div>
          <span className="micro-label">СМЕНА ЗАВЕРШЕНА · ЧЁРНЫЙ ЯЩИК</span>
          <h2 id="sim-debrief-title">Как прошла ваша смена</h2>
          <p>{value.summary}</p>
        </div>
        <strong className="sim-xp">
          +{value.simulation.xp}
          <small>XP за смену</small>
        </strong>
      </div>
      <ProfessionalMetrics simulation={value.simulation} />
      <div className="sim-review-layout">
        <div>
          <label className="sim-filter">
            События в журнале
            <select value={filter} onChange={(e) => setFilter(e.target.value)}>
              <option value="all">Вся смена</option>
              {value.incidents.map((i) => (
                <option key={i.id} value={i.id}>
                  {i.title}
                </option>
              ))}
            </select>
          </label>
          <ol className="sim-blackbox-line" aria-label="Временная линия смены">
            {entries.map((e) => (
              <li key={e.id}>
                <time>T+{clock(e.at_seconds)}</time>
                <div>
                  <strong>{e.title}</strong>
                  <p>{e.explanation}</p>
                  {e.metric_changes.length > 0 && (
                    <ul
                      className="sim-deltas"
                      aria-label="Изменения показателей"
                    >
                      {e.metric_changes.map((c, i) => (
                        <li
                          key={`${c.metric}-${i}`}
                          className={c.delta < 0 ? "loss" : "gain"}
                        >
                          {metricNames[c.metric] ?? c.metric}{" "}
                          {c.delta > 0 ? "+" : ""}
                          {c.delta}{" "}
                          <span>
                            ({c.before} → {c.after})
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </li>
            ))}
          </ol>
        </div>
        <aside className="sim-counterfactual">
          <h3>Разбор по обращениям</h3>
          {value.incidents.map((i) => (
            <details key={i.id}>
              <summary>{i.title}</summary>
              <p>{incidentStage(i.outcome)}</p>
              <dl>
                <div>
                  <dt>Сообщение</dt>
                  <dd>T+{clock(i.reported_at_seconds)}</dd>
                </div>
                <div>
                  <dt>Осмотр</dt>
                  <dd>
                    {i.discovered_at_seconds === null
                      ? "Не проведён"
                      : `T+${clock(i.discovered_at_seconds)}`}
                  </dd>
                </div>
                <div>
                  <dt>Первая реакция</dt>
                  <dd>
                    {i.first_reaction_seconds === null
                      ? "Не было"
                      : `T+${clock(i.first_reaction_seconds)} · через ${Math.round(i.first_reaction_seconds - i.reported_at_seconds)} с`}
                  </dd>
                </div>
                <div>
                  <dt>Завершение</dt>
                  <dd>
                    {i.resolved_at_seconds === null
                      ? "Не завершено"
                      : `T+${clock(i.resolved_at_seconds)}`}
                  </dd>
                </div>
              </dl>
              <h4>Что изменил бы другой подход</h4>
              {i.alternatives.map((a, n) => (
                <p key={n}>{a}</p>
              ))}
            </details>
          ))}
          <h3>На следующую смену</h3>
          <ul>
            {value.recommendations.map((r, n) => (
              <li key={n}>{r}</li>
            ))}
          </ul>
          {value.achievements.length > 0 && (
            <>
              <h3>Учебные достижения</h3>
              <ul>
                {value.achievements.map((a) => (
                  <li key={a}>{a}</li>
                ))}
              </ul>
            </>
          )}
        </aside>
      </div>
    </section>
  );
}

export function SimulationPanel({
  read,
  write,
  identityId,
  training = false,
  runId,
}: {
  read: ReadResource;
  write: WriteResource;
  identityId: string;
  training?: boolean;
  runId?: string;
}) {
  const [requestedRun, setRequestedRun] = useState(runId);
  const runsPath = training ? "/training/runs" : "/simulations";
  const currentPath = requestedRun
    ? `${runsPath}/${encodeURIComponent(requestedRun)}`
    : training
      ? "/training/current"
      : "/simulations/current";
  const [mode, setMode] = useState<TrainingMode>("work");
  const [simulation, setSimulation] = useState<Simulation | null>(null);
  const [debrief, setDebrief] = useState<SimulationDebrief | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [zoneId, setZoneId] = useState<string | null>(null);
  const [incidentId, setIncidentId] = useState<string | null>(null);
  const [pending, setPending] = useState<Command | null>(() =>
    loadPending(identityId, training),
  );
  const pendingRef = useRef(pending);
  const busy = useRef(false);
  const generation = useRef(0);
  const operation = useRef<AbortController | null>(null);
  const current = useRef<Simulation | null>(null);
  const anchor = useRef(0);
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const interval = setInterval(() => {
      const snapshot = current.current;
      if (snapshot?.status === "active")
        setElapsed(
          Math.min(
            snapshot.duration_seconds,
            snapshot.elapsed_seconds +
              Math.max(0, (performance.now() - anchor.current) / 1000),
          ),
        );
    }, 1000);
    return () => clearInterval(interval);
  }, []);
  function accept(next: Simulation | null) {
    const old = current.current;
    if (
      old &&
      next?.id === old.id &&
      (next.revision < old.revision ||
        next.elapsed_seconds < old.elapsed_seconds)
    )
      return;
    current.current = next;
    anchor.current = performance.now();
    setSimulation(next);
    setElapsed(next?.elapsed_seconds ?? 0);
  }
  useEffect(
    () => () => {
      operation.current?.abort();
      generation.current += 1;
    },
    [],
  );
  useEffect(() => {
    const abort = new AbortController();
    let active = true;
    let inFlight = false;
    async function poll(force = false) {
      if (
        busy.current ||
        inFlight ||
        (!force && current.current?.status === "completed")
      )
        return;
      inFlight = true;
      const epoch = generation.current;
      try {
        const next = (
          requestedRun
            ? training
              ? parseTraining
              : parseSimulation
            : training
              ? parseTrainingCurrent
              : parseCurrentSimulation
        )(await read(currentPath, abort.signal));
        if (!active || epoch !== generation.current) return;
        accept(next);
        setLoaded(true);
        if (!pendingRef.current) setError(null);
      } catch (cause) {
        if (active && epoch === generation.current) {
          setError(friendlyError(cause));
          setLoaded(true);
        }
      } finally {
        inFlight = false;
      }
    }
    void poll(true);
    const interval = setInterval(() => void poll(), 2000);
    return () => {
      active = false;
      abort.abort();
      clearInterval(interval);
    };
  }, [read, identityId, refresh, training, currentPath, requestedRun]);
  useEffect(() => {
    if (simulation?.status !== "completed") return;
    const abort = new AbortController();
    void read(
      `${runsPath}/${encodeURIComponent(simulation.id)}/debrief`,
      abort.signal,
    )
      .then((v) => {
        if (!abort.signal.aborted) {
          const parsed = (
            training ? parseTrainingDebrief : parseSimulationDebrief
          )(v);
          if (parsed.simulation.id !== simulation.id)
            throw new Error(
              "Получен разбор другой смены. Обновите подключение.",
            );
          setDebrief(parsed);
          setError(null);
        }
      })
      .catch((cause) => {
        if (!abort.signal.aborted) setError(friendlyError(cause));
      });
    return () => abort.abort();
  }, [read, simulation?.id, simulation?.status, refresh, training, runsPath]);
  function remember(command: Command | null) {
    pendingRef.current = command;
    setPending(command);
    savePending(identityId, command, training);
  }
  async function submit(
    action?: Action,
    startBody?: Record<string, unknown>,
    forkAt?: number,
  ) {
    if (busy.current) return;
    let command = pendingRef.current;
    if (!command) {
      const key = crypto.randomUUID();
      command =
        action && simulation
          ? {
              path: `${runsPath}/${encodeURIComponent(simulation.id)}/actions`,
              key,
              body: {
                command_id: key,
                expected_revision: simulation.revision,
                action_id: action.id,
                incident_id: action.incident_id,
                zone_id: action.zone_id,
              },
            }
          : forkAt !== undefined && simulation
            ? {
                path: `${runsPath}/${encodeURIComponent(simulation.id)}/fork`,
                key,
                body: { at_seconds: forkAt },
              }
            : {
                path: runsPath,
                key,
                body: training ? (startBody ?? { mode }) : {},
              };
      remember(command);
    }
    busy.current = true;
    setWorking(true);
    setError(null);
    generation.current += 1;
    const abort = new AbortController();
    operation.current = abort;
    try {
      const next = (training ? parseTraining : parseSimulation)(
        await write(command.path, command.body, command.key, abort.signal),
      );
      if (abort.signal.aborted) return;
      generation.current += 1;
      remember(null);
      const displayed = current.current;
      if (
        displayed &&
        displayed.id !== next.id &&
        ((command.path !== runsPath && !command.path.endsWith("/fork")) ||
          displayed.status === "active" ||
          Date.parse(next.started_at) < Date.parse(displayed.started_at))
      ) {
        setRefresh((n) => n + 1);
        return;
      }
      if (command.path === runsPath || command.path.endsWith("/fork"))
        setRequestedRun(undefined);
      accept(next);
      setDebrief((previous) =>
        previous?.simulation.id === next.id && next.status === "completed"
          ? previous
          : null,
      );
    } catch (cause) {
      if (abort.signal.aborted) return;
      setError(friendlyError(cause));
      if (cause instanceof ApiError && [404, 409, 422].includes(cause.status)) {
        remember(null);
        setRefresh((n) => n + 1);
      }
    } finally {
      busy.current = false;
      if (!abort.signal.aborted) setWorking(false);
    }
  }
  // The visual clock never decides action availability or completion.
  const selectedZone =
    simulation?.zones.find((z) => z.id === zoneId) ??
    simulation?.zones.find((z) => z.id === simulation.location);
  const localIncidents =
    simulation?.incidents.filter((i) => i.zone_id === selectedZone?.id) ?? [];
  const incident =
    localIncidents.find((i) => i.id === incidentId) ??
    localIncidents.find((i) => i.status !== "resolved") ??
    localIncidents[0];
  const activeIncidents =
    simulation?.incidents.filter((i) => i.status !== "resolved") ?? [];
  const blocked = working || !!pending || !!error;
  const trainingState = training ? (simulation as Training | null) : null;
  const trainingDebrief = training ? (debrief as TrainingDebrief | null) : null;
  return (
    <section className="simulation" aria-label="Операционная смена">
      {error && (
        <div className="sim-connection" role="alert">
          <strong>Проверяем связь с поездом</strong>
          <p>{error} Время смены продолжает идти на сервере.</p>
          <button
            disabled={working}
            onClick={() => (pending ? void submit() : setRefresh((n) => n + 1))}
          >
            {pending ? "Проверить последнее действие" : "Восстановить связь"}
          </button>
        </div>
      )}
      {pending && !error && !working && (
        <div className="sim-connection">
          <p>У последнего действия нет подтверждения.</p>
          <button onClick={() => void submit()}>
            Проверить последнее действие
          </button>
        </div>
      )}
      {!loaded && <p role="status">Подключаемся к рабочей смене…</p>}
      {training && loaded && simulation?.status !== "active" && (
        <ModeChooser mode={mode} onChange={setMode} disabled={blocked} />
      )}
      {trainingState && (
        <div className="training-mode-badge">
          <strong>
            {modeNames[trainingState.mode]}
            {trainingState.source_id ? " · Альтернативная попытка" : ""}
          </strong>
          <span>
            {trainingState.reward_eligible
              ? "Рабочий результат сохраняется в профиле"
              : "Учебная попытка без начисления XP"}
          </span>
          {trainingState.assignment_id && (
            <small>Назначение инструктора · одинаковые условия группы</small>
          )}
        </div>
      )}
      {training && <SoundSwitch simulation={trainingState} />}
      {trainingState && <TutorialGuide simulation={trainingState} />}
      {trainingState?.pending_action && trainingState.status === "active" && (
        <ActionProgress
          action={trainingState.pending_action}
          elapsed={elapsed}
          disabled={blocked}
          onCancel={() =>
            void submit({
              id: "cancel",
              label: "Прервать действие",
              description: "",
              enabled: true,
              reason: null,
              zone_id: null,
              incident_id: null,
            })
          }
        />
      )}
      {loaded && !simulation && !error && (
        <div className="sim-briefing">
          <div className="sim-briefing-number" aria-hidden="true">
            01<span>ВСМ / ЭКИПАЖ</span>
          </div>
          <div>
            <span className="micro-label">
              {training
                ? `${modeNames[mode].toUpperCase()} · ${mode === "work" ? "20 МИНУТ" : mode === "demo" ? "4 МИНУТЫ" : "3 МИНУТЫ"}`
                : "ПРИЁМ СМЕНЫ · 20 МИНУТ"}
            </span>
            <h1>
              Вагон под вашей
              <br />
              ответственностью.
            </h1>
            <p>
              Наблюдайте за обстановкой, уточняйте сообщения пассажиров,
              организуйте помощь. Несколько событий могут требовать внимания
              одновременно.
            </p>
            <div className="sim-briefing-route">
              Москва <span>→</span> Тверь <span>→</span> Великий Новгород{" "}
              <span>→</span> Санкт-Петербург
            </div>
            <p className="quiet-note">
              Учебный маршрут со сжатым временем. Смена продолжится, даже если
              закрыть приложение.
            </p>
            <button
              className="primary-button"
              disabled={blocked}
              onClick={() => void submit()}
            >
              {working
                ? "Принимаем смену…"
                : training && mode !== "work"
                  ? `Начать: ${modeNames[mode]}`
                  : "Принять рабочую смену"}{" "}
              <span aria-hidden="true">→</span>
            </button>
          </div>
          <aside>
            <h2>Перед отправлением</h2>
            <ol>
              <li>Выберите зону на схеме вагона.</li>
              <li>Перейдите к обращению и соберите факты.</li>
              <li>Следите за ответами связи и приближением станции.</li>
              <li>Разберите последствия после прибытия.</li>
            </ol>
            <span className="sim-training-stamp">
              СИНТЕТИЧЕСКИЙ РЕЙС
              <br />
              ДЕЙСТВИЯ И ПОСЛЕДСТВИЯ
            </span>
          </aside>
        </div>
      )}
      {simulation && (
        <>
          <div className="sim-heading">
            <div>
              <span className="micro-label">РАБОЧАЯ СМЕНА · ВАГОН 01</span>
              <h1>{simulation.title}</h1>
            </div>
            <div className="sim-clock" aria-label="Время смены">
              <small>
                {simulation.status === "completed"
                  ? "ПОЕЗДКА ЗАВЕРШЕНА"
                  : "ОТ НАЧАЛА СМЕНЫ"}
              </small>
              <strong>{clock(elapsed)}</strong>
              <span>/ {clock(simulation.duration_seconds)}</span>
            </div>
          </div>
          <div className="sim-route">
            <div
              className="sim-route-track"
              role="progressbar"
              aria-label="Маршрут поездки"
              aria-valuemin={0}
              aria-valuemax={simulation.duration_seconds}
              aria-valuenow={Math.floor(elapsed)}
            >
              <span
                style={{
                  width: `${(100 * elapsed) / simulation.duration_seconds}%`,
                }}
              />
            </div>
            <ol>
              {simulation.stations.map((s) => (
                <li key={s.id} className={s.status}>
                  <strong>{s.title}</strong>
                  <small>
                    {s.status === "passed"
                      ? "Отправились"
                      : s.status === "dwell"
                        ? `Стоянка · ${clock(Math.max(0, s.departure_seconds - elapsed))}`
                        : `Через ${clock(Math.max(0, s.arrival_seconds - elapsed))}`}
                  </small>
                </li>
              ))}
            </ol>
          </div>
          {simulation.status === "active" ? (
            <>
              <ProfessionalMetrics simulation={simulation} />
              <div className="sim-operation">
                <div className="sim-carriage-panel">
                  <div className="sim-section-label">
                    <h2>Обстановка в вагоне</h2>
                    <span>{activeIncidents.length} в работе</span>
                  </div>
                  <div
                    className="sim-map"
                    role="group"
                    aria-label="Схема вагона"
                  >
                    <div className="sim-direction" aria-hidden="true">
                      К ГОЛОВЕ ПОЕЗДА ↑
                    </div>
                    <div className="sim-carriage">
                      {simulation.zones.map((zone, n) => {
                        const incidents = simulation.incidents.filter(
                          (i) =>
                            i.zone_id === zone.id && i.status !== "resolved",
                        );
                        const people = simulation.passengers.filter(
                          (p) => p.zone_id === zone.id,
                        );
                        const urgent = incidents.some(
                          (i) =>
                            i.severity >= 3 ||
                            i.status === "critical" ||
                            i.status === "escalated",
                        );
                        return (
                          <button
                            key={zone.id}
                            className={`sim-zone sim-zone-${zone.kind} ${selectedZone?.id === zone.id ? "selected" : ""} ${urgent ? "urgent" : ""}`}
                            aria-pressed={selectedZone?.id === zone.id}
                            onClick={() => {
                              setZoneId(zone.id);
                              setIncidentId(null);
                            }}
                          >
                            <span className="sim-zone-index">
                              {String(n + 1).padStart(2, "0")}
                            </span>
                            <strong>{zone.title}</strong>
                            <span className="sim-zone-seats" aria-hidden="true">
                              {Array.from(
                                { length: people.length ? 4 : 2 },
                                (_, i) => (
                                  <i
                                    className={
                                      i < people.length ? "occupied" : ""
                                    }
                                    key={i}
                                  />
                                ),
                              )}
                            </span>
                            <span className="sim-zone-info">
                              {simulation.location === zone.id && (
                                <span className="sim-you">● Вы здесь</span>
                              )}
                              {incidents.length > 0 && (
                                <span className="sim-zone-alert">
                                  {urgent ? "! " : "● "}
                                  {incidents.length}{" "}
                                  {incidents.length === 1
                                    ? "обращение"
                                    : "обращения"}
                                </span>
                              )}
                              {people.length > 0 && (
                                <span>{people.length} пасс.</span>
                              )}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  </div>
                  <div className="sim-map-legend">
                    <span>● Проводник</span>
                    <span>□ Пассажирские места</span>
                    <span>! Требует внимания</span>
                  </div>
                  <div className="sim-equipment">
                    <h3>Служебное оборудование</h3>
                    <ul>
                      {simulation.equipment.map((e) => (
                        <li key={e.id}>
                          <strong>{e.title}</strong>
                          <span>
                            {e.carried
                              ? "При вас"
                              : !e.available
                                ? "Недоступно"
                                : simulation.zones.find(
                                    (z) => z.id === e.zone_id,
                                  )?.title}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
                <section className="sim-tablet" aria-label="Контекст зоны">
                  <div className="sim-section-label">
                    <span className="micro-label">СЛУЖЕБНЫЙ ПЛАНШЕТ</span>
                    <span>
                      {simulation.location === selectedZone?.id
                        ? "НА МЕСТЕ"
                        : "ОБЗОР ЗОНЫ"}
                    </span>
                  </div>
                  <h2>{selectedZone?.title}</h2>
                  {localIncidents.length > 1 && (
                    <div
                      className="sim-local-tabs"
                      aria-label="Обращения в зоне"
                    >
                      {localIncidents.map((i) => (
                        <button
                          key={i.id}
                          aria-pressed={incident?.id === i.id}
                          onClick={() => setIncidentId(i.id)}
                        >
                          {i.title}
                        </button>
                      ))}
                    </div>
                  )}
                  {incident ? (
                    <>
                      <div className={`sim-incident-state ${incident.status}`}>
                        {incidentStage(incident.status)} · T+
                        {clock(incident.reported_at_seconds)}
                      </div>
                      <h3>{incident.title}</h3>
                      <p className="sim-observation">{incident.observation}</p>
                      {incident.passenger_id &&
                        simulation.passengers
                          .filter((p) => p.id === incident.passenger_id)
                          .map((p) => (
                            <div className="sim-person" key={p.id}>
                              <span aria-hidden="true">◎</span>
                              <div>
                                <strong>{p.name}</strong>
                                <p>{p.observation}</p>
                              </div>
                            </div>
                          ))}
                      <div className="sim-facts">
                        <h4>Установленные факты</h4>
                        {incident.facts.length ? (
                          <ul>
                            {incident.facts.map((f, n) => (
                              <li key={n}>{f}</li>
                            ))}
                          </ul>
                        ) : (
                          <p>
                            Подробности пока не установлены. Начните с
                            наблюдения и разговора.
                          </p>
                        )}
                      </div>
                      <div
                        className="sim-context-actions"
                        aria-label="Действия в ситуации"
                      >
                        {incident.actions.map((a) => (
                          <ActionButton
                            key={a.id}
                            action={a}
                            blocked={blocked}
                            onAction={(a) => void submit(a)}
                          />
                        ))}
                      </div>
                    </>
                  ) : (
                    <div className="sim-no-incident">
                      <span aria-hidden="true">◎</span>
                      <h3>Наблюдение за обстановкой</h3>
                      <p>
                        В этой зоне нет зарегистрированных обращений. Проверьте
                        оборудование и следите за поступающими сообщениями.
                      </p>
                      {simulation.passengers
                        .filter((p) => p.zone_id === selectedZone?.id)
                        .map((p) => (
                          <p key={p.id}>
                            <strong>{p.name}</strong> · {p.observation}
                          </p>
                        ))}
                    </div>
                  )}
                  <div
                    className="sim-global-actions"
                    aria-label="Перемещение и оборудование"
                  >
                    {simulation.actions
                      .filter(
                        (a) =>
                          (!training || a.id !== "cancel") &&
                          (a.zone_id === null ||
                            a.zone_id === selectedZone?.id),
                      )
                      .map((a) => (
                        <ActionButton
                          key={a.id}
                          action={a}
                          blocked={blocked}
                          onAction={(a) => void submit(a)}
                        />
                      ))}
                  </div>
                  {working && (
                    <p role="status" className="sim-saving">
                      Передаём действие…
                    </p>
                  )}
                </section>
                <aside className="sim-dispatch">
                  <div className="sim-section-label">
                    <h2>Диспетчерская лента</h2>
                    <span
                      className="sim-live-dot"
                      aria-label="События обновляются"
                    />
                  </div>
                  <div
                    className="sim-live-announcement"
                    role="status"
                    aria-live="polite"
                  >
                    {activeIncidents.length
                      ? `В работе: ${activeIncidents.length}. ${activeIncidents.at(-1)?.title}.`
                      : "Активных обращений нет."}
                  </div>
                  <ol className="sim-dispatch-list">
                    {activeIncidents.map((i) => (
                      <li key={i.id} className={i.status}>
                        <button
                          onClick={() => {
                            setZoneId(i.zone_id);
                            setIncidentId(i.id);
                          }}
                        >
                          <time>T+{clock(i.reported_at_seconds)}</time>
                          <strong>{i.title}</strong>
                          <small>
                            {incidentStage(i.status)} ·{" "}
                            {
                              simulation.zones.find((z) => z.id === i.zone_id)
                                ?.title
                            }
                          </small>
                        </button>
                      </li>
                    ))}
                  </ol>
                  <h3>Служебная связь</h3>
                  {!simulation.communications.length && (
                    <p className="sim-empty-note">
                      Запросов пока нет. Связь доступна в действиях по
                      обращению.
                    </p>
                  )}
                  {simulation.communications.map((c) => (
                    <div className="sim-radio" key={c.id}>
                      <span aria-hidden="true">◉</span>
                      <div>
                        <strong>{c.title}</strong>
                        <small>
                          {c.status === "pending"
                            ? `Ожидаем ответ · ${clock(Math.max(0, c.expected_response_seconds - elapsed))}`
                            : "Ответ получен"}
                        </small>
                        {c.result && <p>{c.result}</p>}
                      </div>
                    </div>
                  ))}
                  <h3>Последние записи</h3>
                  <ol className="sim-recent">
                    {simulation.journal
                      .slice(-5)
                      .reverse()
                      .map((e) => (
                        <li key={e.id}>
                          <time>{clock(e.at_seconds)}</time>
                          <span>{e.title}</span>
                        </li>
                      ))}
                  </ol>
                </aside>
              </div>
            </>
          ) : debrief?.simulation.id === simulation.id ? (
            <>
              <BlackBox value={debrief} />
              {trainingState && trainingDebrief && (
                <>
                  <Assessment
                    assessment={trainingDebrief.assessment}
                    journal={trainingState.journal}
                  />
                  <ComparisonPanel simulation={trainingState} read={read} />
                  <ReplayPanel
                    key={trainingState.id}
                    simulation={trainingState}
                    read={read}
                    disabled={blocked}
                    onFork={(at) => void submit(undefined, undefined, at)}
                  />
                  <LearnerComments read={read} runId={trainingState.id} />
                </>
              )}
            </>
          ) : (
            <p role="status">Собираем журнал смены…</p>
          )}
          {simulation.status === "completed" && (
            <button
              className="primary-button sim-restart"
              disabled={blocked}
              onClick={() => void submit()}
            >
              {training ? `Начать: ${modeNames[mode]}` : "Принять новую смену"}{" "}
              <span aria-hidden="true">→</span>
            </button>
          )}
        </>
      )}
      {training && loaded && simulation?.status !== "active" && (
        <>
          <MyAssignments
            read={read}
            onOpen={(id) => {
              generation.current += 1;
              setDebrief(null);
              accept(null);
              setLoaded(false);
              setRequestedRun(id);
              setRefresh((n) => n + 1);
            }}
            disabled={blocked}
            onStart={(id, assignedMode) =>
              void submit(undefined, { mode: assignedMode, assignment_id: id })
            }
          />
          <LearningPanel
            key={simulation?.id ?? "initial"}
            read={read}
            disabled={blocked}
            onPractice={(id) =>
              void submit(undefined, { mode: "practice", competency_id: id })
            }
          />
        </>
      )}
    </section>
  );
}
