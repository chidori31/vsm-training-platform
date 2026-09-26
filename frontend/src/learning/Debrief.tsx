import { useCallback } from "react";
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

function DecisionEntry({ decision }: { decision: DebriefDecision }) {
  return (
    <li className="debrief-entry">
      <span className="debrief-index" aria-hidden="true">
        {String(decision.sequence).padStart(2, "0")}
      </span>
      <article aria-labelledby={`debrief-decision-${decision.sequence}`}>
        <div className="decision-meta">
          <span>
            {decision.was_timeout
              ? "Переход по истечении времени"
              : "Решение принято"}
          </span>
          <span>{displayNumber(decision.elapsed_seconds)} сек. в сцене</span>
          <time dateTime={decision.decided_at}>
            {displayDate(decision.decided_at)}
          </time>
        </div>
        <p className="decision-context">{decision.node_text}</p>
        <h3 id={`debrief-decision-${decision.sequence}`}>
          {decision.choice_text}
        </h3>
        <div className="decision-consequence">
          <span className="micro-label">ПОСЛЕДСТВИЕ</span>
          <p>{decision.explanation}</p>
          <p className="decision-destination">{decision.destination_text}</p>
        </div>
        <dl className="decision-scores" aria-label="Изменения показателей">
          <ScoreExplanation
            name="Доверие пассажиров"
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
                    <strong>{competencyName(competency.competency_id)}</strong>
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
                      <dt>Доверие</dt>
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
              Доверие: <strong>{signed(data.summary.loyalty_delta)}</strong>
            </span>
            <span>
              Безопасность: <strong>{signed(data.summary.safety_delta)}</strong>
            </span>
          </div>
          {data.decisions.length === 0 ? (
            <p className="learning-empty">
              В этой попытке нет решений для разбора.
            </p>
          ) : (
            <ol className="debrief-timeline">
              {data.decisions.map((decision) => (
                <DecisionEntry key={decision.decision_id} decision={decision} />
              ))}
            </ol>
          )}
        </>
      )}
    </section>
  );
}
