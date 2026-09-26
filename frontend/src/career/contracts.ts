import { ContractError } from "../runner/api";

export type ReadResource = (
  path: string,
  signal: AbortSignal,
) => Promise<unknown>;
export type Scope = "brigade" | "depot" | "company";
export interface Competency {
  competency_id: string;
  value: number;
}
export interface Achievement {
  id: string;
  name: string;
  description: string;
  current: number;
  target: number;
  unlocked: boolean;
  unlocked_at: string | null;
  session_id: string | null;
}
export interface Progress {
  id: string;
  display_name: string;
  organization: {
    company_id: string | null;
    depot_id: string | null;
    brigade_id: string | null;
    company_name: string;
    depot_name: string;
    brigade_name: string;
  };
  xp: number;
  level: number;
  level_start_xp: number;
  next_level_xp: number;
  completed_sessions: number;
  rule_version: number;
  competencies: Competency[];
  achievements: Achievement[];
  reward: {
    session_id: string;
    xp: number;
    competencies: Competency[];
    unlocks: string[];
  } | null;
}
export interface Board {
  scope: Scope;
  group_name: string;
  assigned: boolean;
  total: number;
  limit: number;
  offset: number;
  items: {
    rank: number;
    employee_id: string;
    display_name: string;
    xp: number;
    level: number;
    completed_sessions: number;
    is_me: boolean;
  }[];
}
export interface Persona {
  id: string;
  display_name: string;
  company_id: string;
  depot_id: string;
  brigade_id: string;
}
function check(ok: unknown): asserts ok {
  if (!ok) throw new ContractError();
}
function obj(value: unknown): Record<string, unknown> {
  check(value !== null && typeof value === "object" && !Array.isArray(value));
  return value as Record<string, unknown>;
}
function text(value: unknown) {
  check(typeof value === "string" && value.length > 0);
}
function int(value: unknown, min = 0): asserts value is number {
  check(
    typeof value === "number" && Number.isSafeInteger(value) && value >= min,
  );
}
function list(value: unknown): unknown[] {
  check(Array.isArray(value));
  return value;
}
function dateOrNull(value: unknown) {
  check(
    value === null ||
      (typeof value === "string" && Number.isFinite(Date.parse(value))),
  );
}
function competencies(value: unknown) {
  for (const raw of list(value)) {
    const item = obj(raw);
    text(item.competency_id);
    int(item.value);
  }
}

export function parseProgress(value: unknown): Progress {
  const p = obj(value);
  text(p.id);
  text(p.display_name);
  for (const key of ["xp", "level_start_xp", "completed_sessions"]) int(p[key]);
  int(p.level, 1);
  int(p.next_level_xp, 1);
  check(p.rule_version === 1);
  check(
    Number(p.level_start_xp) <= Number(p.xp) &&
      Number(p.xp) < Number(p.next_level_xp),
  );
  const o = obj(p.organization);
  for (const key of ["company", "depot", "brigade"]) {
    text(o[`${key}_name`]);
    if (o[`${key}_id`] !== null) text(o[`${key}_id`]);
  }
  competencies(p.competencies);
  const ids = new Set<string>();
  for (const raw of list(p.achievements)) {
    const a = obj(raw);
    text(a.id);
    text(a.name);
    text(a.description);
    int(a.current);
    int(a.target, 1);
    check(a.current <= a.target);
    check(typeof a.unlocked === "boolean");
    dateOrNull(a.unlocked_at);
    check(a.unlocked === (a.unlocked_at !== null));
    if (a.session_id !== null) text(a.session_id);
    check(a.unlocked === (a.session_id !== null));
    check(!ids.has(String(a.id)));
    ids.add(String(a.id));
  }
  if (p.reward !== null) {
    const r = obj(p.reward);
    text(r.session_id);
    int(r.xp);
    competencies(r.competencies);
    for (const id of list(r.unlocks)) {
      text(id);
      check(ids.has(String(id)));
    }
  }
  return value as Progress;
}
export function parseBoard(value: unknown): Board {
  const p = obj(value);
  check(["brigade", "depot", "company"].includes(String(p.scope)));
  text(p.group_name);
  check(typeof p.assigned === "boolean");
  int(p.total);
  int(p.limit, 1);
  int(p.offset);
  const items = list(p.items);
  check(items.length <= p.limit);
  for (const raw of items) {
    const item = obj(raw);
    int(item.rank, 1);
    text(item.employee_id);
    text(item.display_name);
    int(item.xp);
    int(item.level, 1);
    int(item.completed_sessions, 1);
    check(typeof item.is_me === "boolean");
  }
  return value as Board;
}
export function parsePersonas(value: unknown): Persona[] {
  for (const raw of list(value)) {
    const p = obj(raw);
    for (const key of [
      "id",
      "display_name",
      "company_id",
      "depot_id",
      "brigade_id",
    ])
      text(p[key]);
  }
  return value as Persona[];
}
export function competencyName(id: string) {
  return (
    (
      {
        communication: "Коммуникация",
        regulation: "Соблюдение регламента",
        coordination: "Координация",
        service: "Сервис",
      } as Record<string, string>
    )[id] ?? id
  );
}
