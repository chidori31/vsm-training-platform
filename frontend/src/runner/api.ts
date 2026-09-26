import type {
  CatalogPage,
  LoginResponse,
  SessionResult,
  SessionSnapshot,
  SessionState,
} from "./types";

export class ContractError extends Error {
  constructor() {
    super("Сервер вернул некорректный ответ. Повторите подключение.");
  }
}
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    public data: unknown,
  ) {
    super(code);
  }
}
export class NetworkError extends Error {
  constructor() {
    super(
      "Нет соединения с сервером. Восстановите подключение, чтобы подтвердить действие.",
    );
  }
}
type JsonObject = Record<string, unknown>;
function check(condition: unknown): asserts condition {
  if (!condition) throw new ContractError();
}
function object(value: unknown): JsonObject {
  check(value !== null && typeof value === "object" && !Array.isArray(value));
  return value as JsonObject;
}
function text(value: unknown): asserts value is string {
  check(typeof value === "string" && value.trim().length > 0);
}
function string(value: unknown) {
  check(typeof value === "string");
}
function integer(
  value: unknown,
  minimum = Number.MIN_SAFE_INTEGER,
): asserts value is number {
  check(
    typeof value === "number" &&
      Number.isSafeInteger(value) &&
      value >= minimum,
  );
}
function date(value: unknown): asserts value is string {
  text(value);
  check(
    /^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(value) &&
      Number.isFinite(Date.parse(value)),
  );
}
function array(value: unknown): unknown[] {
  check(Array.isArray(value));
  return value;
}
function metric(value: JsonObject) {
  check(
    value.metric === "passenger_loyalty" ||
      value.metric === "safety_rating" ||
      value.metric === "competency",
  );
  if (value.metric === "competency") text(value.competency_id);
  else check(value.competency_id === null);
}
function scores(value: unknown) {
  const seen = new Set<string>();
  for (const raw of array(value)) {
    const item = object(raw);
    metric(item);
    integer(item.value);
    const key = `${item.metric}:${item.competency_id}`;
    check(!seen.has(key));
    seen.add(key);
  }
  check(seen.has("passenger_loyalty:null") && seen.has("safety_rating:null"));
}
function parseSnapshot(value: unknown): SessionSnapshot {
  const item = object(value);
  check(item.format_version === 2);
  for (const key of ["id", "employee_id", "scenario_id", "current_node_id"])
    text(item[key]);
  integer(item.scenario_version, 1);
  scores(item.scores);
  scores(item.initial_scores);
  const policy = object(item.scoring_policy);
  for (const name of ["loyalty", "safety"]) {
    const bounds = object(policy[name]);
    integer(bounds.minimum);
    integer(bounds.maximum);
    check(bounds.minimum <= bounds.maximum);
  }
  date(item.started_at);
  check(item.status === "active" || item.status === "completed");
  if (item.status === "completed") date(item.completed_at);
  else check(item.completed_at === null);
  const ids = new Set<string>();
  array(item.decisions).forEach((raw, index) => {
    const decision = object(raw);
    for (const key of ["id", "session_id", "node_id", "choice_id"])
      text(decision[key]);
    check(decision.session_id === item.id && decision.sequence === index + 1);
    check(!ids.has(String(decision.id)));
    ids.add(String(decision.id));
    date(decision.decided_at);
    string(decision.explanation);
    for (const rawEffect of array(decision.effects)) {
      const effect = object(rawEffect);
      metric(effect);
      check(effect.type === "add_score");
      integer(effect.delta);
    }
    for (const rawChange of array(decision.score_changes)) {
      const change = object(rawChange);
      metric(change);
      for (const key of ["before", "requested_delta", "after", "applied_delta"])
        integer(change[key]);
      check(
        Number(change.applied_delta) ===
          Number(change.after) - Number(change.before),
      );
      string(change.explanation);
    }
  });
  return value as SessionSnapshot;
}
export function parseSessionState(value: unknown): SessionState {
  const item = object(value);
  const session = parseSnapshot(item.session);
  date(item.server_time);
  if (item.deadline !== null) date(item.deadline);
  integer(item.expected_sequence, 0);
  check(item.expected_sequence === session.decisions.length);
  const node = object(item.current_node);
  text(node.id);
  text(node.text);
  check(typeof node.terminal === "boolean");
  check(
    node.id === session.current_node_id &&
      node.terminal === (session.status === "completed"),
  );
  if (node.time_limit_seconds !== null) integer(node.time_limit_seconds, 1);
  check(
    (item.deadline !== null) ===
      (session.status === "active" && node.time_limit_seconds !== null),
  );
  const choices = array(item.available_choices);
  const seen = new Set<string>();
  for (const raw of choices) {
    const choice = object(raw);
    text(choice.id);
    text(choice.text);
    check(!seen.has(choice.id));
    seen.add(choice.id);
  }
  if (session.status === "completed") check(choices.length === 0);
  return value as SessionState;
}
export function parseDecisionResponse(
  value: unknown,
  decisionId: string,
): SessionState {
  const state = parseSessionState(value);
  const item = object(value);
  check(item.outcome === "accepted" || item.outcome === "duplicate");
  check(item.acknowledged_decision_id === decisionId);
  check(state.session.decisions.some((decision) => decision.id === decisionId));
  return state;
}
export function parseSessionResult(value: unknown): SessionResult {
  const item = object(value);
  const session = parseSnapshot(item.session);
  const summary = object(item.summary);
  for (const key of ["session_id", "scenario_id"]) text(summary[key]);
  check(
    session.status === "completed" &&
      summary.status === "completed" &&
      summary.session_id === session.id &&
      summary.scenario_id === session.scenario_id,
  );
  integer(summary.scenario_version, 1);
  check(summary.scenario_version === session.scenario_version);
  integer(summary.decision_count, 0);
  check(summary.decision_count === session.decisions.length);
  check(
    typeof summary.duration_seconds === "number" &&
      Number.isFinite(summary.duration_seconds) &&
      summary.duration_seconds >= 0,
  );
  integer(summary.passenger_loyalty);
  integer(summary.safety_rating);
  date(summary.completed_at);
  check(summary.completed_at === session.completed_at);
  const seen = new Set<string>();
  for (const raw of array(summary.competencies)) {
    const score = object(raw);
    text(score.competency_id);
    integer(score.value);
    check(!seen.has(score.competency_id));
    seen.add(score.competency_id);
  }
  return value as SessionResult;
}
export function parseCatalogPage(value: unknown): CatalogPage {
  const item = object(value);
  integer(item.total, 0);
  integer(item.offset, 0);
  integer(item.limit, 1);
  check(item.limit <= 100);
  const items = array(item.items);
  check(items.length <= item.limit);
  for (const raw of items) {
    const scenario = object(raw);
    text(scenario.id);
    integer(scenario.version, 1);
    text(scenario.title);
    for (const id of array(scenario.competency_ids)) text(id);
  }
  return value as CatalogPage;
}
export function parseLogin(value: unknown): LoginResponse {
  const item = object(value);
  text(item.access_token);
  check(item.token_type === "bearer");
  date(item.expires_at);
  const profile = object(item.profile);
  text(profile.id);
  text(profile.display_name);
  return value as LoginResponse;
}

export async function request(
  path: string,
  init: RequestInit,
  signal: AbortSignal,
): Promise<unknown> {
  const controller = new AbortController();
  let rejectAbort: () => void = () => {};
  const cancelled = new Promise<never>((_, reject) => {
    rejectAbort = () => reject(new DOMException("Aborted", "AbortError"));
  });
  controller.signal.addEventListener("abort", rejectAbort, { once: true });
  const abort = () => controller.abort();
  signal.addEventListener("abort", abort, { once: true });
  if (signal.aborted) controller.abort();
  let timeout: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      (async () => {
        const response = await fetch(`/api/v1${path}`, {
          ...init,
          signal: controller.signal,
        });
        if (controller.signal.aborted)
          throw new DOMException("Aborted", "AbortError");
        let body: unknown;
        try {
          body = await response.json();
        } catch {
          throw new ContractError();
        }
        if (controller.signal.aborted)
          throw new DOMException("Aborted", "AbortError");
        if (!response.ok) {
          const error = object(object(body).error);
          text(error.code);
          string(error.message);
          array(error.details);
          throw new ApiError(response.status, error.code, object(body).data);
        }
        return body;
      })(),
      cancelled,
      new Promise<never>((_, reject) => {
        timeout = setTimeout(() => {
          controller.abort();
          reject(new NetworkError());
        }, 8000);
      }),
    ]);
  } catch (error) {
    if (signal.aborted) throw new DOMException("Aborted", "AbortError");
    if (
      error instanceof ApiError ||
      error instanceof ContractError ||
      error instanceof NetworkError
    )
      throw error;
    throw new NetworkError();
  } finally {
    clearTimeout(timeout);
    signal.removeEventListener("abort", abort);
    controller.signal.removeEventListener("abort", rejectAbort);
  }
}
export function friendlyError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "demo_auth_disabled")
      return "Демо-вход отключён. Включите DEMO_AUTH_ENABLED=true на сервере.";
    if (error.code === "decision_timed_out")
      return "Время выбора истекло. Показано актуальное состояние сервера.";
    if (error.code === "decision_rejected")
      return "Ситуация уже изменилась. Показано актуальное состояние сервера.";
    if (error.status === 429)
      return "Слишком много запросов. Подождите минуту и повторите действие; подтверждённый прогресс сохранён.";
    if (error.status === 401)
      return "Не удалось обновить демо-вход. Повторите подключение.";
    if (error.status === 404)
      return "Сценарий или попытка недоступны на сервере.";
    if (error.status >= 500)
      return "Сервер временно недоступен. Повторите подключение.";
    return "Сервер не принял действие. Повторите подключение.";
  }
  return error instanceof Error
    ? error.message
    : "Не удалось связаться с сервером. Повторите подключение.";
}
