import { describe, expect, it, vi } from "vitest";
import {
  parseSessionState,
  parseSessionResult,
  parseCatalogPage,
  request,
} from "./api";
import { active, page } from "./testFixtures";

describe("API contract boundary", () => {
  it("accepts the server snapshot without calculating scores", () => {
    expect(parseSessionState(active).session.scores[0].value).toBe(50);
  });
  it.each([
    { ...active, expected_sequence: "0" },
    { ...active, deadline: "tomorrow" },
    { ...active, current_node: { ...active.current_node, terminal: "false" } },
    { ...active, session: { ...active.session, status: "unknown" } },
    {
      ...active,
      session: {
        ...active.session,
        scores: [{ metric: "safety_rating", competency_id: null, value: "50" }],
      },
    },
    {
      ...active,
      session: {
        ...active.session,
        scores: [
          { metric: ["passenger_loyalty"], competency_id: null, value: 50 },
          active.session.scores[1],
        ],
      },
    },
    { ...active, expected_sequence: 9 },
  ])(
    "rejects a malformed state instead of rendering invented values",
    (bad) => {
      expect(() => parseSessionState(bad)).toThrow();
    },
  );
  it("rejects malformed catalog and incomplete result", () => {
    expect(parseCatalogPage(page).items[0].id).toBe("demo-service-situation");
    expect(() =>
      parseCatalogPage({
        ...page,
        items: [{ ...page.items[0], version: false }],
      }),
    ).toThrow();
    expect(() =>
      parseSessionResult({ summary: {}, session: active.session }),
    ).toThrow();
  });
  it("times out the whole response including a stalled body", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", async () => ({
      ok: true,
      json: () => new Promise(() => {}),
    }));
    const task = request("/sessions/1", {}, new AbortController().signal);
    const outcome = task.catch((error) => error);
    await vi.advanceTimersByTimeAsync(8000);
    expect(
      await Promise.race([outcome, Promise.resolve("still pending")]),
    ).toBeInstanceOf(Error);
  });
  it("discards a response whose read was aborted while its body arrived", async () => {
    let deliver!: (value: unknown) => void;
    vi.stubGlobal("fetch", async () => ({
      ok: true,
      json: () =>
        new Promise((resolve) => {
          deliver = resolve;
        }),
    }));
    const controller = new AbortController();
    const task = request("/sessions/1", {}, controller.signal).catch(
      (error) => error,
    );
    await vi.waitFor(() => expect(deliver).toBeTypeOf("function"));
    controller.abort();
    deliver(active);
    expect(await task).toMatchObject({ name: "AbortError" });
  });
});
