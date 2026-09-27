import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { simulationFixture } from "../simulation/testFixtures";
import { SimulationPanel } from "../simulation/SimulationPanel";
import { parseTraining } from "./contracts";
import {
  ModeChooser,
  MyAssignments,
  SoundSwitch,
  Assessment,
  LearningPanel,
  ReplayPanel,
} from "./TrainingTools";

export function trainingFixture() {
  const s = simulationFixture();
  return {
    ...s,
    engine_version: 2 as const,
    mode: "work" as const,
    source_id: null,
    assignment_id: null,
    reward_eligible: true,
    pending_action: null,
    replay: false,
    actions: s.actions.map((a) => ({ ...a, duration_seconds: 8 })),
    incidents: s.incidents.map((i) => ({
      ...i,
      actions: i.actions.map((a) => ({ ...a, duration_seconds: 15 })),
    })),
  };
}
describe("training evolution", () => {
  it("validates timed actions and prevents practice rewards in the contract", () => {
    expect(() => parseTraining(trainingFixture())).not.toThrow();
    expect(() =>
      parseTraining({ ...trainingFixture(), source_id: "source" }),
    ).toThrow();
    expect(() =>
      parseTraining({
        ...trainingFixture(),
        pending_action: {
          id: "job",
          action_id: "talk",
          label: "Разговор",
          incident_id: "request",
          zone_id: null,
          started_at_seconds: 15,
          completes_at_seconds: 10,
          interruptible: true,
        },
      }),
    ).toThrow();
  });
  it("offers three honest server modes", () => {
    const change = vi.fn();
    render(<ModeChooser mode="work" onChange={change} />);
    fireEvent.click(screen.getByRole("radio", { name: /Демонстрация/ }));
    expect(change).toHaveBeenCalledWith("demo");
    expect(screen.getByText(/4 минуты/)).toBeVisible();
    expect(screen.getByText(/20 минут/)).toBeVisible();
  });
  it("shows unobserved criteria as insufficient evidence", () => {
    render(
      <Assessment
        assessment={{
          methodology_version: "1",
          source_notice: "Учебная методика",
          criteria: [
            {
              id: "c1",
              competency_id: "safety",
              title: "Проверка обстановки",
              met: null,
              explanation: "Ситуация не возникала.",
              source: "Критерий демонстрационного кейса",
              source_version: "1",
              evidence_event_ids: [],
            },
          ],
        }}
        journal={[]}
      />,
    );
    expect(screen.getByText("Недостаточно данных")).toBeVisible();
    expect(screen.getByText("Ситуация не возникала.")).toBeVisible();
  });
  it("starts targeted practice from its evidence-backed recommendation", async () => {
    const start = vi.fn();
    const read = vi.fn(async () => ({
      competencies: [],
      patterns: [],
      recommendations: [
        {
          competency_id: "communication",
          title: "Связь без задержки",
          explanation: "Обращение к старшему задержалось в двух сменах.",
        },
      ],
      statistics: { completed_runs: 2, completed_scenarios: 1 },
      source_notice: "Учебная методика",
    }));
    render(<LearningPanel read={read} onPractice={start} />);
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Отработать: Связь без задержки",
      }),
    );
    expect(start).toHaveBeenCalledWith("communication");
  });
  it("loads a past observable state and forks the selected time without sending metrics", async () => {
    const s = {
      ...trainingFixture(),
      status: "completed" as const,
      elapsed_seconds: 1200,
    };
    const read = vi.fn(async () => ({
      ...s,
      elapsed_seconds: 15,
      status: "active",
      replay: true,
      reward_eligible: false,
    }));
    const fork = vi.fn();
    render(<ReplayPanel simulation={s} read={read} onFork={fork} />);
    fireEvent.change(
      screen.getByRole("slider", { name: "Момент воспроизведения" }),
      { target: { value: "15" } },
    );
    await waitFor(() =>
      expect(read).toHaveBeenLastCalledWith(
        "/training/runs/run-1/replay?at_seconds=15",
        expect.any(AbortSignal),
      ),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Попробовать иначе с этого момента" }),
    );
    expect(fork).toHaveBeenCalledWith(15);
  });
});

describe("duration-aware server commands", () => {
  it("shows remaining action time and cancels only the authoritative revision", async () => {
    sessionStorage.clear();
    const state = {
      ...trainingFixture(),
      pending_action: {
        id: "job-2",
        action_id: "talk",
        label: "Разговор с пассажиром",
        incident_id: "request",
        zone_id: "salon",
        started_at_seconds: 0,
        completes_at_seconds: 50,
        interruptible: true,
      },
    };
    const read = vi.fn(async () => ({ simulation: state }));
    const write = vi.fn(async () => ({
      ...state,
      revision: 2,
      pending_action: null,
    }));
    render(
      <SimulationPanel training identityId="timed" read={read} write={write} />,
    );
    await screen.findByRole("heading", { name: "Разговор с пассажиром" });
    fireEvent.click(screen.getByRole("button", { name: /Прервать действие/ }));
    await waitFor(() => expect(write).toHaveBeenCalledTimes(1));
    expect(write).toHaveBeenCalledWith(
      "/training/runs/run-1/actions",
      {
        command_id: expect.any(String),
        expected_revision: 1,
        action_id: "cancel",
        incident_id: null,
        zone_id: null,
      },
      expect.any(String),
      expect.any(AbortSignal),
    );
  });
  it("starts the selected mode without allowing client clocks or scores", async () => {
    sessionStorage.clear();
    const read = vi.fn(async (path: string) =>
      path === "/training/current"
        ? { simulation: null }
        : path === "/training/assignments"
          ? { items: [] }
          : {
              competencies: [],
              patterns: [],
              recommendations: [],
              statistics: { completed_runs: 0, completed_scenarios: 0 },
              source_notice: "Учебная модель",
            },
    );
    const write = vi.fn(async () => ({
      ...trainingFixture(),
      mode: "demo",
      duration_seconds: 240,
      reward_eligible: false,
    }));
    render(
      <SimulationPanel training identityId="modes" read={read} write={write} />,
    );
    fireEvent.click(await screen.findByRole("radio", { name: /Демонстрация/ }));
    fireEvent.click(
      screen.getByRole("button", { name: /Начать: Демонстрация/ }),
    );
    await waitFor(() =>
      expect(write).toHaveBeenCalledWith(
        "/training/runs",
        { mode: "demo" },
        expect.any(String),
        expect.any(AbortSignal),
      ),
    );
  });
  it("opens the existing assignment run instead of submitting a second start", async () => {
    const start = vi.fn(),
      open = vi.fn();
    const read = vi.fn(async (path: string) =>
      path === "/training/assignments"
        ? {
            items: [
              {
                id: "assignment",
                title: "Учебный кейс",
                mode: "demo",
                created_at: "2026-09-27T12:00:00Z",
                member_count: 2,
                completed_count: 1,
              },
            ],
          }
        : {
            id: "assignment",
            title: "Учебный кейс",
            mode: "demo",
            members: [
              {
                employee_id: "demo-employee",
                display_name: "Участник",
                run_id: "saved-run",
                status: "completed",
                metrics: null,
              },
            ],
            difficulties: [],
          },
    );
    render(<MyAssignments read={read} onStart={start} onOpen={open} />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Открыть назначение" }),
    );
    await waitFor(() => expect(open).toHaveBeenCalledWith("saved-run"));
    expect(start).not.toHaveBeenCalled();
  });
  it("sounds once for new report and response events in a batch after opt-in", () => {
    const start = vi.fn();
    class AudioFixture {
      state = "running";
      currentTime = 0;
      destination = {};
      resume = async () => {};
      close = async () => {};
      createOscillator = () => ({
        frequency: { value: 0 },
        connect: vi.fn(),
        start,
        stop: vi.fn(),
      });
      createGain = () => ({
        gain: {
          setValueAtTime: vi.fn(),
          exponentialRampToValueAtTime: vi.fn(),
        },
        connect: vi.fn(),
      });
    }
    vi.stubGlobal("AudioContext", AudioFixture);
    try {
      const state = trainingFixture();
      const mounted = render(<SoundSwitch simulation={state} />);
      fireEvent.click(screen.getByRole("button", { name: /Звуковые сигналы/ }));
      const event = (id: string, kind: string) => ({
        id,
        kind,
        at_seconds: 10,
        incident_id: "request",
        title: "Событие",
        explanation: "Наблюдение",
        metric_changes: [],
      });
      const journal = [event("1", "reported"), event("2", "action_started")];
      mounted.rerender(<SoundSwitch simulation={{ ...state, journal }} />);
      expect(start).toHaveBeenCalledTimes(1);
      mounted.rerender(
        <SoundSwitch
          simulation={{
            ...state,
            journal: [
              ...journal,
              event("3", "communication"),
              event("4", "action"),
            ],
          }}
        />,
      );
      expect(start).toHaveBeenCalledTimes(2);
      mounted.rerender(
        <SoundSwitch
          simulation={{
            ...state,
            journal: [
              ...journal,
              event("3", "communication"),
              event("4", "action"),
            ],
          }}
        />,
      );
      expect(start).toHaveBeenCalledTimes(2);
      mounted.unmount();
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
