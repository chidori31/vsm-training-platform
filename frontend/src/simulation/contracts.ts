import { ContractError } from "../runner/api";

export interface Action {
  duration_seconds?: number;
  id: string;
  label: string;
  description: string;
  enabled: boolean;
  reason: string | null;
  incident_id: string | null;
  zone_id: string | null;
}
export interface JournalEntry {
  id: string;
  at_seconds: number;
  kind: string;
  incident_id: string | null;
  title: string;
  explanation: string;
  metric_changes: {
    metric: string;
    delta: number;
    before: number;
    after: number;
  }[];
}
export interface Incident {
  id: string;
  title: string;
  kind: string;
  zone_id: string;
  passenger_id: string | null;
  status: string;
  severity: number;
  reported_at_seconds: number;
  discovered_at_seconds: number | null;
  first_reaction_seconds: number | null;
  observation: string;
  facts: string[];
  actions: Action[];
}
export interface Simulation {
  id: string;
  title: string;
  status: "active" | "completed";
  revision: number;
  server_time: string;
  started_at: string;
  elapsed_seconds: number;
  duration_seconds: number;
  location: string;
  zones: { id: string; title: string; kind: string }[];
  stations: {
    id: string;
    title: string;
    arrival_seconds: number;
    departure_seconds: number;
    status: "upcoming" | "dwell" | "passed";
  }[];
  passengers: {
    id: string;
    name: string;
    zone_id: string;
    observation: string;
  }[];
  incidents: Incident[];
  actions: Action[];
  equipment: {
    id: string;
    title: string;
    zone_id: string;
    carried: boolean;
    available: boolean;
  }[];
  communications: {
    id: string;
    incident_id: string;
    type: string;
    title: string;
    status: "pending" | "answered";
    requested_at_seconds: number;
    expected_response_seconds: number;
    result: string | null;
  }[];
  metrics: {
    safety: number;
    service: number;
    regulation: number;
    prioritization: number;
    communication: number;
    average_reaction_seconds: number | null;
  };
  xp: number;
  journal: JournalEntry[];
}
export interface SimulationDebrief {
  simulation: Simulation;
  summary: string;
  incidents: {
    id: string;
    title: string;
    outcome: string;
    reported_at_seconds: number;
    discovered_at_seconds: number | null;
    first_reaction_seconds: number | null;
    resolved_at_seconds: number | null;
    alternatives: string[];
  }[];
  recommendations: string[];
  achievements: string[];
}
type Obj = Record<string, unknown>;
function check(v: unknown): asserts v {
  if (!v) throw new ContractError();
}
function obj(v: unknown): Obj {
  check(v && typeof v === "object" && !Array.isArray(v));
  return v as Obj;
}
function str(v: unknown): asserts v is string {
  check(typeof v === "string");
}
function num(v: unknown, min = 0): asserts v is number {
  check(typeof v === "number" && Number.isFinite(v) && v >= min);
}
function nullable(v: unknown, test: (value: unknown) => void) {
  if (v !== null) test(v);
}
function list(v: unknown, test: (value: unknown) => void) {
  check(Array.isArray(v));
  v.forEach(test);
}
function fields(o: Obj, names: string[]) {
  names.forEach((k) => str(o[k]));
}
function action(v: unknown) {
  const a = obj(v);
  fields(a, ["id", "label", "description"]);
  check(typeof a.enabled === "boolean");
  ["reason", "incident_id", "zone_id"].forEach((k) => nullable(a[k], str));
}
export function parseSimulation(v: unknown): Simulation {
  const s = obj(v);
  fields(s, ["id", "title", "server_time", "started_at", "location"]);
  check(
    Number.isFinite(Date.parse(s.server_time as string)) &&
      Number.isFinite(Date.parse(s.started_at as string)),
  );
  check(s.status === "active" || s.status === "completed");
  ["elapsed_seconds", "duration_seconds", "revision", "xp"].forEach((k) =>
    num(s[k]),
  );
  check(
    Number.isSafeInteger(s.revision) &&
      (s.duration_seconds as number) > 0 &&
      (s.elapsed_seconds as number) <= (s.duration_seconds as number),
  );
  list(s.zones, (v) => fields(obj(v), ["id", "title", "kind"]));
  check((s.zones as Obj[]).some((z) => z.id === s.location));
  list(s.stations, (v) => {
    const x = obj(v);
    fields(x, ["id", "title"]);
    num(x.arrival_seconds);
    num(x.departure_seconds);
    check(
      x.departure_seconds >= x.arrival_seconds &&
        ["upcoming", "dwell", "passed"].includes(x.status as string),
    );
  });
  list(s.passengers, (v) =>
    fields(obj(v), ["id", "name", "zone_id", "observation"]),
  );
  list(s.incidents, (v) => {
    const x = obj(v);
    fields(x, ["id", "title", "kind", "zone_id", "status", "observation"]);
    nullable(x.passenger_id, str);
    num(x.severity);
    num(x.reported_at_seconds);
    nullable(x.discovered_at_seconds, num);
    nullable(x.first_reaction_seconds, num);
    list(x.facts, str);
    list(x.actions, action);
  });
  list(s.actions, action);
  list(s.equipment, (v) => {
    const x = obj(v);
    fields(x, ["id", "title", "zone_id"]);
    check(typeof x.carried === "boolean" && typeof x.available === "boolean");
  });
  list(s.communications, (v) => {
    const x = obj(v);
    fields(x, ["id", "incident_id", "type", "title"]);
    check(["pending", "answered"].includes(x.status as string));
    num(x.requested_at_seconds);
    num(x.expected_response_seconds);
    nullable(x.result, str);
  });
  const m = obj(s.metrics);
  for (const k of [
    "safety",
    "service",
    "regulation",
    "prioritization",
    "communication",
  ]) {
    num(m[k]);
    check(m[k] <= 100);
  }
  nullable(m.average_reaction_seconds, num);
  list(s.journal, (v) => {
    const x = obj(v);
    fields(x, ["id", "kind", "title", "explanation"]);
    num(x.at_seconds);
    nullable(x.incident_id, str);
    list(x.metric_changes, (v) => {
      const c = obj(v);
      str(c.metric);
      num(c.delta, -100);
      num(c.before);
      num(c.after);
    });
  });
  return v as Simulation;
}
export function parseCurrentSimulation(v: unknown): Simulation | null {
  const x = obj(v);
  return x.simulation === null ? null : parseSimulation(x.simulation);
}
export function parseSimulationDebrief(v: unknown): SimulationDebrief {
  const x = obj(v);
  parseSimulation(x.simulation);
  check(obj(x.simulation).status === "completed");
  str(x.summary);
  list(x.recommendations, str);
  list(x.achievements, str);
  list(x.incidents, (v) => {
    const i = obj(v);
    fields(i, ["id", "title", "outcome"]);
    num(i.reported_at_seconds);
    [
      "discovered_at_seconds",
      "first_reaction_seconds",
      "resolved_at_seconds",
    ].forEach((k) => nullable(i[k], num));
    list(i.alternatives, str);
  });
  return v as SimulationDebrief;
}

export const metricNames: Record<string, string> = {
  safety: "Безопасность",
  service: "Сервис",
  regulation: "Регламент",
  prioritization: "Приоритеты",
  communication: "Коммуникация",
};
export function clock(seconds: number) {
  const n = Math.max(0, Math.floor(seconds));
  return `${Math.floor(n / 60)
    .toString()
    .padStart(2, "0")}:${(n % 60).toString().padStart(2, "0")}`;
}
export function incidentStage(status: string) {
  return (
    (
      {
        reported: "Новое обращение",
        investigated: "Осмотрено",
        confirmed: "Подтверждено",
        handled: "Помощь организована",
        resolved: "Завершено",
        ignored: "Ожидает реакции",
        escalated: "Ухудшение",
        critical: "Критическая ситуация",
      } as Record<string, string>
    )[status] ?? status
  );
}
