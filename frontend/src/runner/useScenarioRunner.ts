import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  friendlyError,
  parseCatalogPage,
  parseDecisionResponse,
  parseLogin,
  parseSessionResult,
  parseSessionState,
  request,
} from "./api";
import type {
  DecisionCommand,
  LoginResponse,
  RunnerView,
  ScenarioSummary,
  SessionState,
  StartCommand,
} from "./types";

const STORAGE_KEY = "vsm.runner.v1";
const TOKEN_KEY = "vsm.demo-token.v1";
interface SavedAttempt {
  sessionId: string | null;
  start: StartCommand | null;
  decision: DecisionCommand | null;
}
const emptyAttempt = (): SavedAttempt => ({
  sessionId: null,
  start: null,
  decision: null,
});
const initialView = (): RunnerView => ({
  identity: null,
  phase: "loading",
  catalog: [],
  state: null,
  result: null,
  error: null,
  connection: "reconnecting",
  busy: false,
  remainingSeconds: null,
  pendingChoiceId: null,
});
function storageWrite(key: string, value: unknown) {
  try {
    sessionStorage.setItem(key, JSON.stringify(value));
  } catch {
    throw new Error(
      "Браузер не может сохранить попытку. Разрешите sessionStorage и повторите подключение.",
    );
  }
}
function requestId(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.trim().length > 0 &&
    value.length <= 128 &&
    Array.from(value).every(
      (character) =>
        character.charCodeAt(0) >= 32 && character.charCodeAt(0) !== 127,
    )
  );
}
function savedAttempt(): SavedAttempt {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return emptyAttempt();
    const value = JSON.parse(raw) as SavedAttempt;
    if (!value || (value.sessionId !== null && !requestId(value.sessionId)))
      throw new Error();
    if (
      value.start &&
      (!requestId(value.start.key) ||
        typeof value.start.scenario_id !== "string" ||
        !/^[a-z][a-z0-9_-]{0,63}$/.test(value.start.scenario_id) ||
        !Number.isSafeInteger(value.start.scenario_version) ||
        value.start.scenario_version < 1 ||
        value.start.scenario_version > 2147483647)
    )
      throw new Error();
    if (
      value.decision &&
      (!value.sessionId ||
        !requestId(value.decision.decision_id) ||
        value.decision.decision_id.startsWith("timeout:") ||
        !requestId(value.decision.node_id) ||
        !requestId(value.decision.choice_id) ||
        !Number.isSafeInteger(value.decision.expected_sequence) ||
        value.decision.expected_sequence < 0 ||
        value.decision.expected_sequence > 2147483647)
    )
      throw new Error();
    if (value.start && value.decision) throw new Error();
    return {
      sessionId: value.sessionId,
      start: value.start
        ? {
            key: value.start.key,
            scenario_id: value.start.scenario_id,
            scenario_version: value.start.scenario_version,
          }
        : null,
      decision: value.decision
        ? {
            decision_id: value.decision.decision_id,
            node_id: value.decision.node_id,
            choice_id: value.decision.choice_id,
            expected_sequence: value.decision.expected_sequence,
          }
        : null,
    };
  } catch {
    try {
      sessionStorage.removeItem(STORAGE_KEY);
    } catch {
      // Disabled storage is reported by the first required write before POST.
    }
    return emptyAttempt();
  }
}
function savedToken(): LoginResponse | null {
  try {
    const raw = sessionStorage.getItem(TOKEN_KEY);
    if (!raw) return null;
    const token = parseLogin(JSON.parse(raw));
    return token;
  } catch {
    return null;
  }
}
function isAbort(error: unknown) {
  return error instanceof DOMException && error.name === "AbortError";
}
function definitiveRejection(error: unknown): error is ApiError {
  if (!(error instanceof ApiError)) return false;
  // Authentication/rate-limit failures say nothing about an earlier commit.
  // Release an intent only for explicit request or ownership rejection codes.
  return (
    (error.status === 422 && error.code === "validation_error") ||
    (error.status === 404 &&
      ["scenario_not_found", "session_not_found"].includes(error.code)) ||
    (error.status === 409 && error.code === "idempotency_conflict")
  );
}

// This controller owns requests and persistence for one mounted hook. Polls are
// cancellable reads; a command owns priority and its immutable retry payload.
class RunnerController {
  private identityGeneration = 0;
  private view = initialView();
  private saved = savedAttempt();
  private token = savedToken();
  private closed = false;
  private running = false;
  private operation: AbortController | null = null;
  private pollController: AbortController | null = null;
  private anchor: {
    deadline: number;
    server: number;
    monotonic: number;
  } | null = null;
  private tickTimer: ReturnType<typeof setInterval>;
  private pollTimer: ReturnType<typeof setInterval>;

  constructor(private publish: (view: RunnerView) => void) {
    this.tickTimer = setInterval(() => this.tick(), 250);
    this.pollTimer = setInterval(() => {
      if (
        this.view.state?.session.status === "active" &&
        this.view.state.deadline !== null
      )
        void this.poll();
    }, 3000);
    window.addEventListener("online", this.onOnline);
    window.addEventListener("offline", this.onOffline);
    document.addEventListener("visibilitychange", this.onVisibility);
  }
  private onOnline = () => {
    void this.reconnect();
  };
  private onOffline = () => {
    this.update({ connection: "offline" });
  };
  private onVisibility = () => {
    if (document.visibilityState !== "visible") return;
    if (this.saved.start || this.saved.decision || this.view.error)
      void this.reconnect();
    else void this.poll();
  };
  dispose() {
    this.closed = true;
    this.operation?.abort();
    this.pollController?.abort();
    clearInterval(this.tickTimer);
    clearInterval(this.pollTimer);
    window.removeEventListener("online", this.onOnline);
    window.removeEventListener("offline", this.onOffline);
    document.removeEventListener("visibilitychange", this.onVisibility);
  }
  private update(change: Partial<RunnerView>) {
    if (this.closed) return;
    this.view = {
      ...this.view,
      ...change,
      busy: this.running || !!this.saved.start || !!this.saved.decision,
      pendingChoiceId: this.saved.decision?.choice_id ?? null,
    };
    this.publish(this.view);
  }
  private persist(next: SavedAttempt) {
    if (this.closed) throw new DOMException("Aborted", "AbortError");
    storageWrite(STORAGE_KEY, next);
    this.saved = next;
  }
  private async authenticate(signal: AbortSignal, force = false) {
    const generation = this.identityGeneration;
    if (
      !force &&
      this.token &&
      Date.parse(this.token.expires_at) > Date.now() + 1000
    ) {
      this.update({ identity: this.token.profile });
      return;
    }
    const token = parseLogin(
      await request(
        "/auth/demo",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            persona_id: this.token?.profile.id ?? "demo-employee",
          }),
        },
        signal,
      ),
    );
    if (this.closed || signal.aborted || generation !== this.identityGeneration)
      throw new DOMException("Aborted", "AbortError");
    storageWrite(TOKEN_KEY, token);
    this.token = token;
    this.update({ identity: token.profile });
  }
  private async api(path: string, init: RequestInit, signal: AbortSignal) {
    const generation = this.identityGeneration;
    await this.authenticate(signal);
    const send = async () => {
      if (generation !== this.identityGeneration || signal.aborted)
        throw new DOMException("Aborted", "AbortError");
      const value = await request(
        path,
        {
          ...init,
          headers: {
            ...init.headers,
            Authorization: `Bearer ${this.token!.access_token}`,
          },
        },
        signal,
      );
      if (generation !== this.identityGeneration)
        throw new DOMException("Aborted", "AbortError");
      return value;
    };
    try {
      return await send();
    } catch (error) {
      if (!(error instanceof ApiError && error.status === 401)) throw error;
      if (generation !== this.identityGeneration)
        throw new DOMException("Aborted", "AbortError");
      await this.authenticate(signal, true);
      return await send();
    }
  }
  private async catalog(signal: AbortSignal) {
    const items: ScenarioSummary[] = [];
    const seen = new Set<string>();
    // The demo is small; bounded paging also prevents malformed servers looping.
    for (let offset = 0; offset < 10000;) {
      const page = parseCatalogPage(
        await this.api(`/scenarios?limit=100&offset=${offset}`, {}, signal),
      );
      if (
        page.offset !== offset ||
        (page.items.length === 0 && offset < page.total)
      )
        throw new Error("Сервер вернул некорректную страницу сценариев.");
      for (const item of page.items) {
        const key = `${item.id}:${item.version}`;
        if (seen.has(key))
          throw new Error("Сервер вернул повторяющиеся сценарии.");
        seen.add(key);
        items.push(item);
      }
      offset += page.items.length;
      if (offset >= page.total) {
        this.update({ catalog: items });
        return;
      }
    }
    throw new Error(
      "Каталог слишком велик для демо. Уточните настройки сервера.",
    );
  }
  private adopt(state: SessionState) {
    if (this.closed) return;
    this.persist({ ...this.saved, sessionId: state.session.id });
    const monotonic = performance.now();
    const sameDeadline =
      this.anchor &&
      this.view.state?.session.id === state.session.id &&
      this.view.state.current_node.id === state.current_node.id &&
      this.view.state.deadline === state.deadline;
    // A slow response must not give time back to the same timed situation.
    const server = sameDeadline
      ? Math.max(
          Date.parse(state.server_time),
          this.anchor!.server + monotonic - this.anchor!.monotonic,
        )
      : Date.parse(state.server_time);
    this.anchor = state.deadline
      ? {
          deadline: Date.parse(state.deadline),
          server,
          monotonic,
        }
      : null;
    this.update({
      state,
      phase: state.session.status,
      connection: "online",
      remainingSeconds: this.remaining(),
    });
  }
  private remaining() {
    if (!this.anchor) return null;
    return Math.max(
      0,
      Math.ceil(
        (this.anchor.deadline -
          this.anchor.server -
          (performance.now() - this.anchor.monotonic)) /
          1000,
      ),
    );
  }
  private tick() {
    const remainingSeconds = this.remaining();
    if (remainingSeconds === this.view.remainingSeconds) return;
    this.update({ remainingSeconds });
    if (remainingSeconds === 0 && this.view.state?.session.status === "active")
      void this.poll();
  }
  private async result(signal: AbortSignal) {
    if (this.view.state?.session.status !== "completed" || this.view.result)
      return;
    const result = parseSessionResult(
      await this.api(
        `/sessions/${encodeURIComponent(this.saved.sessionId!)}/result`,
        {},
        signal,
      ),
    );
    if (result.session.id !== this.saved.sessionId)
      throw new Error("Сервер вернул результат другой попытки.");
    this.update({ result, connection: "online" });
  }
  private async read(signal: AbortSignal) {
    if (!this.saved.sessionId) return;
    const state = parseSessionState(
      await this.api(
        `/sessions/${encodeURIComponent(this.saved.sessionId)}`,
        {},
        signal,
      ),
    );
    if (state.session.id !== this.saved.sessionId)
      throw new Error("Сервер вернул другую попытку.");
    this.adopt(state);
    await this.result(signal);
  }
  private async replay(signal: AbortSignal) {
    const start = this.saved.start;
    if (start) {
      try {
        const state = parseSessionState(
          await this.api(
            "/sessions",
            {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
                "Idempotency-Key": start.key,
              },
              body: JSON.stringify({
                scenario_id: start.scenario_id,
                scenario_version: start.scenario_version,
              }),
            },
            signal,
          ),
        );
        if (
          state.session.scenario_id !== start.scenario_id ||
          state.session.scenario_version !== start.scenario_version
        )
          throw new Error("Сервер вернул попытку другого сценария.");
        this.persist({
          sessionId: state.session.id,
          start: null,
          decision: null,
        });
        this.adopt(state);
        await this.result(signal);
      } catch (error) {
        if (definitiveRejection(error))
          this.persist({ ...this.saved, start: null });
        throw error;
      }
    }
    const decision = this.saved.decision;
    if (decision) {
      try {
        const state = parseDecisionResponse(
          await this.api(
            `/sessions/${encodeURIComponent(this.saved.sessionId!)}/decisions`,
            {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(decision),
            },
            signal,
          ),
          decision.decision_id,
        );
        if (state.session.id !== this.saved.sessionId)
          throw new Error("Сервер вернул другую попытку.");
        this.persist({ ...this.saved, decision: null });
        this.adopt(state);
        await this.result(signal);
      } catch (error) {
        if (
          error instanceof ApiError &&
          error.status === 409 &&
          error.data != null
        ) {
          const state = parseSessionState(error.data);
          if (state.session.id !== this.saved.sessionId)
            throw new Error("Сервер вернул другую попытку.", { cause: error });
          this.persist({ ...this.saved, decision: null });
          this.adopt(state);
          this.update({ error: friendlyError(error) });
          await this.result(signal);
          return;
        }
        if (definitiveRejection(error))
          this.persist({ ...this.saved, decision: null });
        throw error;
      }
    }
  }
  private async run(work: (signal: AbortSignal) => Promise<void>) {
    if (this.running || this.closed) return;
    this.pollController?.abort();
    this.pollController = null;
    this.running = true;
    const controller = new AbortController();
    this.operation = controller;
    this.update({ error: null, connection: "reconnecting" });
    try {
      await work(controller.signal);
    } catch (error) {
      if (!isAbort(error) && !this.closed)
        this.update({
          error: friendlyError(error),
          connection: "offline",
          phase:
            this.view.state?.session.status ??
            (this.saved.start ? "starting" : "ready"),
        });
    } finally {
      this.running = false;
      if (this.operation === controller) this.operation = null;
      this.update({});
    }
  }
  initialize = async () =>
    this.run(async (signal) => {
      await this.catalog(signal);
      await this.replay(signal);
      await this.read(signal);
      this.update({
        phase: this.view.state?.session.status ?? "ready",
        connection: "online",
      });
    });
  start = async (scenario: ScenarioSummary) => {
    if (
      this.running ||
      this.saved.start ||
      this.saved.decision ||
      this.view.state ||
      this.closed
    )
      return;
    await this.run(async (signal) => {
      this.persist({
        sessionId: null,
        start: {
          key: crypto.randomUUID(),
          scenario_id: scenario.id,
          scenario_version: scenario.version,
        },
        decision: null,
      });
      this.update({ phase: "starting", result: null });
      await this.replay(signal);
    });
  };
  choose = async (choiceId: string) => {
    const state = this.view.state;
    if (
      this.running ||
      this.saved.decision ||
      this.saved.start ||
      !state ||
      state.session.status !== "active" ||
      !state.available_choices.some((choice) => choice.id === choiceId) ||
      this.closed
    )
      return;
    await this.run(async (signal) => {
      this.persist({
        ...this.saved,
        decision: {
          decision_id: crypto.randomUUID(),
          node_id: state.current_node.id,
          choice_id: choiceId,
          expected_sequence: state.expected_sequence,
        },
      });
      this.update({});
      await this.replay(signal);
    });
  };
  reconnect = async () =>
    this.run(async (signal) => {
      if (this.view.catalog.length === 0) await this.catalog(signal);
      await this.replay(signal);
      await this.read(signal);
      this.update({
        phase: this.view.state?.session.status ?? "ready",
        connection: "online",
      });
    });
  private async poll() {
    if (
      this.running ||
      this.saved.start ||
      this.saved.decision ||
      this.pollController ||
      !this.saved.sessionId ||
      this.closed
    )
      return;
    const controller = new AbortController();
    this.pollController = controller;
    try {
      await this.read(controller.signal);
      if (!controller.signal.aborted)
        this.update({ error: null, connection: "online" });
    } catch (error) {
      if (!isAbort(error) && !this.closed)
        this.update({ error: friendlyError(error), connection: "offline" });
    } finally {
      if (this.pollController === controller) this.pollController = null;
    }
  }
  leave = () => {
    if (this.running || this.view.phase !== "completed" || this.closed) return;
    try {
      this.pollController?.abort();
      this.persist(emptyAttempt());
      this.anchor = null;
      this.update({
        state: null,
        result: null,
        phase: "ready",
        remainingSeconds: null,
        error: null,
      });
    } catch (error) {
      this.update({ error: friendlyError(error) });
    }
  };

  readResource = (path: string, signal: AbortSignal): Promise<unknown> => {
    if (this.closed)
      return Promise.reject(new DOMException("Aborted", "AbortError"));
    return this.api(path, {}, signal);
  };

  writeNotificationRead = (
    id: string,
    read: boolean,
    signal: AbortSignal,
  ): Promise<unknown> => {
    if (this.closed)
      return Promise.reject(new DOMException("Aborted", "AbortError"));
    return this.api(
      `/notifications/${encodeURIComponent(id)}/read`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ read }),
      },
      signal,
    );
  };

  switchPersona = async (personaId: string) => {
    if (
      this.running ||
      this.saved.start ||
      this.saved.decision ||
      this.view.state?.session.status === "active" ||
      this.closed
    )
      return;
    await this.run(async (signal) => {
      const token = parseLogin(
        await request(
          "/auth/demo",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ persona_id: personaId }),
          },
          signal,
        ),
      );
      if (this.closed || signal.aborted)
        throw new DOMException("Aborted", "AbortError");
      if (token.profile.id !== personaId)
        throw new Error("Сервер вернул другого учебного проводника.");
      this.persist(emptyAttempt());
      storageWrite(TOKEN_KEY, token);
      this.identityGeneration += 1;
      this.token = token;
      this.anchor = null;
      this.update({
        identity: token.profile,
        state: null,
        result: null,
        phase: "ready",
        remainingSeconds: null,
        error: null,
        connection: "online",
      });
    });
  };
}

export function useScenarioRunner() {
  const [view, setView] = useState<RunnerView>(initialView);
  const controller = useRef<RunnerController | null>(null);
  useEffect(() => {
    const runner = new RunnerController(setView);
    controller.current = runner;
    void runner.initialize();
    return () => {
      runner.dispose();
      if (controller.current === runner) controller.current = null;
    };
  }, []);
  const readResource = useCallback(
    (path: string, signal: AbortSignal) =>
      controller.current?.readResource(path, signal) ??
      Promise.reject(new Error("Подключение ещё не готово.")),
    [],
  );
  const writeNotificationRead = useCallback(
    (id: string, read: boolean, signal: AbortSignal) =>
      controller.current?.writeNotificationRead(id, read, signal) ??
      Promise.reject(new Error("Подключение ещё не готово.")),
    [],
  );
  return {
    ...view,
    writeNotificationRead,
    readResource,
    switchPersona: (personaId: string) =>
      controller.current?.switchPersona(personaId) ?? Promise.resolve(),
    start: (scenario: ScenarioSummary) =>
      controller.current?.start(scenario) ?? Promise.resolve(),
    choose: (choiceId: string) =>
      controller.current?.choose(choiceId) ?? Promise.resolve(),
    reconnect: () => controller.current?.reconnect() ?? Promise.resolve(),
    leave: () => controller.current?.leave(),
  };
}
