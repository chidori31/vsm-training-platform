import { ContractError } from "../runner/api";
import type { SessionResult, ScenarioSummary } from "../runner/types";
import { scenarioTitle } from "../runner/sceneContent";
import type { ReadResource } from "../career/contracts";
import { competencyName } from "../career/contracts";
import { parseAnalytics } from "../learning/contracts";
import {
  displayDate,
  displayNumber,
  useLearningResource,
} from "../learning/resource";

function parseResults(value: unknown): SessionResult["summary"][] {
  if (
    !value ||
    typeof value !== "object" ||
    !("items" in value) ||
    !Array.isArray(value.items)
  )
    throw new ContractError();
  for (const item of value.items) {
    if (
      typeof item.session_id !== "string" ||
      typeof item.scenario_id !== "string" ||
      !Number.isSafeInteger(item.scenario_version) ||
      !Number.isFinite(item.safety_rating) ||
      !Number.isFinite(item.passenger_loyalty) ||
      !Number.isFinite(Date.parse(item.completed_at))
    )
      throw new ContractError();
  }
  return value.items as SessionResult["summary"][];
}
export function TodayBriefing({
  read,
  identityId,
  catalog,
  onResult,
}: {
  read: ReadResource;
  identityId: string;
  catalog: ScenarioSummary[];
  onResult: (id: string) => Promise<void>;
}) {
  const analytics = useLearningResource(
    read,
    identityId,
    "/analytics/me/competencies",
    parseAnalytics,
  );
  const recent = useLearningResource(
    read,
    identityId,
    "/results?limit=3",
    parseResults,
  );
  const data = analytics.data;
  const problem = data?.patterns.find((p) => p.recurring);
  return (
    <section
      className="today-briefing"
      aria-label="План подготовки и последние результаты"
    >
      <div className="today-task">
        <span className="eyebrow">ЗАДАЧА НА СЕГОДНЯ</span>
        <h3>
          {data?.weaknesses.length
            ? `Укрепить навык: ${competencyName(data.weaknesses[0])}`
            : "Закрепить последовательность действий"}
        </h3>
        <p>
          {problem?.advice ??
            "Выслушайте обращение, проверьте ограничения и подтвердите следующий шаг. После тренировки сравните последствия альтернатив."}
        </p>
        {data?.performance && (
          <p className="quiet-note">
            Среднее время решения:{" "}
            {data.performance.average_decision_seconds === null
              ? "пока нет данных"
              : `${displayNumber(data.performance.average_decision_seconds)} сек`}{" "}
            · Решений учтено: {data.performance.measured_decision_count}
          </p>
        )}
        {analytics.error && (
          <button className="text-button" onClick={analytics.retry}>
            Обновить рекомендации
          </button>
        )}
      </div>
      <div className="recent-results">
        <h3>Последние результаты</h3>
        {recent.data?.map((result) => (
          <button
            key={result.session_id}
            onClick={() => void onResult(result.session_id)}
          >
            <span>
              <strong>
                {scenarioTitle(
                  result.scenario_id,
                  result.scenario_version,
                  catalog.find(
                    (s) =>
                      s.id === result.scenario_id &&
                      s.version === result.scenario_version,
                  )?.title ?? "Учебная ситуация",
                )}
              </strong>
              <small>{displayDate(result.completed_at)}</small>
            </span>
            <span>
              Безопасность {result.safety_rating}
              <small>Сервис {result.passenger_loyalty} · Разбор →</small>
            </span>
          </button>
        ))}
        {recent.data?.length === 0 && (
          <p>
            Завершите первую ситуацию — результат и разбор сохранятся здесь.
          </p>
        )}
        {!recent.data && (
          <p>
            {recent.error
              ? "История временно недоступна."
              : "Получаем историю подготовки…"}
          </p>
        )}
        {recent.error && (
          <button className="text-button" onClick={recent.retry}>
            Обновить историю
          </button>
        )}
      </div>
    </section>
  );
}
