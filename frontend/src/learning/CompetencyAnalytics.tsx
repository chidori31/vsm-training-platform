import { useEffect, useRef } from "react";
import { competencyName, type ReadResource } from "../career/contracts";
import {
  parseAnalytics,
  type AnalyticsData,
  type CompetencyAnalysis,
  type CompetencyStatus,
} from "./contracts";
import {
  displayDate,
  displayNumber,
  LearningLoadState,
  signed,
  useLearningResource,
} from "./resource";
import "./learning.css";

const statusLabels: Record<CompetencyStatus, string> = {
  insufficient_data: "Недостаточно данных",
  strength: "Сильная сторона",
  growth_area: "Зона развития",
  developing: "Формируется",
};

function CompetencyRow({ competency }: { competency: CompetencyAnalysis }) {
  return (
    <li
      className="skill-analysis"
      data-testid={`competency-analysis-${competency.competency_id}`}
    >
      <div className="skill-analysis-heading">
        <h3>{competencyName(competency.competency_id)}</h3>
        <span className={`skill-status status-${competency.status}`}>
          {statusLabels[competency.status]}
        </span>
      </div>
      <div className="skill-evidence">
        <span>
          Наблюдений: <strong>{competency.opportunities}</strong>
        </span>
        <span>
          Попыток с наблюдениями:{" "}
          <strong>{competency.practiced_sessions}</strong>
        </span>
        <span>
          Положительных решений:{" "}
          <strong>{competency.positive_decisions}</strong>
        </span>
        <span>
          Отрицательных: <strong>{competency.negative_decisions}</strong>
        </span>
      </div>
      {competency.status === "insufficient_data" && (
        <p className="insufficient-explanation">
          Пока рано делать вывод: нужны как минимум 3 наблюдения в 2 завершённых
          попытках.
        </p>
      )}
      <div className="skill-points">
        <span>
          Накоплено <strong>{competency.earned_points}</strong> очк.
        </span>
        <span>
          Суммарное изменение{" "}
          <strong
            className={competency.net_delta < 0 ? "learning-negative" : ""}
          >
            {signed(competency.net_delta)}
          </strong>
        </span>
      </div>
      <details className="skill-trend">
        <summary>Динамика по попыткам</summary>
        {competency.trend.length === 0 ? (
          <p className="learning-note">
            Завершённых попыток с этой компетенцией пока нет.
          </p>
        ) : (
          <div
            className="learning-table-wrap"
            role="region"
            aria-label={`Динамика: ${competencyName(competency.competency_id)}`}
            tabIndex={0}
          >
            <table className="learning-table trend-table">
              <caption className="visually-hidden">
                Динамика: {competencyName(competency.competency_id)}
              </caption>
              <thead>
                <tr>
                  <th scope="col">Завершение попытки</th>
                  <th scope="col">Изменение</th>
                  <th scope="col">Начислено</th>
                  <th scope="col">Накоплено</th>
                </tr>
              </thead>
              <tbody>
                {competency.trend.map((point) => (
                  <tr key={point.session_id}>
                    <th scope="row">
                      <time dateTime={point.completed_at}>
                        {displayDate(point.completed_at)}
                      </time>
                    </th>
                    <td className={point.delta < 0 ? "learning-negative" : ""}>
                      {signed(point.delta)}
                    </td>
                    <td>{point.earned_points}</td>
                    <td>{point.cumulative_points}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </details>
    </li>
  );
}

function Patterns({ patterns }: { patterns: AnalyticsData["patterns"] }) {
  return (
    <section
      className="learning-patterns"
      data-testid="learning-patterns"
      aria-labelledby="patterns-title"
    >
      <div className="action-heading">
        <h3 id="patterns-title">Повторяющиеся паттерны</h3>
        <span>Что стоит потренировать</span>
      </div>
      {!patterns.some((pattern) => pattern.recurring) && (
        <p className="learning-note">Повторяющихся паттернов пока нет.</p>
      )}
      <ul className="pattern-list">
        {patterns.map((pattern) => (
          <li key={pattern.code}>
            <div className="pattern-heading">
              <h4>{pattern.title}</h4>
              <span>
                {pattern.recurring ? "Повторяется" : "Единичное наблюдение"}
              </span>
            </div>
            <p>{pattern.description}</p>
            <span className="pattern-count">
              Решений: {pattern.count} · попыток: {pattern.session_count}
            </span>
            <p className="pattern-advice">{pattern.advice}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}

function ScenarioStatistics({
  scenarios,
}: {
  scenarios: AnalyticsData["scenarios"];
}) {
  const average = (value: number | null) =>
    value === null ? "Нет завершений" : displayNumber(value);
  return (
    <section
      className="scenario-statistics"
      aria-labelledby="scenario-statistics-title"
    >
      <div className="action-heading">
        <h3 id="scenario-statistics-title">Практика по сценариям</h3>
        <span>Каждая версия отдельно</span>
      </div>
      {scenarios.length === 0 ? (
        <p className="learning-note">
          Начните ситуацию — здесь появится история практики.
        </p>
      ) : (
        <>
          <p className="learning-note">
            Средние длительность и показатели учитывают только завершённые
            попытки.
          </p>
          <div
            className="learning-table-wrap"
            tabIndex={0}
            role="region"
            aria-label="Статистика сценариев: прокручиваемая таблица"
          >
            <table className="learning-table scenario-statistics-table">
              <caption className="visually-hidden">
                Статистика сценариев
              </caption>
              <thead>
                <tr>
                  <th scope="col">Сценарий</th>
                  <th scope="col">Попытки</th>
                  <th scope="col">Завершено</th>
                  <th scope="col">В работе</th>
                  <th scope="col">Таймауты</th>
                  <th scope="col">Среднее время, сек.</th>
                  <th scope="col">Среднее доверие</th>
                  <th scope="col">Средняя безопасность</th>
                </tr>
              </thead>
              <tbody>
                {scenarios.map((scenario) => (
                  <tr
                    key={`${scenario.scenario_id}:${scenario.scenario_version}`}
                  >
                    <th scope="row">
                      {scenario.title}
                      <span className="scenario-version">
                        Версия {scenario.scenario_version}
                      </span>
                    </th>
                    <td>{scenario.attempts}</td>
                    <td>{scenario.completed}</td>
                    <td>{scenario.active}</td>
                    <td>{scenario.timeout_count}</td>
                    <td>{average(scenario.average_duration_seconds)}</td>
                    <td>{average(scenario.average_loyalty)}</td>
                    <td>{average(scenario.average_safety)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}

export function CompetencyAnalytics({
  read,
  identityId,
}: {
  read: ReadResource;
  identityId: string;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  const resource = useLearningResource(
    read,
    identityId,
    "/analytics/me/competencies",
    parseAnalytics,
  );
  const data = resource.data;
  useEffect(() => {
    heading.current?.focus();
  }, []);
  return (
    <section
      id="competency-analytics"
      className="competency-analytics"
      data-testid="competency-analytics"
      aria-labelledby="analytics-title"
    >
      <div className="analytics-heading">
        <p className="eyebrow">ПРАКТИКА / НАБЛЮДЕНИЯ / РОСТ</p>
        <h2 id="analytics-title" ref={heading} tabIndex={-1}>
          Аналитика компетенций
        </h2>
        <p>
          Узнайте, что получается устойчиво и к чему вернуться в следующей
          смене.
        </p>
      </div>
      {!data ? (
        <LearningLoadState
          kind="analytics"
          error={resource.error}
          retry={resource.retry}
        />
      ) : (
        <>
          <dl className="analytics-totals">
            <div>
              <dt>Всего попыток</dt>
              <dd>{data.total_sessions}</dd>
            </div>
            <div>
              <dt>Завершено</dt>
              <dd>{data.completed_sessions}</dd>
            </div>
            <div>
              <dt>В работе</dt>
              <dd>{data.active_sessions}</dd>
            </div>
            <div>
              <dt>Решений</dt>
              <dd>{data.decision_count}</dd>
            </div>
            <div>
              <dt>Таймаутов</dt>
              <dd>{data.timeout_count}</dd>
            </div>
          </dl>
          <p className="analytics-method">
            Выводы основаны на завершённых попытках. Для вывода нужны минимум 3
            наблюдения в 2 попытках. Это учебная эвристика, а не оценка
            профессиональной пригодности.
          </p>
          {data.completed_sessions === 0 && (
            <p className="learning-empty">
              Пока нет завершённых попыток. Пройдите ситуацию, чтобы увидеть
              первые наблюдения о навыках.
            </p>
          )}
          <dl className="analytics-conclusions">
            <div>
              <dt>Сильные стороны</dt>
              <dd>
                {data.strengths.length
                  ? data.strengths.map(competencyName).join(" · ")
                  : "Пока нет устойчивого вывода"}
              </dd>
            </div>
            <div>
              <dt>Зоны развития</dt>
              <dd>
                {data.weaknesses.length
                  ? data.weaknesses.map(competencyName).join(" · ")
                  : "Пока нет устойчивого вывода"}
              </dd>
            </div>
          </dl>
          <p className="learning-note points-explanation">
            Накопленные очки сохраняют положительный прирост завершённых попыток
            с вашим действием. Суммарное изменение также учитывает отрицательный
            результат.
          </p>
          <ul className="skills-analysis-list">
            {data.competencies.map((competency) => (
              <CompetencyRow
                key={competency.competency_id}
                competency={competency}
              />
            ))}
          </ul>
          <Patterns patterns={data.patterns} />
          <ScenarioStatistics scenarios={data.scenarios} />
        </>
      )}
    </section>
  );
}
