import { StrictMode } from "react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useScenarioRunner } from "./useScenarioRunner";
import {
  active,
  apiError,
  completedResult,
  completedState,
  login,
  nextState,
  page,
  scenario,
} from "./testFixtures";

type Request = { path: string; init: RequestInit };
function server(
  handler?: (request: Request) => Response | Promise<Response> | undefined,
) {
  const requests: Request[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const request = { path: String(url), init };
      requests.push(request);
      const custom = await handler?.(request);
      if (custom) return custom;
      if (url.endsWith("/auth/demo")) return Response.json(login);
      if (url.includes("/scenarios?")) return Response.json(page);
      if (url.endsWith("/sessions") && init.method === "POST")
        return Response.json(active, { status: 201 });
      if (url.endsWith("/session-1")) return Response.json(active);
      throw new Error(`Unexpected request ${url}`);
    }),
  );
  return requests;
}
beforeEach(() => sessionStorage.clear());
describe("client storage is not authoritative", () => {
  it.each([
    { decision_id: `id${String.fromCharCode(0)}` },
    { node_id: "request\n" },
    { expected_sequence: 2147483648 },
  ])(
    "discards malformed persisted commands without submitting them: %j",
    async (change) => {
      sessionStorage.setItem(
        "vsm.runner.v1",
        JSON.stringify({
          sessionId: "session-1",
          start: null,
          decision: {
            decision_id: "one",
            node_id: "request",
            choice_id: "explain",
            expected_sequence: 0,
            ...change,
          },
        }),
      );
      const requests = server();
      const { result } = renderHook(useScenarioRunner);
      await waitFor(() => expect(result.current.phase).toBe("ready"));
      expect(requests.some((r) => r.path.includes("/sessions"))).toBe(false);
    },
  );
  it("strips forged score and effect fields from a recoverable decision", async () => {
    const command = {
      decision_id: "one",
      node_id: "request",
      choice_id: "explain",
      expected_sequence: 0,
    };
    sessionStorage.setItem(
      "vsm.runner.v1",
      JSON.stringify({
        sessionId: "session-1",
        start: null,
        decision: {
          ...command,
          loyalty: 999,
          safety: 999,
          xp: 999,
          effects: [{ delta: 999 }],
          destination: "done",
        },
        scores: [999],
      }),
    );
    const requests = server(({ path }) =>
      path.endsWith("/decisions")
        ? Response.json({
            ...nextState("one"),
            outcome: "accepted",
            acknowledged_decision_id: "one",
          })
        : undefined,
    );
    const { result } = renderHook(useScenarioRunner);
    await waitFor(() => expect(result.current.phase).toBe("active"));
    const sent = requests.find((r) => r.path.endsWith("/decisions"));
    expect(JSON.parse(String(sent?.init.body))).toEqual(command);
    expect(
      result.current.state?.session.scores.every((item) => item.value !== 999),
    ).toBe(true);
  });
});
describe("resilient runner", () => {
  it("switches an idle demo persona and renews that same identity after expiry", async () => {
    let expired = false;
    const requests = server(({ path, init }) => {
      if (path.endsWith("/auth/demo")) {
        const id =
          JSON.parse(String(init.body || "{}")).persona_id || "demo-employee";
        return Response.json({
          ...login,
          access_token: `token-${id}`,
          profile: { id, display_name: id },
        });
      }
      if (path.endsWith("/profiles/me/progress")) {
        if (!expired) {
          expired = true;
          return Response.json(apiError("unauthorized"), { status: 401 });
        }
        return Response.json({ id: "demo-north-02", xp: 55 });
      }
    });
    const { result } = renderHook(useScenarioRunner);
    await waitFor(() => expect(result.current.phase).toBe("ready"));
    await act(() => result.current.switchPersona("demo-north-02"));
    expect(result.current.identity?.id).toBe("demo-north-02");
    const progress = await result.current.readResource(
      "/profiles/me/progress",
      new AbortController().signal,
    );
    expect(progress).toEqual({ id: "demo-north-02", xp: 55 });
    const auth = requests.filter((r) => r.path.endsWith("/auth/demo"));
    expect(JSON.parse(String(auth.at(-1)?.init.body)).persona_id).toBe(
      "demo-north-02",
    );
  });

  it("refuses identity switching while a session is active or a command is uncertain", async () => {
    const requests = server();
    const { result } = renderHook(useScenarioRunner);
    await waitFor(() => expect(result.current.phase).toBe("ready"));
    await act(() => result.current.start(scenario));
    const before = sessionStorage.getItem("vsm.runner.v1");
    await act(() => result.current.switchPersona("demo-north-02"));
    expect(result.current.state?.session.id).toBe("session-1");
    expect(sessionStorage.getItem("vsm.runner.v1")).toBe(before);
    expect(requests.filter((r) => r.path.endsWith("/auth/demo"))).toHaveLength(
      1,
    );
  });
  it("authenticates and loads catalog without automatically starting an attempt", async () => {
    const requests = server();
    const { result } = renderHook(useScenarioRunner);
    await waitFor(() => expect(result.current.phase).toBe("ready"));
    expect(result.current.catalog[0].title).toBe("Работа с пассажиром");
    expect(requests.filter((r) => r.path.endsWith("/sessions"))).toHaveLength(
      0,
    );
    expect(localStorage.length).toBe(0);
  });
  it("replays the same persisted start key after a lost acknowledgement", async () => {
    let lost = true;
    const requests = server(({ path, init }) => {
      if (path.endsWith("/sessions") && init.method === "POST" && lost) {
        lost = false;
        throw new TypeError("lost acknowledgement");
      }
    });
    const { result } = renderHook(useScenarioRunner);
    await waitFor(() => expect(result.current.phase).toBe("ready"));
    await act(() => result.current.start(scenario));
    expect(result.current.busy).toBe(true);
    expect(result.current.error).toBeTruthy();
    await act(() => result.current.reconnect());
    expect(result.current.state?.session.id).toBe("session-1");
    expect(result.current.busy).toBe(false);
    const starts = requests.filter((r) => r.path.endsWith("/sessions"));
    expect(starts).toHaveLength(2);
    expect(new Headers(starts[0].init.headers).get("Idempotency-Key")).toBe(
      new Headers(starts[1].init.headers).get("Idempotency-Key"),
    );
  });
  it("locks choices and replays the exact decision after a lost response", async () => {
    let lost = true;
    let accepted = active as unknown;
    const requests = server(({ path, init }) => {
      if (path.endsWith("/decisions")) {
        const command = JSON.parse(String(init.body));
        accepted = nextState(command.decision_id);
        if (lost) {
          lost = false;
          throw new TypeError("lost response");
        }
        return Response.json({
          ...(accepted as object),
          outcome: "duplicate",
          acknowledged_decision_id: command.decision_id,
        });
      }
      if (path.endsWith("/session-1")) return Response.json(accepted);
    });
    const { result } = renderHook(useScenarioRunner);
    await waitFor(() => expect(result.current.phase).toBe("ready"));
    await act(() => result.current.start(scenario));
    await act(() => result.current.choose("explain"));
    expect(result.current.pendingChoiceId).toBe("explain");
    expect(result.current.busy).toBe(true);
    await act(() => result.current.choose("ignore"));
    await act(() => result.current.reconnect());
    const decisions = requests.filter((r) => r.path.endsWith("/decisions"));
    expect(decisions).toHaveLength(2);
    expect(decisions[0].init.body).toBe(decisions[1].init.body);
    expect(result.current.state?.expected_sequence).toBe(1);
    expect(result.current.state?.session.decisions).toHaveLength(1);
    expect(result.current.pendingChoiceId).toBeNull();
  });
  it("adopts the authoritative timeout conflict and releases the old choice", async () => {
    server(({ path }) =>
      path.endsWith("/decisions")
        ? Response.json(
            apiError("decision_timed_out", nextState("timeout:1", true)),
            { status: 409 },
          )
        : undefined,
    );
    const { result } = renderHook(useScenarioRunner);
    await waitFor(() => expect(result.current.phase).toBe("ready"));
    await act(() => result.current.start(scenario));
    await act(() => result.current.choose("explain"));
    expect(result.current.state?.current_node.id).toBe("alternative");
    expect(result.current.busy).toBe(false);
    expect(result.current.state?.session.decisions[0].choice_id).toBe(
      "__timeout__",
    );
  });
  it("resumes the saved session through GET on refresh and reuses the demo token", async () => {
    const requests = server();
    const first = renderHook(useScenarioRunner);
    await waitFor(() => expect(first.result.current.phase).toBe("ready"));
    await act(() => first.result.current.start(scenario));
    first.unmount();
    const resumed = renderHook(useScenarioRunner);
    await waitFor(() => expect(resumed.result.current.phase).toBe("active"));
    expect(requests.filter((r) => r.path.endsWith("/auth/demo"))).toHaveLength(
      1,
    );
    expect(requests.filter((r) => r.path.endsWith("/session-1"))).toHaveLength(
      1,
    );
    expect(requests.filter((r) => r.path.endsWith("/sessions"))).toHaveLength(
      1,
    );
  });
  it("renews an expired server token and retries the original intent once", async () => {
    let expired = true;
    const requests = server(({ path }) => {
      if (path.endsWith("/sessions") && expired) {
        expired = false;
        return Response.json(apiError("unauthorized"), { status: 401 });
      }
    });
    const { result } = renderHook(useScenarioRunner);
    await waitFor(() => expect(result.current.phase).toBe("ready"));
    await act(() => result.current.start(scenario));
    expect(result.current.phase).toBe("active");
    expect(requests.filter((r) => r.path.endsWith("/auth/demo"))).toHaveLength(
      2,
    );
    const starts = requests.filter((r) => r.path.endsWith("/sessions"));
    expect(new Headers(starts[0].init.headers).get("Idempotency-Key")).toBe(
      new Headers(starts[1].init.headers).get("Idempotency-Key"),
    );
  });
  it("reports disabled demo login and can recover an initial connection failure", async () => {
    let disabled = true;
    server(({ path }) =>
      path.endsWith("/auth/demo") && disabled
        ? Response.json(apiError("demo_auth_disabled"), { status: 404 })
        : undefined,
    );
    const { result } = renderHook(useScenarioRunner);
    await waitFor(() =>
      expect(result.current.error).toMatch(/DEMO_AUTH_ENABLED/),
    );
    disabled = false;
    await act(() => result.current.reconnect());
    expect(result.current.phase).toBe("ready");
  });
  it("rejects malformed successful start and keeps its key for recovery", async () => {
    let malformed = true;
    server(({ path }) =>
      path.endsWith("/sessions") && malformed
        ? Response.json({ session: {} })
        : undefined,
    );
    const { result } = renderHook(useScenarioRunner);
    await waitFor(() => expect(result.current.phase).toBe("ready"));
    await act(() => result.current.start(scenario));
    expect(result.current.state).toBeNull();
    expect(result.current.busy).toBe(true);
    malformed = false;
    await act(() => result.current.reconnect());
    expect(result.current.phase).toBe("active");
  });
  it("remains usable under StrictMode cleanup", async () => {
    server();
    const { result } = renderHook(useScenarioRunner, { wrapper: StrictMode });
    await waitFor(() => expect(result.current.phase).toBe("ready"));
    await act(() => result.current.start(scenario));
    expect(result.current.phase).toBe("active");
  });
  it("replays a pending start on reload before reading the committed session", async () => {
    let lost = true;
    const requests = server(({ path }) => {
      if (path.endsWith("/sessions") && lost) {
        lost = false;
        throw new TypeError("lost ack");
      }
    });
    const first = renderHook(useScenarioRunner);
    await waitFor(() => expect(first.result.current.phase).toBe("ready"));
    await act(() => first.result.current.start(scenario));
    first.unmount();
    const second = renderHook(useScenarioRunner);
    await waitFor(() => expect(second.result.current.phase).toBe("active"));
    const starts = requests.filter((r) => r.path.endsWith("/sessions"));
    expect(starts).toHaveLength(2);
    expect(new Headers(starts[0].init.headers).get("Idempotency-Key")).toBe(
      new Headers(starts[1].init.headers).get("Idempotency-Key"),
    );
    expect(second.result.current.state?.session.id).toBe("session-1");
  });
  it("replays an uncertain decision on reload and retains one journal entry", async () => {
    let lost = true;
    let accepted: unknown = active;
    const requests = server(({ path, init }) => {
      if (path.endsWith("/decisions")) {
        const command = JSON.parse(String(init.body));
        accepted = nextState(command.decision_id);
        if (lost) {
          lost = false;
          throw new TypeError("lost ack");
        }
        return Response.json({
          ...(accepted as object),
          outcome: "duplicate",
          acknowledged_decision_id: command.decision_id,
        });
      }
      if (path.endsWith("/session-1")) return Response.json(accepted);
    });
    const first = renderHook(useScenarioRunner);
    await waitFor(() => expect(first.result.current.phase).toBe("ready"));
    await act(() => first.result.current.start(scenario));
    await act(() => first.result.current.choose("explain"));
    first.unmount();
    const second = renderHook(useScenarioRunner);
    await waitFor(() =>
      expect(second.result.current.state?.expected_sequence).toBe(1),
    );
    const decisions = requests.filter((r) => r.path.endsWith("/decisions"));
    expect(decisions).toHaveLength(2);
    expect(decisions[0].init.body).toBe(decisions[1].init.body);
    expect(second.result.current.state?.session.decisions).toHaveLength(1);
    expect(second.result.current.busy).toBe(false);
  });
  it("prioritizes a click over a slow background read and ignores its stale body", async () => {
    let deliver!: (value: unknown) => void;
    const requests = server(({ path, init }) => {
      if (path.endsWith("/session-1")) {
        const response = Response.json(active);
        vi.spyOn(response, "json").mockImplementation(
          () =>
            new Promise((resolve) => {
              deliver = resolve;
            }),
        );
        return response;
      }
      if (path.endsWith("/decisions")) {
        const command = JSON.parse(String(init.body));
        return Response.json({
          ...nextState(command.decision_id),
          outcome: "accepted",
          acknowledged_decision_id: command.decision_id,
        });
      }
    });
    const { result } = renderHook(useScenarioRunner);
    await waitFor(() => expect(result.current.phase).toBe("ready"));
    await act(() => result.current.start(scenario));
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    await waitFor(() => expect(deliver).toBeTypeOf("function"));
    expect(result.current.busy).toBe(false);
    await act(() => result.current.choose("explain"));
    await act(async () => {
      deliver(active);
      await Promise.resolve();
    });
    expect(result.current.state?.current_node.id).toBe("alternative");
    expect(result.current.state?.expected_sequence).toBe(1);
    expect(requests.filter((r) => r.path.endsWith("/decisions"))).toHaveLength(
      1,
    );
  });
  it("automatically retries the saved intent on the browser online event", async () => {
    let lost = true;
    const requests = server(({ path }) => {
      if (path.endsWith("/sessions") && lost) {
        lost = false;
        throw new TypeError("offline");
      }
    });
    const { result } = renderHook(useScenarioRunner);
    await waitFor(() => expect(result.current.phase).toBe("ready"));
    await act(() => result.current.start(scenario));
    act(() => window.dispatchEvent(new Event("online")));
    await waitFor(() => expect(result.current.phase).toBe("active"));
    expect(result.current.error).toBeNull();
    expect(requests.filter((r) => r.path.endsWith("/sessions"))).toHaveLength(
      2,
    );
  });
  it("anchors countdown to server time and reads timeout from the server at zero", async () => {
    vi.useFakeTimers();
    const requests = server(({ path }) => {
      if (path.endsWith("/sessions"))
        return Response.json({
          ...active,
          server_time: "2026-09-26T10:00:38Z",
        });
      if (path.endsWith("/session-1"))
        return Response.json(nextState("timeout:1", true));
    });
    const { result } = renderHook(useScenarioRunner);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.phase).toBe("ready");
    await act(() => result.current.start(scenario));
    expect(result.current.remainingSeconds).toBe(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(result.current.remainingSeconds).toBe(1);
    expect(result.current.state?.session.decisions).toHaveLength(0);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(result.current.state?.session.decisions[0].choice_id).toBe(
      "__timeout__",
    );
    expect(result.current.remainingSeconds).toBeNull();
    expect(requests.filter((r) => r.path.endsWith("/session-1"))).toHaveLength(
      1,
    );
  });
  it("polls critical nodes without making the choices busy", async () => {
    vi.useFakeTimers();
    const requests = server();
    const { result } = renderHook(useScenarioRunner);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(() => result.current.start(scenario));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(requests.filter((r) => r.path.endsWith("/session-1"))).toHaveLength(
      1,
    );
    expect(result.current.busy).toBe(false);
  });
  it("bounds an unresponsive command and retains it for a later reconnect", async () => {
    vi.useFakeTimers();
    server(({ path }) =>
      path.endsWith("/sessions") ? new Promise(() => {}) : undefined,
    );
    const { result } = renderHook(useScenarioRunner);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    let start!: Promise<void>;
    act(() => {
      start = result.current.start(scenario);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(8000);
      await start;
    });
    expect(result.current.error).toBeTruthy();
    expect(result.current.busy).toBe(true);
    expect(result.current.phase).toBe("starting");
  });
  it("fetches the server result after completion and starts a fresh attempt only after leaving", async () => {
    let id = "";
    const requests = server(({ path, init }) => {
      if (path.endsWith("/decisions")) {
        id = JSON.parse(String(init.body)).decision_id;
        return Response.json({
          ...completedState(id),
          outcome: "accepted",
          acknowledged_decision_id: id,
        });
      }
      if (path.endsWith("/result")) return Response.json(completedResult(id));
    });
    const { result } = renderHook(useScenarioRunner);
    await waitFor(() => expect(result.current.phase).toBe("ready"));
    await act(() => result.current.start(scenario));
    await act(() => result.current.choose("explain"));
    expect(result.current.phase).toBe("completed");
    expect(result.current.result?.summary.decision_count).toBe(1);
    await act(() => result.current.start(scenario));
    expect(requests.filter((r) => r.path.endsWith("/sessions"))).toHaveLength(
      1,
    );
    act(() => result.current.leave());
    expect(result.current.phase).toBe("ready");
    expect(result.current.state).toBeNull();
    await act(() => result.current.start(scenario));
    const starts = requests.filter((r) => r.path.endsWith("/sessions"));
    expect(starts).toHaveLength(2);
    expect(new Headers(starts[0].init.headers).get("Idempotency-Key")).not.toBe(
      new Headers(starts[1].init.headers).get("Idempotency-Key"),
    );
  });
  it("surfaces blocked browser storage without crashing or sending a start", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("Blocked", "SecurityError");
    });
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new DOMException("Blocked", "SecurityError");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("Blocked", "SecurityError");
    });
    const requests = server();
    const { result } = renderHook(useScenarioRunner);
    await waitFor(() => expect(result.current.error).toMatch(/sessionStorage/));
    expect(requests.filter((r) => r.path.endsWith("/sessions"))).toHaveLength(
      0,
    );
  });
  it("recovers an initial offline login through manual reconnect", async () => {
    let offline = true;
    server(({ path }) => {
      if (path.endsWith("/auth/demo") && offline)
        throw new TypeError("offline");
    });
    const { result } = renderHook(useScenarioRunner);
    await waitFor(() => expect(result.current.connection).toBe("offline"));
    expect(result.current.catalog).toHaveLength(0);
    offline = false;
    await act(() => result.current.reconnect());
    expect(result.current.phase).toBe("ready");
    expect(result.current.catalog).toHaveLength(1);
  });
  it("bounds reauthentication to one renewal when the server keeps rejecting the token", async () => {
    const requests = server(({ path }) =>
      path.endsWith("/sessions")
        ? Response.json(apiError("unauthorized"), { status: 401 })
        : undefined,
    );
    const { result } = renderHook(useScenarioRunner);
    await waitFor(() => expect(result.current.phase).toBe("ready"));
    await act(() => result.current.start(scenario));
    expect(result.current.error).toBeTruthy();
    expect(result.current.busy).toBe(true);
    expect(requests.filter((r) => r.path.endsWith("/sessions"))).toHaveLength(
      2,
    );
    expect(requests.filter((r) => r.path.endsWith("/auth/demo"))).toHaveLength(
      2,
    );
  });
  it("admits only one command from simultaneous choice clicks", async () => {
    const requests = server(({ path, init }) => {
      if (path.endsWith("/decisions")) {
        const command = JSON.parse(String(init.body));
        return Response.json({
          ...nextState(command.decision_id),
          outcome: "accepted",
          acknowledged_decision_id: command.decision_id,
        });
      }
    });
    const { result } = renderHook(useScenarioRunner);
    await waitFor(() => expect(result.current.phase).toBe("ready"));
    await act(() => result.current.start(scenario));
    await act(async () => {
      await Promise.all([
        result.current.choose("explain"),
        result.current.choose("ignore"),
      ]);
    });
    expect(requests.filter((r) => r.path.endsWith("/decisions"))).toHaveLength(
      1,
    );
    expect(result.current.state?.expected_sequence).toBe(1);
  });
  it("keeps an expired countdown at zero when a delayed same-node poll arrives", async () => {
    vi.useFakeTimers();
    let deliver!: (response: Response) => void;
    let reads = 0;
    server(({ path }) => {
      if (path.endsWith("/sessions"))
        return Response.json({
          ...active,
          server_time: "2026-09-26T10:00:38Z",
        });
      if (path.endsWith("/session-1")) {
        reads++;
        if (reads === 1)
          return new Promise((resolve) => {
            deliver = resolve;
          });
        return Response.json(nextState("timeout:1", true));
      }
    });
    const { result } = renderHook(useScenarioRunner);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(() => result.current.start(scenario));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(result.current.remainingSeconds).toBe(0);
    expect(result.current.state?.session.decisions).toHaveLength(0);
    await act(async () => {
      deliver(
        Response.json({ ...active, server_time: "2026-09-26T10:00:39Z" }),
      );
      await Promise.resolve();
    });
    expect(result.current.remainingSeconds).toBe(0);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(result.current.state?.session.decisions[0].choice_id).toBe(
      "__timeout__",
    );
  });
  it.each([
    "{",
    "null",
    JSON.stringify({
      sessionId: "session-1",
      start: null,
      decision: {
        decision_id: "",
        node_id: "request",
        choice_id: "explain",
        expected_sequence: 0,
      },
    }),
  ])(
    "discards malformed persisted intent without sending it",
    async (saved) => {
      sessionStorage.setItem("vsm.runner.v1", saved);
      const requests = server();
      const { result } = renderHook(useScenarioRunner);
      await waitFor(() => expect(result.current.phase).toBe("ready"));
      expect(result.current.state).toBeNull();
      expect(result.current.busy).toBe(false);
      expect(requests.some((r) => r.path.includes("/sessions"))).toBe(false);
    },
  );
  it("ignores late authentication from an unmounted runner", async () => {
    let deliver!: (value: unknown) => void;
    let logins = 0;
    const requests = server(({ path }) => {
      if (path.endsWith("/auth/demo")) {
        logins++;
        if (logins === 1) {
          const response = Response.json(login);
          vi.spyOn(response, "json").mockImplementation(
            () =>
              new Promise((resolve) => {
                deliver = resolve;
              }),
          );
          return response;
        }
        return Response.json({ ...login, access_token: "fresh-token" });
      }
    });
    const first = renderHook(useScenarioRunner);
    await waitFor(() => expect(deliver).toBeTypeOf("function"));
    first.unmount();
    const second = renderHook(useScenarioRunner);
    await waitFor(() => expect(second.result.current.phase).toBe("ready"));
    await act(async () => {
      deliver({ ...login, access_token: "stale-token" });
      await Promise.resolve();
    });
    await act(() => second.result.current.start(scenario));
    const start = requests.find((r) => r.path.endsWith("/sessions"))!;
    expect(new Headers(start.init.headers).get("Authorization")).toBe(
      "Bearer fresh-token",
    );
    expect(
      JSON.parse(sessionStorage.getItem("vsm.demo-token.v1")!).access_token,
    ).toBe("fresh-token");
  });
  it("retains a lost-ack start key through persistent authentication failure", async () => {
    let mode: "lost" | "unauthorized" | "ok" = "lost";
    const requests = server(({ path }) => {
      if (path.endsWith("/sessions")) {
        if (mode === "lost") {
          mode = "unauthorized";
          throw new TypeError("ack lost after commit");
        }
        if (mode === "unauthorized")
          return Response.json(apiError("unauthorized"), { status: 401 });
      }
    });
    const { result } = renderHook(useScenarioRunner);
    await waitFor(() => expect(result.current.phase).toBe("ready"));
    await act(() => result.current.start(scenario));
    await act(() => result.current.reconnect());
    expect(result.current.busy).toBe(true);
    expect(result.current.phase).toBe("starting");
    mode = "ok";
    await act(() => result.current.reconnect());
    expect(result.current.state?.session.id).toBe("session-1");
    expect(result.current.busy).toBe(false);
    const starts = requests.filter((r) => r.path.endsWith("/sessions"));
    expect(starts).toHaveLength(4);
    expect(
      new Set(
        starts.map((r) => new Headers(r.init.headers).get("Idempotency-Key")),
      ).size,
    ).toBe(1);
  });
  it.each([401, 403, 408, 429])(
    "retains the uncertain decision through HTTP %i and recovers the same intent",
    async (status) => {
      let blocked = true;
      let accepted: unknown = active;
      const requests = server(({ path, init }) => {
        if (path.endsWith("/decisions")) {
          if (blocked)
            return Response.json(apiError("temporarily_rejected"), { status });
          const command = JSON.parse(String(init.body));
          accepted = nextState(command.decision_id);
          return Response.json({
            ...(accepted as object),
            outcome: "duplicate",
            acknowledged_decision_id: command.decision_id,
          });
        }
        if (path.endsWith("/session-1")) return Response.json(accepted);
      });
      const { result } = renderHook(useScenarioRunner);
      await waitFor(() => expect(result.current.phase).toBe("ready"));
      await act(() => result.current.start(scenario));
      await act(() => result.current.choose("explain"));
      expect(result.current.pendingChoiceId).toBe("explain");
      expect(result.current.busy).toBe(true);
      blocked = false;
      await act(() => result.current.reconnect());
      expect(result.current.state?.expected_sequence).toBe(1);
      expect(result.current.pendingChoiceId).toBeNull();
      const commands = requests.filter((r) => r.path.endsWith("/decisions"));
      expect(commands.length).toBeGreaterThanOrEqual(2);
      expect(new Set(commands.map((r) => r.init.body)).size).toBe(1);
    },
  );
});

describe("notification transport", () => {
  it("renews an expired token and retries the same idempotent read command", async () => {
    let calls = 0;
    const requests = server(({ path }) => {
      if (path.endsWith("/notifications/notice/read")) {
        calls++;
        return calls === 1
          ? Response.json(apiError("unauthorized"), { status: 401 })
          : Response.json({ id: "notice", read_at: "2026-09-26T10:00:00Z" });
      }
    });
    const { result } = renderHook(useScenarioRunner);
    await waitFor(() => expect(result.current.phase).toBe("ready"));
    await act(async () => {
      await result.current.writeNotificationRead(
        "notice",
        true,
        new AbortController().signal,
      );
    });
    const writes = requests.filter((r) =>
      r.path.endsWith("/notifications/notice/read"),
    );
    expect(writes).toHaveLength(2);
    expect(writes.map((r) => [r.init.method, r.init.body])).toEqual([
      ["PUT", '{"read":true}'],
      ["PUT", '{"read":true}'],
    ]);
  });
});
