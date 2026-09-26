import { ContractError } from "../runner/api";

export interface ChallengeScenario {
  id: string;
  version: number;
  title: string;
  completed: boolean;
}
export interface Challenge {
  id: string;
  title: string;
  description: string;
  starts_at: string;
  expires_at: string;
  target: number;
  progress: number;
  status: "scheduled" | "active" | "completed" | "expired";
  scenarios: ChallengeScenario[];
}
export interface Notification {
  id: string;
  kind:
    | "new_scenario"
    | "challenge_started"
    | "challenge_ending"
    | "achievement_unlocked";
  title: string;
  body: string;
  created_at: string;
  expires_at: string;
  read_at: string | null;
  expired: boolean;
}
export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
  server_time: string;
}
export interface Inbox extends Page<Notification> {
  unread_count: number;
}
export type WriteRead = (
  id: string,
  read: boolean,
  signal: AbortSignal,
) => Promise<unknown>;

function check(condition: unknown): asserts condition {
  if (!condition) throw new ContractError();
}
function object(value: unknown): Record<string, unknown> {
  check(value && typeof value === "object" && !Array.isArray(value));
  return value as Record<string, unknown>;
}
function text(value: unknown): asserts value is string {
  check(typeof value === "string" && value.trim().length > 0);
}
function integer(value: unknown, minimum = 0): asserts value is number {
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
function page(value: unknown): Record<string, unknown> & { items: unknown[] } {
  const data = object(value);
  integer(data.total);
  integer(data.limit, 1);
  integer(data.offset);
  date(data.server_time);
  check(
    Array.isArray(data.items) &&
      data.items.length <= data.limit &&
      data.items.length <= data.total,
  );
  return { ...data, items: data.items };
}
export function parseChallenges(value: unknown): Page<Challenge> {
  const data = page(value);
  for (const item of data.items) {
    const row = object(item);
    text(row.id);
    text(row.title);
    text(row.description);
    date(row.starts_at);
    date(row.expires_at);
    check(Date.parse(row.expires_at) > Date.parse(row.starts_at));
    integer(row.target, 1);
    integer(row.progress);
    check(row.progress <= row.target);
    check(
      ["scheduled", "active", "completed", "expired"].includes(
        String(row.status),
      ),
    );
    check((row.status === "completed") === (row.progress === row.target));
    check(Array.isArray(row.scenarios) && row.target <= row.scenarios.length);
    const ids = new Set<string>();
    for (const item of row.scenarios) {
      const target = object(item);
      text(target.id);
      text(target.title);
      integer(target.version, 1);
      check(typeof target.completed === "boolean" && !ids.has(target.id));
      ids.add(target.id);
    }
  }
  return value as Page<Challenge>;
}
export function parseNotification(value: unknown): Notification {
  const row = object(value);
  text(row.id);
  text(row.title);
  text(row.body);
  check(
    [
      "new_scenario",
      "challenge_started",
      "challenge_ending",
      "achievement_unlocked",
    ].includes(String(row.kind)),
  );
  date(row.created_at);
  date(row.expires_at);
  check(Date.parse(row.expires_at) > Date.parse(row.created_at));
  if (row.read_at !== null) {
    date(row.read_at);
    check(Date.parse(row.read_at) >= Date.parse(row.created_at));
  }
  check(typeof row.expired === "boolean");
  return value as Notification;
}
export function parseNotifications(value: unknown): Inbox {
  const data = page(value);
  integer(data.unread_count);
  check(data.unread_count <= Number(data.total));
  data.items.forEach(parseNotification);
  return value as Inbox;
}
