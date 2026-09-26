import { useEffect, useRef, useState } from "react";
import {
  competencyName,
  parseProgress,
  type ReadResource,
} from "../career/contracts";
import { useLearningResource, displayNumber } from "../learning/resource";
import { Debrief } from "../learning/Debrief";
import { ApiError, friendlyError } from "../runner/api";
import {
  parseCurrentShift,
  parseShift,
  type Shift,
  type WriteResource,
} from "./contracts";

export function ShiftRoute({ shift }: { shift: Shift }) {
  return (
    <ol className="shift-route" aria-label="Маршрут учебной смены">
      <li className="completed">
        <span aria-hidden="true">✓</span>
        <div>
          <strong>Приём смены</strong>
          <small>Готовность к работе</small>
        </div>
      </li>
      {shift.steps.map((step) => (
        <li
          key={step.index}
          className={step.status}
          aria-label={`${step.title}: ${step.status === "completed" ? "завершено" : step.status === "active" ? "текущее событие" : "впереди"}${step.kind === "critical" ? ", критическое событие" : ""}`}
          aria-current={
            step.index === shift.current_step && shift.status === "active"
              ? "step"
              : undefined
          }
        >
          <span aria-hidden="true">
            {step.status === "completed"
              ? "✓"
              : String(step.index + 1).padStart(2, "0")}
          </span>
          <div>
            <strong>{step.title}</strong>
            <small>
              {step.kind === "critical"
                ? "Критическое событие"
                : step.status === "locked"
                  ? "Впереди по маршруту"
                  : step.status === "completed"
                    ? "Завершено"
                    : "Текущее событие"}
            </small>
          </div>
        </li>
      ))}
      <li className={shift.status === "completed" ? "active" : "locked"}>
        <span aria-hidden="true">◎</span>
        <div>
          <strong>Итог смены</strong>
          <small>Разбор решений</small>
        </div>
      </li>
    </ol>
  );
}

export function ShiftPanel({
  read,
  write,
  identityId,
  sessionId,
  sessionCompleted,
  revision,
  openSession,
  busy,
  onShiftComplete,
}: {
  read: ReadResource;
  write: WriteResource;
  identityId: string;
  sessionId?: string;
  sessionCompleted: boolean;
  revision: number;
  openSession: (id: string) => Promise<void>;
  busy: boolean;
  onShiftComplete?: () => void;
}) {
  const [shift, setShift] = useState<Shift | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [difficulty, setDifficulty] = useState("standard");
  const [reviewId, setReviewId] = useState<string | null>(null);
  const pending = useRef<{ path: string; body: unknown; key: string } | null>(
    null,
  );
  const generation = useRef(0);
  const operation = useRef<AbortController | null>(null);
  useEffect(() => () => operation.current?.abort(), []);
  useEffect(() => {
    const abort = new AbortController();
    const requestGeneration = ++generation.current;
    void read("/shifts/current", abort.signal)
      .then((value) => {
        if (!abort.signal.aborted && requestGeneration === generation.current) {
          setShift(parseCurrentShift(value));
          setLoaded(true);
          setError(null);
        }
      })
      .catch((cause) => {
        if (!abort.signal.aborted && requestGeneration === generation.current) {
          setError(friendlyError(cause));
          setLoaded(true);
        }
      });
    return () => abort.abort();
  }, [read, identityId, revision, sessionId, refresh]);
  async function submit() {
    if (working || busy) return;
    if (!pending.current) {
      const key = crypto.randomUUID();
      pending.current =
        shift?.status === "active"
          ? {
              path: `/shifts/${encodeURIComponent(shift.id)}/advance`,
              body: {
                command_id: key,
                expected_step: shift.current_step,
                session_id: shift.current_session_id,
              },
              key,
            }
          : { path: "/shifts", body: { difficulty }, key };
    }
    const command = pending.current;
    generation.current += 1;
    const abort = new AbortController();
    operation.current = abort;
    setWorking(true);
    setError(null);
    try {
      const next = parseShift(
        await write(command.path, command.body, command.key, abort.signal),
      );
      if (abort.signal.aborted) return;
      generation.current += 1;
      pending.current = null;
      setShift(next);
      setReviewId(null);
      if (next.current_session_id) await openSession(next.current_session_id);
      else if (next.status === "completed") onShiftComplete?.();
    } catch (cause) {
      if (!abort.signal.aborted) {
        setError(friendlyError(cause));
        if (cause instanceof ApiError && cause.status === 409) {
          pending.current = null;
          setRefresh((n) => n + 1);
        }
      }
    } finally {
      if (!abort.signal.aborted) setWorking(false);
    }
  }
  const linked = shift?.current_session_id === sessionId;
  // A separate training attempt must be finished before opening a shift event.
  const separateActive = !!sessionId && !sessionCompleted && !linked;
  if (separateActive) return null;
  if (!loaded)
    return (
      <p className="shift-loading" role="status">
        Проверяем маршрут смены…
      </p>
    );
  const step = shift?.steps[shift.current_step];
  const nextAllowed = shift?.status === "active" && linked && sessionCompleted;
  return (
    <section
      className={`shift-panel ${sessionId && linked && !sessionCompleted ? "shift-compact" : ""}`}
      aria-label="Рабочая смена"
    >
      <div className="shift-title">
        <div>
          <p className="eyebrow">УЧЕБНЫЙ ЦЕНТР / ВСМ</p>
          <h2>
            {shift?.status === "completed"
              ? "Смена завершена. Разберём результат."
              : shift
                ? "Вы на маршруте"
                : "Готовность начинается с практики"}
          </h2>
        </div>
        <span className="service-stamp">
          {shift
            ? `${shift.metrics.completed_scenarios} / ${shift.metrics.total_scenarios}`
            : "СМЕНА 01"}
          <small>
            {shift?.difficulty === "advanced"
              ? "Повышенная сложность"
              : "Учебный режим"}
          </small>
        </span>
      </div>
      {shift && <ShiftRoute shift={shift} />}
      {!shift && (
        <div className="shift-intro">
          <div>
            <p>
              Пройдите рабочую смену: от первого обращения до ситуации,
              требующей быстрого решения.
            </p>
            <div className="route-preview" aria-label="План смены">
              <span>01 · Сервис</span>
              <i aria-hidden="true" />
              <span>02 · Конфликт</span>
              <i aria-hidden="true" />
              <span>03 · Безопасность</span>
            </div>
          </div>
          <div className="shift-launch">
            <label htmlFor="shift-difficulty">Подготовка</label>
            <select
              id="shift-difficulty"
              value={difficulty}
              onChange={(e) => setDifficulty(e.target.value)}
              disabled={working || !!error}
            >
              <option value="standard">Стандартная смена</option>
              <option value="advanced">Сложная смена</option>
            </select>
            <button
              className="primary-button"
              onClick={() => void submit()}
              disabled={working || busy || !!error}
            >
              {working ? "Открываем смену…" : "Заступить на учебную смену"}
              <span aria-hidden="true">→</span>
            </button>
          </div>
        </div>
      )}
      {shift?.status === "active" && step && (
        <div className="shift-current">
          <div>
            <span className="eyebrow">{step.passenger_profile}</span>
            <p>{step.context}</p>
          </div>
          {(!linked || nextAllowed) && (
            <button
              className="primary-button"
              disabled={working || busy}
              onClick={() =>
                nextAllowed
                  ? void submit()
                  : void openSession(shift.current_session_id!)
              }
            >
              {working
                ? "Обновляем маршрут…"
                : nextAllowed
                  ? shift.current_step === shift.steps.length - 1
                    ? "Завершить смену"
                    : "К следующему событию"
                  : "Продолжить учебную смену"}{" "}
              <span aria-hidden="true">→</span>
            </button>
          )}
        </div>
      )}
      {shift?.status === "completed" && (
        <>
          <dl className="shift-metrics">
            {[
              ["Безопасность", shift.metrics.safety],
              ["Клиентский сервис", shift.metrics.service],
              ["Регламент", shift.metrics.regulation],
              ["Коммуникация", shift.metrics.communication],
              ["Реакция, сек", shift.metrics.average_reaction_seconds],
              ["Учебный опыт, XP", shift.metrics.xp],
            ].map(([name, value]) => (
              <div key={String(name)}>
                <dt>{name}</dt>
                <dd>{value === null ? "—" : displayNumber(Number(value))}</dd>
              </div>
            ))}
          </dl>
          <p className="quiet-note">
            Безопасность и сервис — среднее по ситуациям, компетенции — сумма
            изменений. Критических ошибок: {shift.metrics.critical_errors}.
          </p>
          <div className="shift-summary">
            <div>
              <h3>На следующую смену</h3>
              <ul>
                {shift.recommendations.map((text) => (
                  <li key={text}>{text}</li>
                ))}
              </ul>
            </div>
            <div>
              <h3>Учебные квалификации</h3>
              {shift.achievements.map((a) => (
                <div
                  className={`qualification ${a.unlocked ? "unlocked" : ""}`}
                  key={a.id}
                >
                  <span aria-hidden="true">{a.unlocked ? "✓" : "◇"}</span>
                  <div>
                    <strong>{a.title}</strong>
                    <p>{a.description}</p>
                    <small>
                      {a.unlocked
                        ? "Подтверждена практикой"
                        : "Условие ещё не выполнено"}
                    </small>
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div className="shift-review">
            <h3>Разбор событий смены</h3>
            {shift.steps.map((item) => (
              <button
                className="shift-review-button"
                key={item.index}
                aria-expanded={reviewId === item.session_id}
                onClick={() =>
                  setReviewId(
                    reviewId === item.session_id ? null : item.session_id,
                  )
                }
              >
                {String(item.index + 1).padStart(2, "0")} · {item.title}
                <span>{reviewId === item.session_id ? "−" : "+"}</span>
              </button>
            ))}
            {reviewId && (
              <Debrief
                key={reviewId}
                read={read}
                sessionId={reviewId}
                identityId={identityId}
              />
            )}
          </div>
          <button
            className="text-button"
            disabled={working || busy}
            onClick={() => void submit()}
          >
            Открыть новую смену →
          </button>
        </>
      )}
      {error && (
        <div className="shift-error">
          <strong>Маршрут временно недоступен</strong>
          <p>{error}</p>
          <button
            className="text-button"
            disabled={working}
            onClick={() =>
              pending.current ? void submit() : setRefresh((n) => n + 1)
            }
          >
            Повторить проверку смены →
          </button>
        </div>
      )}
    </section>
  );
}

export function PersonalBriefing({
  read,
  identityId,
  onProfile,
}: {
  read: ReadResource;
  identityId: string;
  onProfile: () => void;
}) {
  const resource = useLearningResource(
    read,
    identityId,
    "/profiles/me/progress",
    parseProgress,
  );
  const p = resource.data;
  if (!p)
    return (
      <div className="personal-briefing">
        <p>
          {resource.error
            ? "Личная сводка пока недоступна."
            : "Получаем личную сводку…"}
        </p>
        {resource.error && (
          <button className="text-button" onClick={resource.retry}>
            Обновить сводку
          </button>
        )}
      </div>
    );
  const streak = p.achievements.find((a) => a.id === "critical-streak");
  return (
    <section className="personal-briefing" aria-label="Личная сводка">
      <div>
        <p className="eyebrow">ВАШ УЧЕБНЫЙ ПРОФИЛЬ</p>
        <h3>{p.display_name}</h3>
        <p>
          {p.organization.depot_name} · {p.organization.brigade_name}
        </p>
        <button className="text-button" onClick={onProfile}>
          Паспорт и аналитика →
        </button>
      </div>
      <dl>
        <div>
          <dt>Завершено ситуаций</dt>
          <dd>{p.completed_sessions}</dd>
        </div>
        <div>
          <dt>Учебный опыт</dt>
          <dd>
            {p.xp}
            <small> XP · уровень {p.level}</small>
          </dd>
        </div>
        <div>
          <dt>Серия критических решений</dt>
          <dd>
            {streak?.current ?? 0}
            <small>{streak ? ` / ${streak.target}` : ""}</small>
          </dd>
        </div>
      </dl>
      <div className="briefing-skills">
        <h3>Навыки и квалификации</h3>
        {p.competencies.length ? (
          p.competencies.map((c) => (
            <p key={c.competency_id}>
              {competencyName(c.competency_id)}
              <strong>{c.value}</strong>
            </p>
          ))
        ) : (
          <p>После первой ситуации здесь появится прогресс навыков.</p>
        )}
        <p className="quiet-note">
          {p.achievements
            .filter((a) => a.unlocked)
            .map((a) => a.name)
            .join(" · ") ||
            "Квалификации подтверждаются решениями в тренировках."}
        </p>
      </div>
    </section>
  );
}
