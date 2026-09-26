import { ContractError } from "../runner/api";

export interface ScoreReason {
  before: number;
  after: number;
  delta: number;
  requested_delta: number;
  explanation: string;
}
export interface CompetencyEffect {
  competency_id: string;
  delta: number;
}
export interface DebriefDecision {
  assessment?: {
    status: "critical_error" | "attention" | "strong" | "neutral";
    title: string;
    explanation: string;
    is_critical: boolean;
  };
  sequence: number;
  decision_id: string;
  node_id: string;
  node_text: string;
  choice_id: string;
  choice_text: string;
  destination_id: string;
  destination_text: string;
  decided_at: string;
  elapsed_seconds: number;
  was_timeout: boolean;
  explanation: string;
  loyalty: ScoreReason;
  safety: ScoreReason;
  competencies: (CompetencyEffect & {
    before: number;
    after: number;
    explanation: string;
  })[];
  alternatives: {
    choice_id: string;
    text: string;
    destination_id: string;
    explanation: string;
    available: boolean;
    loyalty_delta: number;
    safety_delta: number;
    competencies: CompetencyEffect[];
  }[];
  suggestion: { text: string; choice_ids: string[] };
  pattern_codes: string[];
}
export interface DebriefData {
  rule_version: 1;
  session_id: string;
  scenario_id: string;
  scenario_version: number;
  title: string;
  completed_at: string;
  summary: {
    decision_count: number;
    timeout_count: number;
    loyalty_delta: number;
    safety_delta: number;
  };
  decisions: DebriefDecision[];
}
export type CompetencyStatus =
  "insufficient_data" | "strength" | "growth_area" | "developing";
export interface CompetencyAnalysis {
  competency_id: string;
  earned_points: number;
  net_delta: number;
  positive_decisions: number;
  negative_decisions: number;
  opportunities: number;
  practiced_sessions: number;
  status: CompetencyStatus;
  trend: {
    session_id: string;
    completed_at: string;
    delta: number;
    earned_points: number;
    cumulative_points: number;
  }[];
}
export interface AnalyticsData {
  performance?: {
    measured_decision_count: number;
    average_decision_seconds: number | null;
    best_loyalty: number | null;
    best_safety: number | null;
    weeks: {
      week_start: string;
      completed_sessions: number;
      decision_count: number;
      timeout_count: number;
      average_decision_seconds: number | null;
      average_loyalty: number;
      average_safety: number;
    }[];
  };
  rule_version: 1;
  total_sessions: number;
  completed_sessions: number;
  active_sessions: number;
  decision_count: number;
  timeout_count: number;
  competencies: CompetencyAnalysis[];
  strengths: string[];
  weaknesses: string[];
  patterns: {
    code: string;
    title: string;
    description: string;
    count: number;
    session_count: number;
    recurring: boolean;
    advice: string;
  }[];
  scenarios: {
    scenario_id: string;
    scenario_version: number;
    title: string;
    attempts: number;
    completed: number;
    active: number;
    timeout_count: number;
    average_duration_seconds: number | null;
    average_loyalty: number | null;
    average_safety: number | null;
  }[];
}

function check(ok: unknown): asserts ok {
  if (!ok) throw new ContractError();
}
function object(value: unknown): Record<string, unknown> {
  check(value !== null && typeof value === "object" && !Array.isArray(value));
  return value as Record<string, unknown>;
}
function text(value: unknown): asserts value is string {
  check(typeof value === "string" && value.trim().length > 0);
}
function number(value: unknown, minimum = -Infinity): asserts value is number {
  check(
    typeof value === "number" && Number.isFinite(value) && value >= minimum,
  );
}
function integer(value: unknown, minimum = 0): asserts value is number {
  number(value, minimum);
  check(Number.isSafeInteger(value));
}
function list(value: unknown): unknown[] {
  check(Array.isArray(value));
  return value;
}
function date(value: unknown) {
  text(value);
  check(Number.isFinite(Date.parse(value)));
}
function boolean(value: unknown) {
  check(typeof value === "boolean");
}
function stringList(value: unknown): string[] {
  const items = list(value);
  for (const item of items) text(item);
  check(new Set(items).size === items.length);
  return items as string[];
}
function unique(items: Record<string, unknown>[], key: string) {
  check(new Set(items.map((item) => item[key])).size === items.length);
}
function effects(value: unknown, detailed: boolean) {
  const items = list(value).map(object);
  for (const item of items) {
    text(item.competency_id);
    integer(item.delta, -Number.MAX_SAFE_INTEGER);
    if (detailed) {
      integer(item.before, -Number.MAX_SAFE_INTEGER);
      integer(item.after, -Number.MAX_SAFE_INTEGER);
      text(item.explanation);
    }
  }
  unique(items, "competency_id");
}

export function parseDebrief(value: unknown): DebriefData {
  const data = object(value);
  check(data.rule_version === 1);
  for (const key of ["session_id", "scenario_id", "title"]) text(data[key]);
  integer(data.scenario_version, 1);
  date(data.completed_at);
  const summary = object(data.summary);
  for (const key of ["decision_count", "timeout_count"]) integer(summary[key]);
  for (const key of ["loyalty_delta", "safety_delta"])
    integer(summary[key], -Number.MAX_SAFE_INTEGER);
  const decisions = list(data.decisions).map(object);
  unique(decisions, "decision_id");
  check(summary.decision_count === decisions.length);
  for (const [index, decision] of decisions.entries()) {
    check(decision.sequence === index + 1);
    for (const key of [
      "decision_id",
      "node_id",
      "node_text",
      "choice_id",
      "choice_text",
      "destination_id",
      "destination_text",
      "explanation",
    ])
      text(decision[key]);
    date(decision.decided_at);
    number(decision.elapsed_seconds, 0);
    boolean(decision.was_timeout);
    if (decision.assessment !== undefined) {
      const assessment = object(decision.assessment);
      check(
        ["critical_error", "attention", "strong", "neutral"].includes(
          String(assessment.status),
        ),
      );
      text(assessment.title);
      text(assessment.explanation);
      boolean(assessment.is_critical);
    }
    for (const key of ["loyalty", "safety"]) {
      const score = object(decision[key]);
      for (const field of ["before", "after", "delta", "requested_delta"])
        integer(score[field], -Number.MAX_SAFE_INTEGER);
      text(score.explanation);
    }
    effects(decision.competencies, true);
    const alternatives = list(decision.alternatives).map(object);
    unique(alternatives, "choice_id");
    for (const alternative of alternatives) {
      for (const key of ["choice_id", "text", "destination_id", "explanation"])
        text(alternative[key]);
      boolean(alternative.available);
      integer(alternative.loyalty_delta, -Number.MAX_SAFE_INTEGER);
      integer(alternative.safety_delta, -Number.MAX_SAFE_INTEGER);
      effects(alternative.competencies, false);
    }
    const suggestion = object(decision.suggestion);
    text(suggestion.text);
    for (const id of stringList(suggestion.choice_ids))
      check(
        alternatives.some(
          (alternative) =>
            alternative.choice_id === id && alternative.available === true,
        ),
      );
    stringList(decision.pattern_codes);
  }
  return value as DebriefData;
}

export function parseAnalytics(value: unknown): AnalyticsData {
  const data = object(value);
  check(data.rule_version === 1);
  if (data.performance !== undefined) {
    const performance = object(data.performance);
    integer(performance.measured_decision_count);
    if (performance.average_decision_seconds !== null)
      number(performance.average_decision_seconds, 0);
    for (const key of ["best_loyalty", "best_safety"])
      if (performance[key] !== null)
        integer(performance[key], -Number.MAX_SAFE_INTEGER);
    const weeks = list(performance.weeks).map(object);
    unique(weeks, "week_start");
    for (const week of weeks) {
      date(week.week_start);
      integer(week.completed_sessions, 1);
      integer(week.decision_count);
      integer(week.timeout_count);
      if (week.average_decision_seconds !== null)
        number(week.average_decision_seconds, 0);
      number(week.average_loyalty);
      number(week.average_safety);
    }
  }
  for (const key of [
    "total_sessions",
    "completed_sessions",
    "active_sessions",
    "decision_count",
    "timeout_count",
  ])
    integer(data[key]);
  const competencies = list(data.competencies).map(object);
  unique(competencies, "competency_id");
  for (const competency of competencies) {
    text(competency.competency_id);
    for (const key of [
      "earned_points",
      "positive_decisions",
      "negative_decisions",
      "opportunities",
      "practiced_sessions",
    ])
      integer(competency[key]);
    integer(competency.net_delta, -Number.MAX_SAFE_INTEGER);
    check(
      ["insufficient_data", "strength", "growth_area", "developing"].includes(
        String(competency.status),
      ),
    );
    const trend = list(competency.trend).map(object);
    unique(trend, "session_id");
    for (const point of trend) {
      text(point.session_id);
      date(point.completed_at);
      integer(point.delta, -Number.MAX_SAFE_INTEGER);
      integer(point.earned_points);
      integer(point.cumulative_points);
    }
  }
  for (const key of ["strengths", "weaknesses"]) {
    for (const id of stringList(data[key]))
      check(competencies.some((competency) => competency.competency_id === id));
  }
  const patterns = list(data.patterns).map(object);
  unique(patterns, "code");
  for (const pattern of patterns) {
    for (const key of ["code", "title", "description", "advice"])
      text(pattern[key]);
    integer(pattern.count);
    integer(pattern.session_count);
    boolean(pattern.recurring);
  }
  for (const scenario of list(data.scenarios).map(object)) {
    text(scenario.scenario_id);
    text(scenario.title);
    integer(scenario.scenario_version, 1);
    for (const key of ["attempts", "completed", "active", "timeout_count"])
      integer(scenario[key]);
    if (scenario.average_duration_seconds !== null)
      number(scenario.average_duration_seconds, 0);
    for (const key of ["average_loyalty", "average_safety"])
      if (scenario[key] !== null) number(scenario[key]);
  }
  return value as AnalyticsData;
}
