import { useEffect, useRef, useState } from "react";
import { friendlyError } from "../runner/api";
import { CompetencyAnalytics } from "../learning/CompetencyAnalytics";
import {
  competencyName,
  parseBoard,
  parsePersonas,
  parseProgress,
  type ReadResource,
  type Scope,
} from "./contracts";
import "./career.css";

function useResource<T>(
  read: ReadResource,
  path: string,
  parse: (data: unknown) => T,
) {
  const [attempt, setAttempt] = useState(0);
  const key = `${path}:${attempt}`;
  const [response, setResponse] = useState<{
    key: string;
    data: T | null;
    error: string | null;
  } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void read(path, controller.signal)
      .then((value) => {
        const data = parse(value);
        if (!controller.signal.aborted) setResponse({ key, data, error: null });
      })
      .catch((error) => {
        if (!controller.signal.aborted)
          setResponse({ key, data: null, error: friendlyError(error) });
      });
    return () => controller.abort();
  }, [read, path, parse, key]);
  const current = response?.key === key ? response : null;
  return {
    data: current?.data ?? null,
    error: current?.error ?? null,
    retry: () => setAttempt((n) => n + 1),
  };
}

function LoadState({
  error,
  retry,
}: {
  error: string | null;
  retry: () => void;
}) {
  return error ? (
    <div className="career-error" role="alert">
      <p>{error}</p>
      <button className="text-button" onClick={retry}>
        Повторить загрузку <span aria-hidden="true">↗</span>
      </button>
    </div>
  ) : (
    <p className="career-loading" role="status">
      Загружаем учебный прогресс…
    </p>
  );
}

export function CompletionReward({
  read,
  sessionId,
  onProfile,
}: {
  read: ReadResource;
  sessionId: string;
  onProfile: () => void;
}) {
  const resource = useResource(
    read,
    `/profiles/me/progress?session_id=${encodeURIComponent(sessionId)}`,
    parseProgress,
  );
  if (!resource.data)
    return <LoadState error={resource.error} retry={resource.retry} />;
  const { reward, achievements, level } = resource.data;
  if (!reward || reward.session_id !== sessionId)
    return <p className="quiet-note">Награда этой попытки пока недоступна.</p>;
  const names = achievements
    .filter((a) => reward.unlocks.includes(a.id))
    .map((a) => a.name);
  return (
    <section
      className="earned-reward"
      aria-label="Награда за ситуацию"
      role="status"
    >
      <div className="earned-number">+{reward.xp} XP</div>
      <div className="earned-detail">
        <strong>Уровень {level} · прогресс сохранён</strong>
        <p>
          {reward.competencies.length
            ? reward.competencies
                .map((c) => `${competencyName(c.competency_id)} +${c.value}`)
                .join(" · ")
            : "Эта попытка не добавила очков компетенций."}
        </p>
        {names.length > 0 && (
          <p className="new-unlock">Открыто: {names.join(" · ")}</p>
        )}
      </div>
      <button className="text-button" onClick={onProfile}>
        Мой прогресс <span aria-hidden="true">↗</span>
      </button>
    </section>
  );
}

interface Props {
  mode: "profile" | "leaderboard";
  read: ReadResource;
  identityId: string;
  busy: boolean;
  switchPersona: (id: string) => Promise<void>;
  onPlay: () => void;
}

export function CareerPanel(props: Props) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    heading.current?.focus();
  }, [props.mode]);
  return (
    <section className="career-panel">
      <div className="section-heading">
        <div>
          <p className="eyebrow">ВСМ / ПУТЬ ПРОВОДНИКА</p>
          <h1 ref={heading} tabIndex={-1}>
            {props.mode === "profile"
              ? "Учебный паспорт"
              : "Рейтинг проводников"}
          </h1>
        </div>
        <span className="section-number" aria-hidden="true">
          {props.mode === "profile" ? "ID" : "↗"}
        </span>
      </div>
      {props.mode === "profile" ? (
        <Profile {...props} />
      ) : (
        <Leaderboard read={props.read} />
      )}
      <button className="primary-button return-shift" onClick={props.onPlay}>
        Продолжить смену <span aria-hidden="true">→</span>
      </button>
    </section>
  );
}

function Profile({ read, identityId, busy, switchPersona }: Props) {
  const [analyticsOpen, setAnalyticsOpen] = useState(false);
  const resource = useResource(read, "/profiles/me/progress", parseProgress);
  const personas = useResource(read, "/auth/demo/personas", parsePersonas);
  if (!resource.data)
    return <LoadState error={resource.error} retry={resource.retry} />;
  const p = resource.data;
  const ratio =
    ((p.xp - p.level_start_xp) / (p.next_level_xp - p.level_start_xp)) * 100;
  return (
    <>
      <div className="conductor-pass">
        <div className="pass-identity">
          <span className="eyebrow">ПРОВОДНИК / УЧЕБНЫЙ ПРОФИЛЬ</span>
          <h2>{p.display_name}</h2>
          <p>{p.organization.company_name}</p>
          <p>
            {p.organization.depot_name}
            <span aria-hidden="true"> / </span>
            {p.organization.brigade_name}
          </p>
          <span className="pass-count">
            Завершённых ситуаций: {p.completed_sessions}
          </span>
        </div>
        <div className="pass-level">
          <div className="level-heading">
            <span className="micro-label">УРОВЕНЬ</span>
            <strong>{String(p.level).padStart(2, "0")}</strong>
            <span className="xp-total">{p.xp} XP</span>
          </div>
          <div
            className="level-track"
            role="progressbar"
            aria-label="Прогресс уровня"
            aria-valuemin={p.level_start_xp}
            aria-valuemax={p.next_level_xp}
            aria-valuenow={p.xp}
          >
            <span style={{ width: `${ratio}%` }} />
          </div>
          <p>
            До уровня {p.level + 1} — {p.next_level_xp - p.xp} XP
          </p>
        </div>
      </div>
      <div className="analytics-toggle-row">
        <button
          className="text-button"
          aria-expanded={analyticsOpen}
          aria-controls="competency-analytics"
          onClick={() => setAnalyticsOpen((open) => !open)}
        >
          Аналитика компетенций{" "}
          <span aria-hidden="true">{analyticsOpen ? "−" : "↗"}</span>
        </button>
        <p>Сильные стороны, динамика и следующий шаг</p>
      </div>
      {analyticsOpen && (
        <CompetencyAnalytics read={read} identityId={identityId} />
      )}
      <section
        className="competency-section"
        aria-labelledby="competencies-title"
      >
        <div className="action-heading">
          <h2 id="competencies-title">Профессиональные навыки</h2>
          <span>Очки из завершённых ситуаций</span>
        </div>
        {p.competencies.length === 0 ? (
          <p className="career-empty">
            Пройдите ситуацию, чтобы начать развитие компетенций.
          </p>
        ) : (
          <dl className="competency-register">
            {p.competencies.map((c) => (
              <div key={c.competency_id}>
                <dt>{competencyName(c.competency_id)}</dt>
                <dd>
                  {c.value}
                  <span>очк.</span>
                </dd>
              </div>
            ))}
          </dl>
        )}
      </section>
      <section
        className="achievement-section"
        aria-labelledby="achievements-title"
      >
        <div className="action-heading">
          <h2 id="achievements-title">Знаки мастерства</h2>
          <span>
            {p.achievements.filter((a) => a.unlocked).length} /{" "}
            {p.achievements.length} открыто
          </span>
        </div>
        <ol className="achievement-register">
          {p.achievements.map((a, index) => (
            <li key={a.id} className={a.unlocked ? "unlocked" : "locked"}>
              <span className="achievement-emblem" aria-hidden="true">
                {a.unlocked ? "✓" : String(index + 1).padStart(2, "0")}
              </span>
              <div className="achievement-description">
                <h3>{a.name}</h3>
                <p>{a.description}</p>
                {a.unlocked_at && (
                  <span className="unlock-date">
                    Открыто{" "}
                    {new Date(a.unlocked_at).toLocaleDateString("ru-RU")}
                  </span>
                )}
              </div>
              <div className="achievement-progress">
                <strong>
                  {a.unlocked ? "Открыто" : `${a.current} / ${a.target}`}
                </strong>
                <div
                  role="progressbar"
                  aria-label={a.name}
                  aria-valuemin={0}
                  aria-valuemax={a.target}
                  aria-valuenow={a.current}
                >
                  <span style={{ width: `${(a.current / a.target) * 100}%` }} />
                </div>
              </div>
            </li>
          ))}
        </ol>
      </section>
      <div className="demo-identity">
        <div>
          <h2>Учебный проводник</h2>
          <p>
            Синтетические профили для демонстрации. У каждого — собственные
            результаты и прогресс.
          </p>
        </div>
        {personas.data ? (
          <label>
            Переключить профиль
            <select
              aria-label="Учебный проводник"
              value={identityId}
              disabled={busy}
              onChange={(event) => void switchPersona(event.target.value)}
            >
              {personas.data.map((persona) => (
                <option value={persona.id} key={persona.id}>
                  {persona.display_name} ·{" "}
                  {persona.depot_id === "north" ? "Север" : "Юг"} /{" "}
                  {persona.brigade_id}
                  {persona.company_id === "demo-other" ? " · компания 02" : ""}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <LoadState error={personas.error} retry={personas.retry} />
        )}
      </div>
    </>
  );
}

function Leaderboard({ read }: { read: ReadResource }) {
  const [scope, setScope] = useState<Scope>("brigade");
  const [offset, setOffset] = useState(0);
  const resource = useResource(
    read,
    `/leaderboard/organization?scope=${scope}&limit=20&offset=${offset}`,
    parseBoard,
  );
  const board = resource.data;
  return (
    <>
      <div className="ranking-intro">
        <p>Результаты коллег по учебной смене.</p>
        <span>
          Место определяется накопленным XP. При равных очках место одинаковое.
        </span>
      </div>
      <div
        className="scope-switch"
        role="group"
        aria-label="Подразделение рейтинга"
      >
        {(
          [
            ["brigade", "Бригада"],
            ["depot", "Депо"],
            ["company", "Компания"],
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            aria-pressed={scope === value}
            onClick={() => {
              setScope(value);
              setOffset(0);
            }}
          >
            {label}
          </button>
        ))}
      </div>
      {!board ? (
        <LoadState error={resource.error} retry={resource.retry} />
      ) : (
        <>
          <div className="ranking-caption">
            <strong>{board.group_name}</strong>
            <span>Участников: {board.total}</span>
          </div>
          {board.items.length === 0 ? (
            <p className="career-empty">
              {board.assigned
                ? "Пока нет завершённых ситуаций. Пройдите первую — ваш результат появится на табло."
                : "Подразделение пока не назначено. Рейтинг станет доступен после назначения."}
            </p>
          ) : (
            <div className="ranking-table-wrap">
              <table className="ranking-table">
                <caption className="visually-hidden">
                  Рейтинг: {board.group_name}
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Место</th>
                    <th scope="col">Проводник</th>
                    <th scope="col">Уровень</th>
                    <th scope="col">Ситуации</th>
                    <th scope="col">XP</th>
                  </tr>
                </thead>
                <tbody>
                  {board.items.map((row) => (
                    <tr
                      key={row.employee_id}
                      className={row.is_me ? "your-rank" : ""}
                    >
                      <td className="rank-number">
                        {String(row.rank).padStart(2, "0")}
                      </td>
                      <th scope="row">
                        {row.display_name}
                        {row.is_me && <span className="you-label">Вы</span>}
                      </th>
                      <td>{row.level}</td>
                      <td>{row.completed_sessions}</td>
                      <td className="ranking-xp">{row.xp}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="ranking-pagination">
            <button
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - 20))}
            >
              ← Назад
            </button>
            <span>
              {board.total === 0
                ? "0"
                : `${offset + 1}–${Math.min(offset + 20, board.total)}`}{" "}
              / {board.total}
            </span>
            <button
              disabled={offset + 20 >= board.total}
              onClick={() => setOffset(offset + 20)}
            >
              Далее →
            </button>
          </div>
        </>
      )}
    </>
  );
}
