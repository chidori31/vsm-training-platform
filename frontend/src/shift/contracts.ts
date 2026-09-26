import { ContractError } from "../runner/api";

export interface Shift {
  id: string;
  title: string;
  status: "active" | "completed";
  seed: number;
  difficulty: "standard" | "advanced";
  started_at: string;
  completed_at: string | null;
  current_step: number;
  current_session_id: string | null;
  steps: {
    index: number;
    kind: "service" | "conflict" | "critical";
    title: string;
    scenario_id: string;
    scenario_version: number;
    passenger_profile: string;
    context: string;
    session_id: string | null;
    status: "locked" | "active" | "completed";
  }[];
  metrics: {
    safety: number | null;
    service: number | null;
    regulation: number;
    communication: number;
    average_reaction_seconds: number | null;
    decision_count: number;
    critical_errors: number;
    completed_scenarios: number;
    total_scenarios: number;
    xp: number;
  };
  achievements: {
    id: string;
    title: string;
    description: string;
    unlocked: boolean;
  }[];
  recommendations: string[];
}
export type WriteResource = (
  path: string,
  body: unknown,
  key: string,
  signal: AbortSignal,
) => Promise<unknown>;
export function parseShift(value: unknown): Shift {
  if (!value || typeof value !== "object") throw new ContractError();
  const s = value as Shift;
  if (
    typeof s.id !== "string" ||
    typeof s.title !== "string" ||
    !["active", "completed"].includes(s.status) ||
    !Number.isInteger(s.current_step) ||
    !Array.isArray(s.steps) ||
    !s.steps.length ||
    !s.metrics ||
    !Array.isArray(s.achievements) ||
    !Array.isArray(s.recommendations)
  )
    throw new ContractError();
  if (
    s.current_step < 0 ||
    s.current_step > s.steps.length ||
    !["standard", "advanced"].includes(s.difficulty)
  )
    throw new ContractError();
  for (const [index, step] of s.steps.entries()) {
    if (
      step.index !== index ||
      typeof step.title !== "string" ||
      typeof step.context !== "string" ||
      typeof step.passenger_profile !== "string" ||
      !["locked", "active", "completed"].includes(step.status) ||
      (step.session_id !== null && typeof step.session_id !== "string")
    )
      throw new ContractError();
  }
  for (const key of ["safety", "service", "average_reaction_seconds"] as const)
    if (s.metrics[key] !== null && !Number.isFinite(s.metrics[key]))
      throw new ContractError();
  for (const key of [
    "regulation",
    "communication",
    "decision_count",
    "critical_errors",
    "completed_scenarios",
    "total_scenarios",
    "xp",
  ] as const)
    if (!Number.isFinite(s.metrics[key])) throw new ContractError();
  const validText = (value: unknown): value is string =>
    typeof value === "string" && value.trim().length > 0;
  if (
    !validText(s.id) ||
    !validText(s.title) ||
    !Number.isSafeInteger(s.seed) ||
    s.seed < 0 ||
    !Number.isFinite(Date.parse(s.started_at))
  )
    throw new ContractError();
  for (const item of s.achievements) {
    if (
      !item ||
      !validText(item.id) ||
      !validText(item.title) ||
      !validText(item.description) ||
      typeof item.unlocked !== "boolean"
    )
      throw new ContractError();
  }
  if (
    new Set(s.achievements.map((a) => a.id)).size !== s.achievements.length ||
    !s.recommendations.every(validText)
  )
    throw new ContractError();
  for (const step of s.steps) {
    if (
      !validText(step.scenario_id) ||
      !Number.isSafeInteger(step.scenario_version) ||
      step.scenario_version < 1 ||
      !["service", "conflict", "critical"].includes(step.kind)
    )
      throw new ContractError();
    if (
      step.status === "locked"
        ? step.session_id !== null
        : !validText(step.session_id)
    )
      throw new ContractError();
    if (step.index < s.current_step && step.status !== "completed")
      throw new ContractError();
    if (step.index > s.current_step && step.status !== "locked")
      throw new ContractError();
  }
  if (s.status === "active") {
    if (
      s.current_step >= s.steps.length ||
      !validText(s.current_session_id) ||
      s.steps[s.current_step].session_id !== s.current_session_id ||
      s.completed_at !== null
    )
      throw new ContractError();
  } else if (
    s.current_step !== s.steps.length ||
    s.current_session_id !== null ||
    !validText(s.completed_at) ||
    !Number.isFinite(Date.parse(s.completed_at)) ||
    s.steps.some((step) => step.status !== "completed")
  )
    throw new ContractError();
  return s;
}
export function parseCurrentShift(value: unknown): Shift | null {
  if (!value || typeof value !== "object" || !("shift" in value))
    throw new ContractError();
  return value.shift === null ? null : parseShift(value.shift);
}
