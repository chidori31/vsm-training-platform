import { useEffect, useRef, useState } from "react";
import { PassengerPortrait } from "./runner/PassengerPortrait";
import {
  scenarioPresentation,
  scenarioTitle,
  scenePresentation,
} from "./runner/sceneContent";
import type { Decision, SessionSnapshot } from "./runner/types";
import { useScenarioRunner } from "./runner/useScenarioRunner";
import { CareerPanel, CompletionReward } from "./career/CareerPanel";
import { Debrief } from "./learning/Debrief";

function Arrow({ diagonal = false }: { diagonal?: boolean }) {
  return (
    <span aria-hidden="true" className="arrow">
      {diagonal ? "↗" : "→"}
    </span>
  );
}

function scoreValue(session: SessionSnapshot, metric: string) {
  return session.scores.find((score) => score.metric === metric)?.value ?? 0;
}

function Scoreboard({ session }: { session: SessionSnapshot }) {
  const last = session.decisions.at(-1);
  return (
    <div className="scoreboard" aria-label="Игровые показатели">
      {(
        [
          {
            metric: "passenger_loyalty",
            name: "Доверие пассажиров",
            english: "PASSENGER LOYALTY",
            key: "loyalty",
          },
          {
            metric: "safety_rating",
            name: "Безопасность",
            english: "SAFETY RATING",
            key: "safety",
          },
        ] as const
      ).map(({ metric, name, english, key }) => {
        const value = scoreValue(session, metric);
        const bounds = session.scoring_policy[key];
        const width = Math.max(
          0,
          Math.min(
            100,
            ((value - bounds.minimum) /
              Math.max(1, bounds.maximum - bounds.minimum)) *
              100,
          ),
        );
        const delta =
          last?.score_changes
            .filter((change) => change.metric === metric)
            .reduce((total, change) => total + change.applied_delta, 0) ?? 0;
        return (
          <div className={`score score-${key}`} key={key}>
            <span className="micro-label">{english}</span>
            <div className="score-reading">
              <strong data-testid={`${key}-value`}>{value}</strong>
              <span className="score-max">/ {bounds.maximum}</span>
              {delta !== 0 && (
                <span
                  className={`score-delta ${delta < 0 ? "negative" : ""}`}
                  aria-label={`Изменение: ${delta > 0 ? "+" : ""}${delta}`}
                >
                  {delta > 0 ? "+" : ""}
                  {delta}
                </span>
              )}
            </div>
            <span className="score-name">{name}</span>
            <div
              className="score-track"
              role="meter"
              aria-label={name}
              aria-valuemin={bounds.minimum}
              aria-valuemax={bounds.maximum}
              aria-valuenow={value}
            >
              <span style={{ width: `${width}%` }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function DecisionFeedback({ decision }: { decision: Decision }) {
  const timeout = decision.choice_id === "__timeout__";
  return (
    <div
      className={`decision-feedback ${timeout ? "timeout-feedback" : ""}`}
      role="status"
    >
      <span className="feedback-mark" aria-hidden="true">
        {timeout ? "!" : "✓"}
      </span>
      <div>
        <strong>
          {timeout ? "Время на решение истекло" : "Решение принято"}
        </strong>
        <p>{decision.explanation}</p>
      </div>
    </div>
  );
}

function RouteLine({ session }: { session: SessionSnapshot }) {
  const visited = [
    ...session.decisions.map((decision) => decision.node_id),
    session.current_node_id,
  ];
  return (
    <div className="route-strip">
      <span className="micro-label">МАРШРУТ РЕШЕНИЙ</span>
      <ol className="route-line" aria-label="Пройденные сцены">
        {visited.map((node, index) => {
          const current = index === visited.length - 1;
          return (
            <li
              key={`${index}-${node}`}
              aria-current={current ? "step" : undefined}
              className={current ? "current" : "visited"}
            >
              <span className="route-point" aria-hidden="true">
                {current
                  ? session.status === "completed"
                    ? "✓"
                    : String(index + 1).padStart(2, "0")
                  : "✓"}
              </span>
              <span>
                {current && session.status === "completed"
                  ? "Разбор"
                  : `Сцена ${String(index + 1).padStart(2, "0")}`}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

export default function App() {
  const runner = useScenarioRunner();
  const [screen, setScreen] = useState<"play" | "profile" | "leaderboard">(
    "play",
  );
  const [selectedId, setSelectedId] = useState("");
  const heading = useRef<HTMLHeadingElement>(null);
  const selected =
    runner.catalog.find(
      (item) => `${item.id}:${item.version}` === selectedId,
    ) ??
    runner.catalog.find((item) => item.id === "demo-passenger-conflict") ??
    runner.catalog[0];
  const state = runner.state;
  const session = state?.session;
  const completed = session?.status === "completed";
  const lastDecision = session?.decisions.at(-1);
  const phase = session ? (completed ? 2 : 1) : 0;
  const sceneKey = session
    ? `${session.id}:${state.current_node.id}:${state.expected_sequence}`
    : "catalog";

  useEffect(() => {
    heading.current?.focus();
  }, [sceneKey, screen]);

  const connectionProblem = runner.error || runner.connection === "offline";
  const selectedCopy =
    selected && scenarioPresentation(selected.id, selected.version);
  const scene =
    state &&
    scenePresentation(
      state.session.scenario_id,
      state.session.scenario_version,
      state.current_node.id,
      state.current_node.text,
    );
  const seconds = runner.remainingSeconds;
  const timeExpired = state?.deadline !== null && seconds === 0;
  const actionsDisabled =
    runner.busy || runner.connection !== "online" || Boolean(timeExpired);

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        К основной сцене
      </a>
      <header className="system-header">
        <div className="wordmark" aria-label="ВСМ — учебная смена">
          <svg viewBox="0 0 46 32" aria-hidden="true">
            <path
              d="M0 2h21l-9 10H0zm0 16h11L0 30zm29-16h17L19 30H2z"
              fill="currentColor"
            />
          </svg>
          <span>
            ВСМ<span className="wordmark-divider">/</span>СМЕНА
          </span>
        </div>
        <span className="header-caption">ТРЕНАЖЁР ПРОВОДНИКА</span>
        <span className="operation-status">
          <span
            className={`status-light ${runner.connection === "offline" ? "disconnected" : ""}`}
          />
          {runner.connection === "offline"
            ? "Связь прервана"
            : "Учебная поездка"}
        </span>
      </header>

      <main
        id="main-content"
        className="workspace"
        data-testid={session ? "scenario-runner" : undefined}
      >
        {session?.status !== "active" && runner.phase !== "loading" && (
          <nav className="career-navigation" aria-label="Учебная смена">
            <button
              aria-current={screen === "play" ? "page" : undefined}
              onClick={() => setScreen("play")}
            >
              Смена
            </button>
            <button
              disabled={!runner.identity || runner.busy}
              aria-current={screen === "profile" ? "page" : undefined}
              onClick={() => setScreen("profile")}
            >
              Мой прогресс
            </button>
            <button
              disabled={!runner.identity || runner.busy}
              aria-current={screen === "leaderboard" ? "page" : undefined}
              onClick={() => setScreen("leaderboard")}
            >
              Рейтинг
            </button>
            <span>{runner.identity?.display_name}</span>
          </nav>
        )}
        {screen !== "play" && session?.status !== "active" ? (
          <>
            {runner.error && (
              <div className="connection-notice" role="alert">
                <p>{runner.error}</p>
                <button
                  className="reconnect-button"
                  disabled={runner.busy}
                  onClick={() => void runner.reconnect()}
                >
                  Восстановить связь
                </button>
              </div>
            )}
            <CareerPanel
              key={runner.identity?.id}
              mode={screen}
              read={runner.readResource}
              identityId={runner.identity?.id ?? "demo-employee"}
              busy={runner.busy}
              switchPersona={runner.switchPersona}
              onPlay={() => {
                if (completed) runner.leave();
                setScreen("play");
              }}
            />
          </>
        ) : (
          <>
            <div className="journey-header">
              <ol aria-label="Этапы прохождения">
                {["Подготовка", "Ситуация", "Разбор"].map((label, index) => (
                  <li
                    key={label}
                    className={
                      index === phase ? "current" : index < phase ? "done" : ""
                    }
                    aria-current={index === phase ? "step" : undefined}
                  >
                    <span>{String(index + 1).padStart(2, "0")}</span>
                    {label}
                  </li>
                ))}
              </ol>
              <span className="training-badge">SIMULATION / ВСМ</span>
            </div>

            {connectionProblem && (
              <div className="connection-notice" role="alert">
                <div>
                  <strong>
                    {runner.connection === "online"
                      ? "Ситуация обновлена"
                      : session
                        ? "Сохраняем место в вашей смене"
                        : "Не удалось подключиться"}
                  </strong>
                  <p>
                    {runner.error ||
                      "Соединение потеряно. После восстановления загрузим актуальное состояние."}
                  </p>
                  {runner.pendingChoiceId && (
                    <p>
                      Проверим, принято ли последнее действие. Повторно выбирать
                      его не нужно.
                    </p>
                  )}
                </div>
                <button
                  className="reconnect-button"
                  disabled={runner.connection === "reconnecting"}
                  onClick={() => void runner.reconnect()}
                >
                  {runner.connection === "online"
                    ? "Обновить состояние"
                    : "Восстановить связь"}{" "}
                  <Arrow />
                </button>
              </div>
            )}
            {runner.connection === "reconnecting" &&
              runner.phase !== "loading" &&
              runner.phase !== "starting" &&
              !runner.pendingChoiceId && (
                <p className="connection-progress" role="status">
                  Восстанавливаем связь…
                </p>
              )}

            {!session && (
              <>
                <div className="section-heading">
                  <div>
                    <p className="eyebrow">ВАША УЧЕБНАЯ СМЕНА</p>
                    <h1 ref={heading} tabIndex={-1}>
                      Выберите рабочую ситуацию
                    </h1>
                  </div>
                  <span className="section-number" aria-hidden="true">
                    01
                  </span>
                </div>
                {runner.phase === "loading" && !connectionProblem && (
                  <div className="loading-scene" role="status">
                    <span className="loading-track" aria-hidden="true" />
                    <p>Подключаемся к учебной смене…</p>
                  </div>
                )}
                {selected && selectedCopy && (
                  <div className="departure-board">
                    <div className="briefing-preview">
                      <div className="briefing-copy">
                        <span className="eyebrow">{selectedCopy.category}</span>
                        <h2>
                          {scenarioTitle(
                            selected.id,
                            selected.version,
                            selected.title,
                          )}
                        </h2>
                        <p>{selectedCopy.context}</p>
                      </div>
                      <div className="briefing-art">
                        <PassengerPortrait kind={selectedCopy.portrait} />
                        <span className="window-caption">
                          ВСМ / УЧЕБНЫЙ ВАГОН
                        </span>
                      </div>
                    </div>
                    <div className="departure-controls">
                      <fieldset disabled={runner.busy}>
                        <legend>Ситуации на маршруте</legend>
                        <div className="board-column-head" aria-hidden="true">
                          <span>СИТУАЦИЯ</span>
                          <span>ВЫБОР</span>
                        </div>
                        {runner.catalog.map((scenario, index) => {
                          const chosen =
                            scenario.id === selected.id &&
                            scenario.version === selected.version;
                          return (
                            <label
                              key={`${scenario.id}:${scenario.version}`}
                              className={`scenario-row ${chosen ? "selected" : ""}`}
                              data-testid="scenario-card"
                            >
                              <span
                                className="departure-index"
                                aria-hidden="true"
                              >
                                {String(index + 1).padStart(2, "0")}
                              </span>
                              <span className="departure-text">
                                <strong>
                                  {scenarioTitle(
                                    scenario.id,
                                    scenario.version,
                                    scenario.title,
                                  )}
                                </strong>
                                <span>
                                  {
                                    scenarioPresentation(
                                      scenario.id,
                                      scenario.version,
                                    ).duration
                                  }
                                </span>
                              </span>
                              <input
                                type="radio"
                                name="scenario"
                                value={`${scenario.id}:${scenario.version}`}
                                checked={chosen}
                                onChange={() =>
                                  setSelectedId(
                                    `${scenario.id}:${scenario.version}`,
                                  )
                                }
                                aria-label={scenarioTitle(
                                  scenario.id,
                                  scenario.version,
                                  scenario.title,
                                )}
                              />
                            </label>
                          );
                        })}
                      </fieldset>
                      <div className="departure-action">
                        <p>
                          <span aria-hidden="true">◷</span> Время начнётся после
                          старта
                        </p>
                        <button
                          className="primary-button"
                          disabled={
                            runner.busy || runner.connection !== "online"
                          }
                          onClick={() => void runner.start(selected)}
                        >
                          {runner.phase === "starting" || runner.busy
                            ? "Готовим ситуацию…"
                            : "Начать сценарий"}
                          <Arrow />
                        </button>
                        <span className="quiet-note">
                          Каждый выбор меняет дальнейший разговор.
                        </span>
                      </div>
                    </div>
                  </div>
                )}
                {runner.phase === "ready" &&
                  runner.catalog.length === 0 &&
                  !connectionProblem && (
                    <div className="empty-scene">
                      <h2>Пока нет учебных ситуаций</h2>
                      <p>
                        Каталог ещё не заполнен. После добавления сценариев они
                        появятся здесь.
                      </p>
                      <button
                        className="text-button"
                        onClick={() => void runner.reconnect()}
                      >
                        Обновить каталог <Arrow />
                      </button>
                    </div>
                  )}
              </>
            )}

            {state && session && scene && !completed && (
              <>
                <div className="section-heading compact">
                  <div>
                    <p className="eyebrow">
                      {scenarioTitle(
                        session.scenario_id,
                        session.scenario_version,
                        runner.catalog.find(
                          (item) =>
                            item.id === session.scenario_id &&
                            item.version === session.scenario_version,
                        )?.title ?? "Учебная ситуация",
                      )}
                    </p>
                    <h1 ref={heading} tabIndex={-1}>
                      {scene.title}
                    </h1>
                  </div>
                  <span className="section-number" aria-hidden="true">
                    {String(session.decisions.length + 1).padStart(2, "0")}
                  </span>
                </div>
                <div className="operating-stage">
                  <section
                    className="conversation-scene"
                    aria-label="Разговор в вагоне"
                    key={sceneKey}
                  >
                    <div className="conversation-copy">
                      <span className="character-label">
                        <span aria-hidden="true" />
                        {scene.role}
                      </span>
                      {scene.isDialogue ? (
                        <blockquote>{scene.dialogue}</blockquote>
                      ) : (
                        <p className="scene-statement">{scene.dialogue}</p>
                      )}
                      <p className="scene-context">{scene.context}</p>
                      <details className="source-context">
                        <summary>Контекст ситуации</summary>
                        <p>{state.current_node.text}</p>
                      </details>
                    </div>
                    <div className="scene-art">
                      <PassengerPortrait kind={scene.portrait} />
                      <span className="window-caption">
                        УЧЕБНЫЙ ВАГОН / СЦЕНА{" "}
                        {String(session.decisions.length + 1).padStart(2, "0")}
                      </span>
                    </div>
                  </section>
                  <aside
                    className="instruments"
                    aria-label="Показатели и время"
                  >
                    <div
                      className={`timer ${seconds !== null && seconds <= 10 ? "timer-urgent" : ""}`}
                      role="timer"
                      aria-label={
                        seconds === null
                          ? "Решение без ограничения времени"
                          : `Осталось ${seconds} секунд`
                      }
                    >
                      <span className="micro-label">
                        {seconds === null
                          ? "СПОКОЙНЫЙ ТЕМП"
                          : "ВРЕМЯ НА РЕШЕНИЕ"}
                      </span>
                      <strong>
                        {seconds === null
                          ? "—:—"
                          : `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`}
                      </strong>
                      <span>
                        {timeExpired
                          ? "Уточняем итог…"
                          : seconds === null
                            ? "Обдумайте следующий шаг"
                            : "Выберите действие до конца отсчёта"}
                      </span>
                    </div>
                    <Scoreboard session={session} />
                  </aside>
                </div>
                <RouteLine session={session} />
                {lastDecision && (
                  <DecisionFeedback
                    key={lastDecision.id}
                    decision={lastDecision}
                  />
                )}
                <section
                  className="action-section"
                  aria-labelledby="actions-heading"
                >
                  <div className="action-heading">
                    <h2 id="actions-heading">Ваше действие</h2>
                    <span>
                      {runner.busy
                        ? "Проверяем отправленное решение…"
                        : timeExpired
                          ? "Ожидаем итог по времени"
                          : "Выберите один вариант"}
                    </span>
                  </div>
                  <div className="choices">
                    {state.available_choices.map((choice, index) => (
                      <button
                        className={`choice ${runner.pendingChoiceId === choice.id ? "pending" : ""}`}
                        key={`${state.current_node.id}:${choice.id}`}
                        disabled={actionsDisabled}
                        onClick={() => void runner.choose(choice.id)}
                        aria-label={choice.text}
                      >
                        <span className="choice-index" aria-hidden="true">
                          {String(index + 1).padStart(2, "0")}
                        </span>
                        <span>
                          {choice.text}
                          {runner.pendingChoiceId === choice.id && (
                            <small>Отправлено · ожидаем подтверждение</small>
                          )}
                        </span>
                        <Arrow diagonal />
                      </button>
                    ))}
                  </div>
                  {timeExpired && (
                    <p className="quiet-note" role="status">
                      Время истекло. Загружаем следующую сцену.
                    </p>
                  )}
                </section>
              </>
            )}

            {state && session && completed && (
              <div className="completion">
                <div className="section-heading">
                  <div>
                    <p className="eyebrow">МАРШРУТ ПРОЙДЕН / РАЗБОР СМЕНЫ</p>
                    <h1 ref={heading} tabIndex={-1}>
                      Сценарий завершён
                    </h1>
                  </div>
                  <span className="completion-symbol" aria-hidden="true">
                    ↗
                  </span>
                </div>
                <div className="result-overview">
                  <div className="result-copy">
                    <span className="eyebrow">ИТОГ СИТУАЦИИ</span>
                    <p>{state.current_node.text}</p>
                    <span className="quiet-note">
                      Доверие и безопасность оцениваются независимо. Разберите,
                      как каждое решение повлияло на результат.
                    </span>
                  </div>
                  <Scoreboard session={session} />
                </div>
                <RouteLine session={session} />
                {lastDecision && (
                  <DecisionFeedback
                    key={lastDecision.id}
                    decision={lastDecision}
                  />
                )}
                <Debrief
                  key={`${runner.identity?.id}:${session.id}`}
                  read={runner.readResource}
                  sessionId={session.id}
                  identityId={runner.identity?.id ?? "demo-employee"}
                />
                <div className="completion-actions">
                  <button
                    className="primary-button"
                    onClick={runner.leave}
                    disabled={runner.busy}
                  >
                    Выбрать другую ситуацию <Arrow />
                  </button>
                  <span className="quiet-note">
                    История этой попытки сохранена.
                  </span>
                </div>
                <CompletionReward
                  key={session.id}
                  read={runner.readResource}
                  sessionId={session.id}
                  onProfile={() => setScreen("profile")}
                />
              </div>
            )}
          </>
        )}
        <footer className="workspace-footer">
          <span>Учебная модель · синтетические ситуации</span>
          <span>ТОЧНОСТЬ РЕШЕНИЙ. УВЕРЕННОСТЬ В ПУТИ.</span>
        </footer>
      </main>
    </div>
  );
}
