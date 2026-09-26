import { useEffect, useRef, useState } from "react";
import type { ReadResource } from "../career/contracts";
import { displayDate, useLearningResource } from "../learning/resource";
import { friendlyError } from "../runner/api";
import {
  parseChallenges,
  parseNotification,
  parseNotifications,
} from "./contracts";
import type { ChallengeScenario, Notification, WriteRead } from "./contracts";
import "./retention.css";

const statuses = {
  scheduled: "Скоро старт",
  active: "Идёт сейчас",
  completed: "Выполнено",
  expired: "Срок истёк",
};

function LoadState({
  error,
  retry,
}: {
  error: string | null;
  retry: () => void;
}) {
  return error ? (
    <div className="retention-error" role="alert">
      <p>{error}</p>
      <button className="text-button" onClick={retry}>
        Повторить
      </button>
    </div>
  ) : (
    <p role="status">Загружаем события…</p>
  );
}

function Pagination({
  offset,
  total,
  onChange,
  label,
}: {
  offset: number;
  total: number;
  onChange: (offset: number) => void;
  label: string;
}) {
  if (total <= 20) return null;
  return (
    <nav className="retention-pagination" aria-label={label}>
      <button
        className="text-button"
        disabled={offset === 0}
        onClick={() => onChange(Math.max(0, offset - 20))}
      >
        Назад
      </button>
      <span>
        {offset + 1}–{Math.min(offset + 20, total)} / {total}
      </span>
      <button
        className="text-button"
        disabled={offset + 20 >= total}
        onClick={() => onChange(offset + 20)}
      >
        Далее
      </button>
    </nav>
  );
}

export function RetentionPanel({
  read,
  write,
  identityId,
  onScenario,
}: {
  read: ReadResource;
  write: WriteRead;
  identityId: string;
  onScenario: (scenario: ChallengeScenario) => void;
}) {
  const [challengeOffset, setChallengeOffset] = useState(0);
  const [inboxOffset, setInboxOffset] = useState(0);
  const challenges = useLearningResource(
    read,
    identityId,
    `/challenges?limit=20&offset=${challengeOffset}`,
    parseChallenges,
    true,
  );
  const inbox = useLearningResource(
    read,
    identityId,
    `/notifications?limit=20&offset=${inboxOffset}`,
    parseNotifications,
    true,
  );
  const [pending, setPending] = useState<string | null>(null);
  const [mutationError, setMutationError] = useState<string | null>(null);
  const mutation = useRef<AbortController | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const refreshChallenges = challenges.retry;
  const refreshInbox = inbox.retry;
  useEffect(() => {
    heading.current?.focus();
    return () => mutation.current?.abort();
  }, [identityId]);
  useEffect(() => {
    const refresh = () => {
      if (!mutation.current && document.visibilityState === "visible") {
        refreshChallenges();
        refreshInbox();
      }
    };
    const timer = window.setInterval(refresh, 30_000);
    window.addEventListener("focus", refresh);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", refresh);
    };
  }, [refreshChallenges, refreshInbox]);

  async function toggle(item: Notification) {
    if (mutation.current) return;
    const controller = new AbortController();
    mutation.current = controller;
    setPending(item.id);
    setMutationError(null);
    try {
      const result = parseNotification(
        await write(item.id, item.read_at === null, controller.signal),
      );
      if (result.id !== item.id)
        throw new Error("Ответ относится к другому уведомлению.");
      if (!controller.signal.aborted) {
        inbox.updateData((previous) => {
          const old = previous.items.find((row) => row.id === result.id);
          const wasUnread = old && !old.read_at && !old.expired ? 1 : 0;
          const isUnread = !result.read_at && !result.expired ? 1 : 0;
          return {
            ...previous,
            items: previous.items.map((row) =>
              row.id === result.id ? result : row,
            ),
            unread_count: Math.max(
              0,
              previous.unread_count - wasUnread + isUnread,
            ),
          };
        });
        refreshInbox();
      }
    } catch (error) {
      if (!controller.signal.aborted) setMutationError(friendlyError(error));
    } finally {
      if (!controller.signal.aborted) setPending(null);
      if (mutation.current === controller) mutation.current = null;
    }
  }

  return (
    <section className="retention-panel" aria-label="События учебной смены">
      <header className="retention-heading">
        <div>
          <span className="micro-label">СЛЕДУЮЩАЯ ОСТАНОВКА / ПРАКТИКА</span>
          <h1 ref={heading} tabIndex={-1}>
            Продолжить маршрут
          </h1>
        </div>
        <p>
          Новые ситуации. Небольшие цели.
          <br />
          Навыки, которые остаются с вами.
        </p>
      </header>
      <div className="retention-columns">
        <section aria-labelledby="challenges-title" className="challenge-board">
          <div className="retention-section-title">
            <h2 id="challenges-title">Челленджи</h2>
            <button className="text-button" onClick={refreshChallenges}>
              Обновить задания
            </button>
          </div>
          <p className="retention-caption">
            Участие автоматическое. Завершите разные ситуации в срок: без
            таймаутов, с безопасностью ≥ 50. Бонуса за скорость нет.
          </p>
          {challenges.data && challenges.error && (
            <LoadState error={challenges.error} retry={refreshChallenges} />
          )}
          {!challenges.data ? (
            <LoadState error={challenges.error} retry={refreshChallenges} />
          ) : (
            <>
              {challenges.data.items.length === 0 && (
                <p>
                  Сейчас заданий нет. Новые появятся здесь после публикации.
                </p>
              )}
              <ol className="challenge-list">
                {challenges.data.items.map((item, index) => (
                  <li
                    key={item.id}
                    className={`challenge-entry challenge-${item.status}`}
                  >
                    <span className="challenge-number" aria-hidden="true">
                      {String(challengeOffset + index + 1).padStart(2, "0")}
                    </span>
                    <div>
                      <span className="challenge-status">
                        {statuses[item.status]}
                      </span>
                      <h3>{item.title}</h3>
                      <p>{item.description}</p>
                      <p className="challenge-deadline">
                        {item.status === "scheduled" ? "Начало" : "До"}{" "}
                        <time
                          dateTime={
                            item.status === "scheduled"
                              ? item.starts_at
                              : item.expires_at
                          }
                        >
                          {displayDate(
                            item.status === "scheduled"
                              ? item.starts_at
                              : item.expires_at,
                          )}
                        </time>
                      </p>
                      <div className="challenge-progress">
                        <span>
                          {item.progress} / {item.target} ситуаций
                        </span>
                        <div
                          role="progressbar"
                          aria-label={`Прогресс: ${item.title}`}
                          aria-valuenow={item.progress}
                          aria-valuemin={0}
                          aria-valuemax={item.target}
                        >
                          <span
                            style={{
                              width: `${(item.progress / item.target) * 100}%`,
                            }}
                          />
                        </div>
                      </div>
                      <ul className="challenge-stops">
                        {item.scenarios.map((scenario) => (
                          <li key={scenario.id}>
                            <span aria-hidden="true">
                              {scenario.completed ? "✓" : "○"}
                            </span>
                            <span>
                              {scenario.title.replace(
                                "Синтетическое демо: ",
                                "",
                              )}
                              {scenario.completed && <small>Засчитано</small>}
                            </span>
                            <button
                              className="text-button"
                              aria-label={`Открыть: ${scenario.title.replace("Синтетическое демо: ", "")}`}
                              onClick={() => onScenario(scenario)}
                            >
                              Открыть <span aria-hidden="true">↗</span>
                            </button>
                          </li>
                        ))}
                      </ul>
                      {item.status === "expired" && (
                        <p className="retention-caption">
                          Можно продолжить практику. Результат уже не войдёт в
                          это задание.
                        </p>
                      )}
                      {item.status === "scheduled" && (
                        <p className="retention-caption">
                          Для зачёта начните новую попытку после старта задания.
                        </p>
                      )}
                    </div>
                  </li>
                ))}
              </ol>
              <Pagination
                offset={challengeOffset}
                total={challenges.data.total}
                onChange={setChallengeOffset}
                label="Страницы челленджей"
              />
              <p className="retention-caption">
                По данным сервера: {displayDate(challenges.data.server_time)}.
                Сроки показаны в вашем часовом поясе.
              </p>
            </>
          )}
        </section>
        <section aria-labelledby="inbox-title" className="retention-inbox">
          <div className="retention-section-title">
            <h2 id="inbox-title">Входящие</h2>
            <button
              className="text-button"
              disabled={pending !== null}
              onClick={refreshInbox}
            >
              Обновить входящие
            </button>
          </div>
          {mutationError && (
            <p role="alert">{mutationError} Повторите действие.</p>
          )}
          {inbox.data && inbox.error && (
            <LoadState error={inbox.error} retry={refreshInbox} />
          )}
          {!inbox.data ? (
            <LoadState error={inbox.error} retry={refreshInbox} />
          ) : (
            <>
              <p className="retention-caption" aria-live="polite">
                Непрочитанных: {inbox.data.unread_count}
              </p>
              {inbox.data.items.length === 0 && (
                <p>
                  Пока нет уведомлений. Здесь появятся новые сценарии, задания и
                  достижения.
                </p>
              )}
              <ol className="notification-list">
                {inbox.data.items.map((item) => (
                  <li
                    key={item.id}
                    className={
                      !item.read_at && !item.expired
                        ? "notification-unread"
                        : ""
                    }
                  >
                    <span className="micro-label">
                      {item.expired
                        ? "СРОК ИСТЁК"
                        : item.read_at
                          ? "ПРОЧИТАНО"
                          : "НЕ ПРОЧИТАНО"}
                    </span>
                    <h3>{item.title}</h3>
                    <p>{item.body}</p>
                    <p className="notification-date">
                      {displayDate(item.created_at)}
                      <br />
                      Актуально до {displayDate(item.expires_at)}
                    </p>
                    <button
                      className="text-button"
                      disabled={pending !== null}
                      onClick={() => void toggle(item)}
                    >
                      {pending === item.id
                        ? "Сохраняем…"
                        : item.read_at
                          ? "Отметить непрочитанным"
                          : "Отметить прочитанным"}
                    </button>
                  </li>
                ))}
              </ol>
              <Pagination
                offset={inboxOffset}
                total={inbox.data.total}
                onChange={setInboxOffset}
                label="Страницы уведомлений"
              />
            </>
          )}
        </section>
      </div>
    </section>
  );
}
