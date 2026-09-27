import { ContractError } from "../runner/api";
import {
  parseSimulation,
  parseSimulationDebrief,
  type Simulation,
  type SimulationDebrief,
} from "../simulation/contracts";

export type TrainingMode = "work" | "tutorial" | "demo" | "practice";
export const modeNames: Record<TrainingMode, string> = {
  work: "Рабочая смена",
  tutorial: "Знакомство",
  demo: "Демонстрация",
  practice: "Целевая практика",
};
export interface PendingAction {
  id: string;
  action_id: string;
  label: string;
  incident_id: string | null;
  zone_id: string | null;
  started_at_seconds: number;
  completes_at_seconds: number;
  interruptible: boolean;
}
export interface Training extends Simulation {
  engine_version: 2;
  mode: TrainingMode;
  source_id: string | null;
  assignment_id: string | null;
  reward_eligible: boolean;
  pending_action: PendingAction | null;
  replay: boolean;
}
export interface Evidence {
  id: string;
  competency_id: string;
  title: string;
  met: boolean | null;
  explanation: string;
  source: string;
  source_version: string;
  evidence_event_ids: string[];
}
export interface TrainingDebrief extends SimulationDebrief {
  simulation: Training;
  assessment: {
    criteria: Evidence[];
    methodology_version: string;
    source_notice: string;
  };
}
export interface Learning {
  competencies: {
    id: string;
    title: string;
    score: number | null;
    evidence_count: number;
    status: "insufficient" | "developing" | "steady";
    strong: boolean;
  }[];
  patterns: { id: string; title: string; count: number; explanation: string }[];
  recommendations: {
    competency_id: string;
    title: string;
    explanation: string;
  }[];
  statistics: { completed_runs: number; completed_scenarios: number };
  source_notice: string;
}
export interface Access {
  role: "employee" | "instructor" | "methodist";
  group_id: string | null;
}
export interface Assignment {
  id: string;
  title: string;
  mode: "work" | "demo";
  created_at: string;
  member_count: number;
  completed_count: number;
}
export interface AssignmentDetail {
  id: string;
  title: string;
  mode: string;
  members: {
    employee_id: string;
    display_name: string;
    run_id: string | null;
    status: string;
    metrics: Simulation["metrics"] | null;
  }[];
  difficulties: { title: string; count: number }[];
}
export interface InstructorComment {
  id: string;
  event_id: string;
  text: string;
  author_name: string;
  created_at: string;
}
export interface Comparison {
  source_id: string;
  source_metrics: Record<string, number>;
  current_metrics: Record<string, number>;
  differences: { metric: string; delta: number }[];
  source_resolved: number;
  current_resolved: number;
}
export type ObjectValue = Record<string, unknown>;
export function check(v: unknown): asserts v {
  if (!v) throw new ContractError();
}
export function object(v: unknown): ObjectValue {
  check(v && typeof v === "object" && !Array.isArray(v));
  return v as ObjectValue;
}
export function text(v: unknown): asserts v is string {
  check(typeof v === "string");
}
export function number(v: unknown, min = 0): asserts v is number {
  check(typeof v === "number" && Number.isFinite(v) && v >= min);
}
export function list(v: unknown): unknown[] {
  check(Array.isArray(v));
  return v;
}
export function nullable(v: unknown, test: (v: unknown) => void) {
  if (v !== null) test(v);
}
export function parseTraining(v: unknown): Training {
  parseSimulation(v);
  const s = object(v);
  check(s.engine_version === 2);
  check(["work", "tutorial", "demo", "practice"].includes(String(s.mode)));
  nullable(s.source_id, text);
  nullable(s.assignment_id, text);
  check(
    typeof s.reward_eligible === "boolean" && typeof s.replay === "boolean",
  );
  nullable(s.pending_action, (v) => {
    const p = object(v);
    ["id", "action_id", "label"].forEach((k) => text(p[k]));
    nullable(p.incident_id, text);
    nullable(p.zone_id, text);
    number(p.started_at_seconds);
    number(p.completes_at_seconds);
    check(p.completes_at_seconds > p.started_at_seconds);
    check(typeof p.interruptible === "boolean");
  });
  for (const a of [
    ...list(s.actions),
    ...list(s.incidents).flatMap((i) => list(object(i).actions)),
  ])
    number(object(a).duration_seconds);
  check(!s.source_id || !s.reward_eligible);
  return v as Training;
}
export function parseTrainingCurrent(v: unknown): Training | null {
  const s = object(v);
  return s.simulation === null ? null : parseTraining(s.simulation);
}
export function parseTrainingDebrief(v: unknown): TrainingDebrief {
  parseSimulationDebrief(v);
  const s = object(v);
  parseTraining(s.simulation);
  const a = object(s.assessment);
  text(a.methodology_version);
  text(a.source_notice);
  for (const raw of list(a.criteria)) {
    const e = object(raw);
    [
      "id",
      "competency_id",
      "title",
      "explanation",
      "source",
      "source_version",
    ].forEach((k) => text(e[k]));
    check(e.met === null || typeof e.met === "boolean");
    list(e.evidence_event_ids).forEach(text);
  }
  return v as TrainingDebrief;
}
export function parseLearning(v: unknown): Learning {
  const s = object(v);
  text(s.source_notice);
  for (const raw of list(s.competencies)) {
    const c = object(raw);
    text(c.id);
    text(c.title);
    nullable(c.score, (v) => {
      number(v);
      check(v <= 100);
    });
    number(c.evidence_count);
    check(["insufficient", "developing", "steady"].includes(String(c.status)));
    check(typeof c.strong === "boolean");
  }
  for (const raw of list(s.patterns)) {
    const p = object(raw);
    ["id", "title", "explanation"].forEach((k) => text(p[k]));
    number(p.count);
  }
  for (const raw of list(s.recommendations)) {
    const r = object(raw);
    ["competency_id", "title", "explanation"].forEach((k) => text(r[k]));
  }
  const stat = object(s.statistics);
  number(stat.completed_runs);
  number(stat.completed_scenarios);
  return v as Learning;
}
export function parseAccess(v: unknown): Access {
  const s = object(v);
  check(["employee", "instructor", "methodist"].includes(String(s.role)));
  nullable(s.group_id, text);
  return v as Access;
}
export function parseAssignments(v: unknown): Assignment[] {
  const items = list(object(v).items);
  for (const raw of items) {
    const a = object(raw);
    ["id", "title", "created_at"].forEach((k) => text(a[k]));
    check(a.mode === "work" || a.mode === "demo");
    number(a.member_count);
    number(a.completed_count);
  }
  return items as Assignment[];
}
export function parseAssignment(v: unknown): AssignmentDetail {
  const a = object(v);
  ["id", "title", "mode"].forEach((k) => text(a[k]));
  for (const raw of list(a.members)) {
    const m = object(raw);
    ["employee_id", "display_name", "status"].forEach((k) => text(m[k]));
    nullable(m.run_id, text);
    nullable(m.metrics, (v) => {
      const m = object(v);
      [
        "safety",
        "service",
        "regulation",
        "prioritization",
        "communication",
      ].forEach((k) => number(m[k]));
    });
  }
  for (const raw of list(a.difficulties)) {
    const d = object(raw);
    text(d.title);
    number(d.count);
  }
  return v as AssignmentDetail;
}
export function parseComments(v: unknown): InstructorComment[] {
  const items = list(object(v).items);
  items.forEach((raw) => {
    const c = object(raw);
    ["id", "event_id", "text", "author_name", "created_at"].forEach((k) =>
      text(c[k]),
    );
  });
  return items as InstructorComment[];
}
export function parseComparison(v: unknown): Comparison {
  const c = object(v);
  text(c.source_id);
  object(c.source_metrics);
  object(c.current_metrics);
  number(c.source_resolved);
  number(c.current_resolved);
  list(c.differences).forEach((raw) => {
    const d = object(raw);
    text(d.metric);
    number(d.delta, -100);
  });
  return v as Comparison;
}
