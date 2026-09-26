import { useCallback, useState } from "react";
import { competencyName, type ReadResource } from "../career/contracts";
import { ContractError } from "../runner/api";
import {
  parseDebrief,
  type DebriefDecision,
  type ScoreReason,
} from "./contracts";
import {
  displayDate,
  displayNumber,
  LearningLoadState,
  signed,
  useLearningResource,
} from "./resource";
import "./learning.css";

function ScoreExplanation({
  name,
  score,
}: {
  name: string;
  score: ScoreReason;
}) {
  return (
    <div className="decision-score">
      <dt>{name}</dt>
      <dd>
        <div className="decision-score-values">
          <span>
            {score.before} <span aria-hidden="true">→</span>
            <span className="visually-hidden">до</span> {score.after}
          </span>
          <strong className={score.delta < 0 ? "learning-negative" : ""}>
            {signed(score.delta)}
          </strong>
        </div>
        <p>{score.explanation}</p>
        {score.delta === 0 && (
          <span className="learning-note">Без изменения показателя</span>
        )}
        {score.requested_delta !== score.delta && (
          <span className="learning-note">
            Эффект выбора: {signed(score.requested_delta)} · фактически:{" "}
            {signed(score.delta)}
          </span>
        )}
      </dd>
    </div>
  );
}

function DecisionEntry({
  decision,
  open,
  onToggle,
}: {
  decision: DebriefDecision;
  open: boolean;
  onToggle: (open: boolean) => void;
}) {
  return (
    <li className="debrief-entry">
      <span className="debrief-index" aria-hidden="true">
        {String(decision.sequence).padStart(2, "0")}
      </span>
      <details
        className={`debrief-event assessment-${decision.assessment?.status ?? "neutral"}`}
        open={open}
        onToggle={(event) => onToggle(event.currentTarget.open)}
      >
        <summary className="debrief-event-summary">
          <div className="event-summary-top">
            <span className="micro-label">
              СОБЫТИЕ {String(decision.sequence).padStart(2, "0")}
            </span>
            <span className="assessment-label">
              {decision.assessment?.title ?? "Запись решения"}
            </span>
          </div>
          <h3 id={`debrief-decision-${decision.sequence}`}>
            {decision.choice_text}
          </h3>
          <div className="event-summary-bottom">
            <span>{displayNumber(decision.elapsed_seconds)} сек. в сцене</span>
            <span>
              Сервис {signed(decision.loyalty.delta)} · Безопасность{" "}
              {signed(decision.safety.delta)}
            </span>
            <span className="event-disclosure-label">
              {open ? "Свернуть" : "Разобрать"}{" "}
              <span aria-hidden="true">{open ? "−" : "+"}</span>
            </span>
          </div>
        </summary>
        <article aria-labelledby={`debrief-decision-${decision.sequence}`}>
          <div className="decision-meta">
            <span>
              {decision.was_timeout
                ? "Переход по истечении времени"
                : "Решение принято"}
            </span>
            <time dateTime={decision.decided_at}>
              {displayDate(decision.decided_at)}
            </time>
          </div>
          <span className="micro-label">ЧТО ПРОИЗОШЛО</span>
          <p className="decision-context">{decision.node_text}</p>
          {decision.assessment && (
            <div className="decision-assessment">
              <span className="micro-label">ПОЧЕМУ ЭТО ВАЖНО</span>
              <p>{decision.assessment.explanation}</p>
              {decision.assessment.is_critical && (
                <span className="learning-note">
                  Событие с ограничением времени
                </span>
              )}
            </div>
          )}
          <div className="decision-consequence">
            <span className="micro-label">ПОСЛЕДСТВИЕ</span>
            <p>{decision.explanation}</p>
            <p className="decision-destination">{decision.destination_text}</p>
          </div>
          <dl className="decision-scores" aria-label="Изменения показателей">
            <ScoreExplanation
              name="Клиентский сервис"
              score={decision.loyalty}
            />
            <ScoreExplanation name="Безопасность" score={decision.safety} />
          </dl>
          <div className="decision-competencies">
            <h4>Компетенции в решении</h4>
            {decision.competencies.length === 0 ? (
              <p className="learning-note">
                Решение не изменило очки компетенций.
              </p>
            ) : (
              <ul>
                {decision.competencies.map((competency) => (
                  <li key={competency.competency_id}>
                    <div>
                      <strong>
                        {competencyName(competency.competency_id)}
                      </strong>
                      <span
                        className={
                          competency.delta < 0 ? "learning-negative" : ""
                        }
                      >
                        {signed(competency.delta)}{" "}
                        <span className="learning-note">
                          ({competency.before} → {competency.after})
                        </span>
                      </span>
                    </div>
                    <p>{competency.explanation}</p>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <details className="decision-alternatives">
            <summary>Альтернативы решения {decision.sequence}</summary>
            <p className="learning-note">
              Сравнение на момент выбора. Показаны непосредственные эффекты;
              дальнейший исход зависит от следующих решений.
            </p>
            {decision.alternatives.length === 0 ? (
              <p className="learning-note">
                Других пользовательских вариантов в этой сцене нет.
              </p>
            ) : (
              <ul>
                {decision.alternatives.map((alternative) => (
                  <li
                    key={alternative.choice_id}
                    className={
                      alternative.available ? "" : "alternative-unavailable"
                    }
                  >
                    <div className="alternative-heading">
                      <h4>{alternative.text}</h4>
                      <span className="alternative-availability">
                        {!alternative.available
                          ? "Недоступно в момент решения"
                          : decision.suggestion.choice_ids.includes(
                                alternative.choice_id,
                              )
                            ? "Можно попробовать"
                            : "Было доступно"}
                      </span>
                    </div>
                    <p>{alternative.explanation}</p>
                    <dl className="alternative-effects">
                      <div>
                        <dt>Сервис</dt>
                        <dd>{signed(alternative.loyalty_delta)}</dd>
                      </div>
                      <div>
                        <dt>Безопасность</dt>
                        <dd>{signed(alternative.safety_delta)}</dd>
                      </div>
                      {alternative.competencies.map((competency) => (
                        <div key={competency.competency_id}>
                          <dt>{competencyName(competency.competency_id)}</dt>
                          <dd>{signed(competency.delta)}</dd>
                        </div>
                      ))}
                    </dl>
                  </li>
                ))}
              </ul>
            )}
          </details>
          <div className="decision-advice">
            <span className="micro-label">СЛЕДУЮЩИЙ УЧЕБНЫЙ ШАГ</span>
            <p>{decision.suggestion.text}</p>
          </div>
        </article>
      </details>
    </li>
  );
}

export function Debrief({
  read,
  sessionId,
  identityId,
}: {
  read: ReadResource;
  sessionId: string;
  identityId: string;
}) {
  const [filter, setFilter] = useState<"all" | "attention" | "strong">("all");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const parse = useCallback(
    (value: unknown) => {
      const data = parseDebrief(value);
      if (data.session_id !== sessionId) throw new ContractError();
      return data;
    },
    [sessionId],
  );
  const resource = useLearningResource(
    read,
    identityId,
    `/sessions/${encodeURIComponent(sessionId)}/debrief`,
    parse,
  );
  const data = resource.data;
  return (
    <section
      className="debrief-section"
      aria-labelledby="debrief-title"
      data-testid="decision-history"
    >
      <div className="action-heading">
        <h2 id="debrief-title">Разбор решений</h2>
        <span>Выбор → последствие → следующий шаг</span>
      </div>
      {!data ? (
        <LearningLoadState
          kind="debrief"
          error={resource.error}
          retry={resource.retry}
        />
      ) : (
        <>
          <div className="debrief-summary">
            <span>
              Решений: <strong>{data.summary.decision_count}</strong>
            </span>
            <span>
              Таймаутов: <strong>{data.summary.timeout_count}</strong>
            </span>
            <span>
              Клиентский сервис:{" "}
              <strong>{signed(data.summary.loyalty_delta)}</strong>
            </span>
            <span>
              Безопасность: <strong>{signed(data.summary.safety_delta)}</strong>
            </span>
          </div>
          <div className="debrief-controls">
            <div
              className="learning-filter"
              role="group"
              aria-label="Фильтр разбора"
            >
              {(
                [
                  ["all", "Все события"],
                  ["attention", "Требует внимания"],
                  ["strong", "Сильные решения"],
                ] as const
              ).map(([value, label]) => (
                <button
                  key={value}
                  aria-pressed={filter === value}
                  onClick={() => setFilter(value)}
                >
                  {label}
                </button>
              ))}
            </div>
            <button
              className="text-button"
              onClick={() =>
                setExpanded(
                  Object.fromEntries(
                    data.decisions.map((decision) => [
                      decision.decision_id,
                      true,
                    ]),
                  ),
                )
              }
            >
              Раскрыть все события
            </button>
          </div>
          <p className="learning-note debrief-method">
            Сервис — прежняя шкала доверия пассажиров. Время в сцене включает
            доставку запроса; таймаут не измеряет скорость реакции. XP и допуски
            показаны отдельно от профессиональных показателей.
          </p>
          {data.decisions.length === 0 ? (
            <p className="learning-empty">
              В этой попытке нет решений для разбора.
            </p>
          ) : (
            <ol className="debrief-timeline">
              {data.decisions
                .filter(
                  (decision) =>
                    filter === "all" ||
                    (filter === "attention"
                      ? ["critical_error", "attention"].includes(
                          decision.assessment?.status ?? "",
                        )
                      : decision.assessment?.status === "strong"),
                )
                .map((decision) => (
                  <DecisionEntry
                    key={decision.decision_id}
                    decision={decision}
                    open={
                      expanded[decision.decision_id] ?? decision.sequence === 1
                    }
                    onToggle={(open) =>
                      setExpanded((previous) =>
                        previous[decision.decision_id] === open
                          ? previous
                          : { ...previous, [decision.decision_id]: open },
                      )
                    }
                  />
                ))}
            </ol>
          )}
          {filter !== "all" &&
            !data.decisions.some((decision) =>
              filter === "attention"
                ? ["critical_error", "attention"].includes(
                    decision.assessment?.status ?? "",
                  )
                : decision.assessment?.status === "strong",
            ) && (
              <p className="learning-empty">
                В этой попытке нет событий для выбранного фильтра.
              </p>
            )}
        </>
      )}
    </section>
  );
}
