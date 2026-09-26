import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ShiftPanel, ShiftRoute } from "./ShiftPanel";
import { parseShift, type Shift, type WriteResource } from "./contracts";

export const fixture = (): Shift => ({
  id: "shift-one",
  title: "Учебная смена",
  status: "active",
  seed: 123,
  difficulty: "standard",
  started_at: "2026-09-26T12:00:00Z",
  completed_at: null,
  current_step: 0,
  current_session_id: "session-one",
  steps: ["service", "conflict", "critical"].map((kind, index) => ({
    index,
    kind: kind as "service" | "conflict" | "critical",
    title: `Событие ${index + 1}`,
    scenario_id: `scenario-${index}`,
    scenario_version: 1,
    passenger_profile: "Пассажир с багажом",
    context: "Сначала согласуйте дальнейшие действия.",
    session_id: index === 0 ? "session-one" : null,
    status: index === 0 ? "active" : "locked",
  })),
  metrics: {
    safety: null,
    service: null,
    regulation: 0,
    communication: 0,
    average_reaction_seconds: null,
    decision_count: 0,
    critical_errors: 0,
    completed_scenarios: 0,
    total_scenarios: 3,
    xp: 0,
  },
  achievements: [],
  recommendations: [],
});
const defaults = () => ({
  read: vi.fn(async () => ({ shift: null })),
  write: vi.fn<WriteResource>(async () => fixture()),
  identityId: "demo",
  sessionCompleted: false,
  revision: 0,
  openSession: vi.fn(async () => {}),
  busy: false,
});

describe("server-backed training shift", () => {
  it("starts with only difficulty and an idempotency key, then opens the server session", async () => {
    const props = defaults();
    render(<ShiftPanel {...props} />);
    fireEvent.click(await screen.findByRole("button", { name: /Заступить/ }));
    await waitFor(() =>
      expect(props.openSession).toHaveBeenCalledWith("session-one"),
    );
    expect(props.write).toHaveBeenCalledWith(
      "/shifts",
      { difficulty: "standard" },
      expect.any(String),
      expect.any(AbortSignal),
    );
  });
  it("does not let a stale read started during a command erase its confirmed route", async () => {
    let finishWrite!: (value: unknown) => void;
    let finishRead!: (value: unknown) => void;
    const props = defaults();
    props.write.mockImplementation(
      () =>
        new Promise((resolve) => {
          finishWrite = resolve;
        }),
    );
    const { rerender } = render(<ShiftPanel {...props} />);
    fireEvent.click(await screen.findByRole("button", { name: /Заступить/ }));
    props.read.mockImplementation(
      () =>
        new Promise((resolve) => {
          finishRead = resolve as typeof finishRead;
        }),
    );
    rerender(
      <ShiftPanel {...props} sessionId="old-completed" sessionCompleted />,
    );
    await waitFor(() => expect(finishRead).toBeDefined());
    finishWrite(fixture());
    await screen.findByRole("list", { name: "Маршрут учебной смены" });
    await act(async () => {
      finishRead({ shift: null });
    });
    await waitFor(() =>
      expect(
        screen.getByRole("list", { name: "Маршрут учебной смены" }),
      ).toBeInTheDocument(),
    );
    expect(
      screen.queryByRole("button", { name: /Заступить/ }),
    ).not.toBeInTheDocument();
  });
  it("reuses the same command after a lost acknowledgement", async () => {
    const props = defaults();
    props.write.mockRejectedValueOnce(new Error("Network failed"));
    render(<ShiftPanel {...props} />);
    fireEvent.click(await screen.findByRole("button", { name: /Заступить/ }));
    fireEvent.click(
      await screen.findByRole("button", { name: /Повторить проверку/ }),
    );
    await waitFor(() =>
      expect(props.openSession).toHaveBeenCalledWith("session-one"),
    );
    expect(props.write.mock.calls[0].slice(0, 3)).toEqual(
      props.write.mock.calls[1].slice(0, 3),
    );
  });
  it("restores the current shift and never offers advance while its session is active", async () => {
    const props = {
      ...defaults(),
      read: vi.fn(async () => ({ shift: fixture() })),
      sessionId: "session-one",
    };
    render(<ShiftPanel {...props} />);
    await screen.findByRole("list", { name: "Маршрут учебной смены" });
    expect(
      screen.queryByRole("button", { name: /К следующему/ }),
    ).not.toBeInTheDocument();
    expect(props.write).not.toHaveBeenCalled();
  });
  it("advances with server session identity and expected step, not scores", async () => {
    const props = {
      ...defaults(),
      read: vi.fn(async () => ({ shift: fixture() })),
      sessionId: "session-one",
      sessionCompleted: true,
    };
    render(<ShiftPanel {...props} />);
    fireEvent.click(
      await screen.findByRole("button", { name: /К следующему/ }),
    );
    await waitFor(() => expect(props.write).toHaveBeenCalled());
    expect(props.write.mock.calls[0][1]).toEqual({
      command_id: expect.any(String),
      expected_step: 0,
      session_id: "session-one",
    });
  });
  it("shows a recoverable error without fabricating an empty shift", async () => {
    const props = defaults();
    props.read.mockRejectedValue(new Error("offline"));
    render(<ShiftPanel {...props} />);
    await screen.findByText("Маршрут временно недоступен");
    expect(screen.getByRole("button", { name: /Заступить/ })).toBeDisabled();
  });
  it("does not replace an active standalone attempt", async () => {
    const props = {
      ...defaults(),
      read: vi.fn(async () => ({ shift: fixture() })),
      sessionId: "another-session",
    };
    render(<ShiftPanel {...props} />);
    await waitFor(() => expect(props.read).toHaveBeenCalled());
    expect(
      screen.queryByRole("region", { name: "Рабочая смена" }),
    ).not.toBeInTheDocument();
  });
  it("marks route stations without offering future stages as links", () => {
    render(<ShiftRoute shift={fixture()} />);
    expect(
      screen.getByRole("list").querySelectorAll('[aria-current="step"]'),
    ).toHaveLength(1);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
  it("rejects nested content that cannot be safely rendered and impossible active routes", () => {
    expect(() => parseShift({ ...fixture(), achievements: [null] })).toThrow();
    expect(() =>
      parseShift({ ...fixture(), recommendations: [{ text: "bad" }] }),
    ).toThrow();
    expect(() =>
      parseShift({ ...fixture(), current_step: 3, current_session_id: null }),
    ).toThrow();
    expect(() =>
      parseShift({ ...fixture(), current_session_id: "wrong" }),
    ).toThrow();
  });
  it("rejects malformed route order and nonfinite server metrics", () => {
    const bad = fixture();
    bad.steps[1].index = 5;
    expect(() => parseShift(bad)).toThrow();
    const badScore = fixture();
    badScore.metrics.xp = Infinity;
    expect(() => parseShift(badScore)).toThrow();
  });
});
