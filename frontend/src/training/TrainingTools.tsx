import { useEffect, useRef, useState } from "react";
import type { ReadResource } from "../career/contracts";
import type { JournalEntry } from "../simulation/contracts";
import { clock, incidentStage, metricNames } from "../simulation/contracts";
import { friendlyError } from "../runner/api";
import {
  modeNames,
  parseComparison,
  parseLearning,
  parseTraining,
  parseAssignments,
  parseAssignment,
  parseComments,
  type Training,
  type TrainingDebrief,
  type TrainingMode,
  type PendingAction,
} from "./contracts";
import "./training.css";

export function useResource<T>(
  read: ReadResource,
  path: string | null,
  parse: (v: unknown) => T,
) {
  const [result, setResult] = useState<{ path: string; value: T } | null>(null);
  const [failure, setFailure] = useState<{
    path: string;
    message: string;
  } | null>(null);
  const [generation, reload] = useState(0);
  useEffect(() => {
    if (!path) return;
    const abort = new AbortController();
    void read(path, abort.signal)
      .then((v) => {
        if (abort.signal.aborted) return;
        setResult({ path, value: parse(v) });
        setFailure(null);
      })
      .catch((e) => {
        if (!abort.signal.aborted)
          setFailure({ path, message: friendlyError(e) });
      });
    return () => abort.abort();
  }, [read, path, parse, generation]);
  return {
    value: result?.path === path ? result.value : null,
    error: failure?.path === path ? failure.message : null,
    reload: () => reload((n) => n + 1),
  };
}
export function ResourceError({
  message,
  retry,
}: {
  message: string | null;
  retry: () => void;
}) {
  return message ? (
    <div className="training-error" role="alert">
      <p>{message}</p>
      <button onClick={retry}>Повторить загрузку</button>
    </div>
  ) : null;
}

export function ModeChooser({
  mode,
  onChange,
  disabled = false,
}: {
  mode: TrainingMode;
  onChange: (mode: TrainingMode) => void;
  disabled?: boolean;
}) {
  return (
    <fieldset className="training-modes" disabled={disabled}>
      <legend>Формат поездки</legend>
      {(
        [
          [
            "tutorial",
            "3 минуты",
            "Освойте планшет, перемещение и связь. Без начисления XP.",
          ],
          [
            "work",
            "20 минут",
            "Полная смена: параллельные события, приоритеты и рабочий результат.",
          ],
          [
            "demo",
            "4 минуты",
            "Короткий демонстрационный рейс. Отдельное расписание, без XP.",
          ],
        ] as const
      ).map(([id, duration, description], index) => (
        <label key={id} className={mode === id ? "selected" : ""}>
          <input
            type="radio"
            name="training-mode"
            value={id}
            checked={mode === id}
            onChange={() => onChange(id)}
          />
          <span className="training-mode-index">0{index + 1}</span>
          <span>
            <strong>{modeNames[id]}</strong>
            <small>{duration}</small>
            <span>{description}</span>
          </span>
        </label>
      ))}
    </fieldset>
  );
}
export function ActionProgress({
  action,
  elapsed,
  onCancel,
  disabled,
}: {
  action: PendingAction;
  elapsed: number;
  onCancel: () => void;
  disabled: boolean;
}) {
  const duration = action.completes_at_seconds - action.started_at_seconds;
  const remaining = Math.max(0, action.completes_at_seconds - elapsed);
  return (
    <section
      className="training-action-progress"
      aria-label="Выполняемое действие"
    >
      <div>
        <span className="micro-label">ДЕЙСТВИЕ В ПРОЦЕССЕ</span>
        <h2>{action.label}</h2>
        <p role="status">
          {remaining > 0
            ? `Осталось около ${Math.ceil(remaining)} с`
            : "Ожидаем подтверждение завершения"}
          . Другие события продолжаются.
        </p>
        <progress
          aria-label="Ход действия"
          max={duration}
          value={Math.min(
            duration,
            Math.max(0, elapsed - action.started_at_seconds),
          )}
        />
      </div>
      {action.interruptible && (
        <button disabled={disabled || remaining === 0} onClick={onCancel}>
          Прервать действие<small>Потраченное время не вернётся</small>
        </button>
      )}
    </section>
  );
}
export function TutorialGuide({ simulation }: { simulation: Training }) {
  if (simulation.mode !== "tutorial" || simulation.status !== "active")
    return null;
  const incident = simulation.incidents[0];
  const done = incident?.status === "resolved";
  const next = incident?.actions.find((a) => a.enabled && a.id !== "defer");
  const zone = simulation.zones.find((z) => z.id === incident?.zone_id);
  const instruction = simulation.pending_action
    ? `Дождитесь завершения: ${simulation.pending_action.label}. Посмотрите на часы и диспетчерскую ленту.`
    : !incident
      ? "Посмотрите на схему вагона. Первое обращение появится в ленте."
      : simulation.location !== incident.zone_id
        ? `Выберите «${zone?.title}» на схеме и выполните перемещение в эту зону.`
        : done
          ? "Обращение завершено. Зафиксируйте результат, если действие доступно; после прибытия откроется разбор."
          : next
            ? `Следующий шаг: ${next.label}. Длительность указана рядом с действием.`
            : "Прочитайте условия недоступных действий. Сверьте факты и требуемое оборудование.";
  return (
    <aside className="training-tutorial" aria-label="Подсказка знакомства">
      <span className="micro-label">ЗНАКОМСТВО С РАБОЧИМ МЕСТОМ</span>
      <p>{instruction}</p>
      <small>
        Вы управляете действиями проводника. Схема выбирает контекст;
        перемещение выполняется отдельной командой.
      </small>
    </aside>
  );
}
export function Assessment({
  assessment,
  journal,
}: {
  assessment: TrainingDebrief["assessment"];
  journal: JournalEntry[];
}) {
  return (
    <section className="training-assessment" aria-labelledby="assessment-title">
      <div className="training-section-heading">
        <span className="micro-label">НАБЛЮДАЕМЫЕ ДЕЙСТВИЯ → КРИТЕРИИ</span>
        <h2 id="assessment-title">Основания оценки</h2>
        <p>{assessment.source_notice}</p>
      </div>
      <ul>
        {assessment.criteria.map((c) => (
          <li
            key={c.id}
            className={c.met === null ? "unobserved" : c.met ? "met" : "missed"}
          >
            <div className="training-criterion-result">
              <span aria-hidden="true">
                {c.met === null ? "—" : c.met ? "✓" : "!"}
              </span>
              {c.met === null
                ? "Недостаточно данных"
                : c.met
                  ? "Подтверждено"
                  : "Нужно отработать"}
            </div>
            <div>
              <h3>{c.title}</h3>
              <p>{c.explanation}</p>
              {c.evidence_event_ids.length > 0 && (
                <details>
                  <summary>Действия, на которых основан вывод</summary>
                  <ol>
                    {c.evidence_event_ids.map((id) => {
                      const event = journal.find((e) => e.id === id);
                      return (
                        <li key={id}>
                          {event
                            ? `T+${clock(event.at_seconds)} · ${event.title} — ${event.explanation}`
                            : id}
                        </li>
                      );
                    })}
                  </ol>
                </details>
              )}
              <small>
                {c.source} · версия {c.source_version}
              </small>
            </div>
          </li>
        ))}
      </ul>
      <p className="quiet-note">
        Методика: {assessment.methodology_version}. XP отражает учебную
        активность; это отдельный показатель от освоения навыка.
      </p>
    </section>
  );
}
export function ReplayPanel({
  simulation,
  read,
  onFork,
  disabled = false,
}: {
  simulation: Training;
  read: ReadResource;
  onFork: (seconds: number) => void;
  disabled?: boolean;
}) {
  const [at, setAt] = useState(0);
  const [queryAt, setQueryAt] = useState(0);
  useEffect(() => {
    const timer = setTimeout(() => setQueryAt(at), 120);
    return () => clearTimeout(timer);
  }, [at]);
  const replay = useResource(
    read,
    `/training/runs/${encodeURIComponent(simulation.id)}/replay?at_seconds=${queryAt}`,
    parseTraining,
  );
  const snapshot =
    replay.value?.id === simulation.id &&
    replay.value.replay &&
    replay.value.elapsed_seconds === at
      ? replay.value
      : null;
  return (
    <section className="training-replay" aria-labelledby="replay-title">
      <div className="training-section-heading">
        <span className="micro-label">РЕКОНСТРУКЦИЯ СМЕНЫ</span>
        <h2 id="replay-title">Вернуться к моменту решения</h2>
        <p>
          Здесь только то, что было известно к выбранному моменту. Исходная
          смена уже завершена и сохранена.
        </p>
      </div>
      <div className="training-replay-control">
        <label htmlFor="replay-time">
          Момент воспроизведения <strong>T+{clock(at)}</strong>
        </label>
        <input
          id="replay-time"
          aria-label="Момент воспроизведения"
          type="range"
          min={0}
          max={simulation.duration_seconds}
          step={1}
          value={at}
          onChange={(e) => setAt(Number(e.target.value))}
        />
        <div className="training-replay-markers">
          {simulation.journal
            .filter((e) =>
              ["action", "action_started", "action_interrupted"].includes(
                e.kind,
              ),
            )
            .slice(0, 30)
            .map((e) => (
              <button
                key={e.id}
                onClick={() => setAt(e.at_seconds)}
                title={e.title}
              >
                T+{clock(e.at_seconds)}
              </button>
            ))}
        </div>
      </div>
      <ResourceError message={replay.error} retry={replay.reload} />
      {snapshot ? (
        <>
          <div
            className="training-replay-carriage"
            aria-label="Вагон в выбранный момент"
          >
            {snapshot.zones.map((z) => (
              <div
                key={z.id}
                className={snapshot.location === z.id ? "current" : ""}
              >
                <strong>{z.title}</strong>
                {snapshot.location === z.id && <small>● Проводник здесь</small>}
                <span>
                  {
                    snapshot.incidents.filter(
                      (i) => i.zone_id === z.id && i.status !== "resolved",
                    ).length
                  }{" "}
                  обращ.
                </span>
              </div>
            ))}
          </div>
          {snapshot.pending_action && (
            <p className="training-replay-pending">
              Выполняется: {snapshot.pending_action.label} · до T+
              {clock(snapshot.pending_action.completes_at_seconds)}
            </p>
          )}
          <div className="training-known-facts">
            {snapshot.incidents.length ? (
              snapshot.incidents.map((i) => (
                <article key={i.id}>
                  <h3>
                    {i.title} <small>{incidentStage(i.status)}</small>
                  </h3>
                  <p>{i.observation}</p>
                  {i.facts.length > 0 ? (
                    <ul>
                      {i.facts.map((f, n) => (
                        <li key={n}>{f}</li>
                      ))}
                    </ul>
                  ) : (
                    <small>Дополнительные факты ещё не установлены</small>
                  )}
                </article>
              ))
            ) : (
              <p>К этому моменту обращений ещё не поступало.</p>
            )}
          </div>
        </>
      ) : (
        !replay.error && <p role="status">Восстанавливаем обстановку…</p>
      )}
      <button
        className="primary-button"
        disabled={disabled || !snapshot || at >= simulation.duration_seconds}
        onClick={() => onFork(at)}
      >
        Попробовать иначе с этого момента
      </button>
      <p className="quiet-note">
        Создаст отдельную учебную попытку с теми же событиями. XP не
        начисляется; первоначальный результат не изменится.
      </p>
    </section>
  );
}
export function ComparisonPanel({
  simulation,
  read,
}: {
  simulation: Training;
  read: ReadResource;
}) {
  const result = useResource(
    read,
    simulation.source_id
      ? `/training/runs/${encodeURIComponent(simulation.id)}/comparison`
      : null,
    parseComparison,
  );
  if (!simulation.source_id) return null;
  return (
    <section className="training-comparison">
      <h2>Что изменил другой подход</h2>
      <ResourceError message={result.error} retry={result.reload} />
      {result.value ? (
        <>
          <p>
            Завершённых обращений: {result.value.source_resolved} →{" "}
            {result.value.current_resolved}
          </p>
          <div className="training-comparison-scores">
            {result.value.differences.map((d) => (
              <div key={d.metric}>
                <span>{metricNames[d.metric] ?? d.metric}</span>
                <strong>
                  {d.delta > 0 ? "+" : ""}
                  {d.delta}
                </strong>
                <small>
                  {result.value!.source_metrics[d.metric]} →{" "}
                  {result.value!.current_metrics[d.metric]}
                </small>
              </div>
            ))}
          </div>
          <p className="quiet-note">
            Разница рассчитана в учебной модели. Это не прогноз реального
            происшествия.
          </p>
        </>
      ) : (
        !result.error && <p role="status">Сравниваем последствия…</p>
      )}
    </section>
  );
}
export function LearningPanel({
  read,
  onPractice,
  disabled = false,
}: {
  read: ReadResource;
  onPractice: (competencyId: string) => void;
  disabled?: boolean;
}) {
  const result = useResource(read, "/training/learning", parseLearning);
  return (
    <section className="training-learning" aria-labelledby="learning-title">
      <div className="training-section-heading">
        <span className="micro-label">ЛИЧНАЯ УЧЕБНАЯ ТРАЕКТОРИЯ</span>
        <h2 id="learning-title">От наблюдения — к следующей практике</h2>
        <p>Смена → разбор → короткая отработка → новая рабочая смена.</p>
      </div>
      <ResourceError message={result.error} retry={result.reload} />
      {result.value ? (
        <>
          <div className="training-competencies">
            {result.value.competencies.map((c) => (
              <div key={c.id}>
                <div>
                  <strong>{c.title}</strong>
                  <span>
                    {c.score === null ? "—" : `${Math.round(c.score)} / 100`}
                  </span>
                </div>
                <meter
                  min={0}
                  max={100}
                  value={c.score ?? 0}
                  aria-label={`${c.title}: освоение`}
                />
                <small>
                  {c.status === "insufficient"
                    ? "Недостаточно независимых наблюдений"
                    : c.strong
                      ? "Устойчивая сильная сторона"
                      : c.status === "steady"
                        ? "Навык подтверждается"
                        : "Нужна практика"}{" "}
                  · свидетельств: {c.evidence_count}
                </small>
              </div>
            ))}
          </div>
          {result.value.patterns.length > 0 && (
            <div className="training-patterns">
              <h3>Повторяющиеся затруднения</h3>
              {result.value.patterns.map((p) => (
                <p key={p.id}>
                  <strong>
                    {p.title} · {p.count}
                  </strong>
                  <span>{p.explanation}</span>
                </p>
              ))}
            </div>
          )}
          <div className="training-recommendations">
            {result.value.recommendations.map((r) => (
              <article key={r.competency_id}>
                <div>
                  <span className="micro-label">СЛЕДУЮЩИЙ ШАГ · 3 МИНУТЫ</span>
                  <h3>{r.title}</h3>
                  <p>{r.explanation}</p>
                </div>
                <button
                  disabled={disabled}
                  onClick={() => onPractice(r.competency_id)}
                  aria-label={`Отработать: ${r.title}`}
                >
                  Начать отработку →
                </button>
              </article>
            ))}
          </div>
          <p className="quiet-note">
            Пройдено смен: {result.value.statistics.completed_runs}; сценариев:{" "}
            {result.value.statistics.completed_scenarios}.{" "}
            {result.value.source_notice}
          </p>
        </>
      ) : (
        !result.error && <p role="status">Собираем учебную траекторию…</p>
      )}
    </section>
  );
}
export function MyAssignments({
  read,
  onStart,
  onOpen,
  disabled = false,
}: {
  read: ReadResource;
  onStart: (id: string, mode: TrainingMode) => void;
  onOpen: (id: string) => void;
  disabled?: boolean;
}) {
  const result = useResource(read, "/training/assignments", parseAssignments);
  const [opening, setOpening] = useState(false);
  const [openError, setOpenError] = useState<string | null>(null);
  const operation = useRef<AbortController | null>(null);
  useEffect(() => () => operation.current?.abort(), []);
  async function openAssignment(id: string, mode: TrainingMode) {
    if (operation.current) return;
    const abort = new AbortController();
    operation.current = abort;
    setOpening(true);
    setOpenError(null);
    try {
      const detail = parseAssignment(
        await read(
          `/training/assignments/${encodeURIComponent(id)}`,
          abort.signal,
        ),
      );
      if (abort.signal.aborted) return;
      const member = detail.members[0];
      if (!member)
        throw new Error("Участник назначения не найден. Обновите страницу.");
      if (member.run_id) onOpen(member.run_id);
      else onStart(id, mode);
    } catch (cause) {
      if (!abort.signal.aborted) setOpenError(friendlyError(cause));
    } finally {
      operation.current = null;
      if (!abort.signal.aborted) setOpening(false);
    }
  }

  if (!result.value?.length && !result.error) return null;
  return (
    <section className="training-assignments">
      <h2>Назначено инструктором</h2>
      {openError && <p role="alert">{openError}</p>}
      <ResourceError message={result.error} retry={result.reload} />
      {result.value?.map((a) => (
        <div key={a.id}>
          <div>
            <strong>{a.title}</strong>
            <small>
              {modeNames[a.mode]} · одинаковые условия для всей группы
            </small>
          </div>
          <button
            disabled={disabled || opening}
            onClick={() => void openAssignment(a.id, a.mode)}
          >
            Открыть назначение
          </button>
        </div>
      ))}
    </section>
  );
}
export function LearnerComments({
  read,
  runId,
}: {
  read: ReadResource;
  runId: string;
}) {
  const result = useResource(
    read,
    `/training/instructor/runs/${encodeURIComponent(runId)}/comments`,
    parseComments,
  );
  return (
    <section className="training-comments">
      <h2>Обратная связь инструктора</h2>
      <ResourceError message={result.error} retry={result.reload} />
      {result.value?.length
        ? result.value.map((c) => (
            <blockquote key={c.id}>
              <p>{c.text}</p>
              <footer>
                {c.author_name} · действие {c.event_id}
              </footer>
            </blockquote>
          ))
        : !result.error && <p className="quiet-note">Комментариев пока нет.</p>}
    </section>
  );
}

export function SoundSwitch({ simulation }: { simulation: Training | null }) {
  const [enabled, setEnabled] = useState(false);
  const audio = useRef<AudioContext | null>(null);
  const last = useRef<{ runId: string; ids: Set<string> } | null>(null);
  useEffect(
    () => () => {
      void audio.current?.close();
    },
    [],
  );
  useEffect(() => {
    const previous = last.current;
    const events = simulation?.journal ?? [];
    const sameRun = previous?.runId === simulation?.id;
    const event = events
      .filter(
        (entry) =>
          !previous?.ids.has(entry.id) &&
          ["reported", "communication", "escalation"].includes(entry.kind),
      )
      .at(-1);
    last.current = simulation
      ? { runId: simulation.id, ids: new Set(events.map((entry) => entry.id)) }
      : null;
    if (!enabled || !sameRun || !event || !audio.current) return;
    const context = audio.current;
    if (context.state !== "running") return;
    const tone = context.createOscillator(),
      gain = context.createGain();
    tone.frequency.value = event.kind === "escalation" ? 440 : 660;
    gain.gain.setValueAtTime(0.035, context.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, context.currentTime + 0.18);
    tone.connect(gain);
    gain.connect(context.destination);
    tone.start();
    tone.stop(context.currentTime + 0.2);
  }, [simulation, enabled]);
  return (
    <button
      className="training-sound"
      aria-pressed={enabled}
      onClick={() => {
        if (!enabled) {
          try {
            audio.current ??= new AudioContext();
            void audio.current.resume().catch(() => setEnabled(false));
          } catch {
            return;
          }
        }
        setEnabled((v) => !v);
      }}
    >
      Звуковые сигналы: {enabled ? "включены" : "выключены"}
      <small>Все сообщения также видны в диспетчерской ленте</small>
    </button>
  );
}
