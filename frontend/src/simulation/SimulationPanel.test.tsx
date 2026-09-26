import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SimulationPanel } from "./SimulationPanel";
import { parseSimulation } from "./contracts";
import { simulationFixture } from "./testFixtures";
import { ApiError } from "../runner/api";

beforeEach(() => sessionStorage.clear());
const setup = () => ({
  identityId: "demo",
  read: vi.fn<(path: string, signal: AbortSignal) => Promise<unknown>>(
    async () => ({ simulation: simulationFixture() }),
  ),
  write: vi.fn(async () => simulationFixture()),
});
describe("operational shift", () => {
  it("rejects malformed nested actions and out of range server metrics", () => {
    const malformed = simulationFixture();
    malformed.incidents[0].actions[0].enabled = "yes" as unknown as boolean;
    expect(() => parseSimulation(malformed)).toThrow();
    expect(() =>
      parseSimulation({ ...simulationFixture(), metrics: { safety: 101 } }),
    ).toThrow();
    expect(() => parseSimulation(simulationFixture())).not.toThrow();
  });
  it("opens a zone and sends only the contextual server action with revision", async () => {
    const p = setup();
    render(<SimulationPanel {...p} />);
    fireEvent.click(
      await within(
        await screen.findByRole("group", { name: "Схема вагона" }),
      ).findByRole("button", { name: /Пассажирский салон/ }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Поговорить" }));
    await waitFor(() => expect(p.write).toHaveBeenCalledTimes(1));
    expect(p.write).toHaveBeenCalledWith(
      "/simulations/run-1/actions",
      {
        command_id: expect.any(String),
        expected_revision: 1,
        action_id: "talk",
        incident_id: "request",
        zone_id: "salon",
      },
      expect.any(String),
      expect.any(AbortSignal),
    );
    expect(screen.getByText("Пассажир просит помочь с багажом.")).toBeVisible();
  });
  it("keeps unavailable equipment actions disabled and explains why", async () => {
    const p = setup();
    p.read.mockResolvedValue({
      simulation: {
        ...simulationFixture(),
        actions: [
          {
            id: "kit",
            label: "Взять аптечку",
            description: "Служебная экипировка",
            enabled: false,
            reason: "Сначала перейдите в служебную зону",
            incident_id: null,
            zone_id: "service",
          },
        ],
      },
    });
    render(<SimulationPanel {...p} />);
    expect(
      await screen.findByRole("button", { name: "Взять аптечку" }),
    ).toBeDisabled();
    expect(
      screen.getByText("Сначала перейдите в служебную зону"),
    ).toBeVisible();
  });
  it("replays the same command after a lost acknowledgement, including remount", async () => {
    const p = setup();
    p.write.mockRejectedValueOnce(new Error("Нет связи"));
    const mounted = render(<SimulationPanel {...p} />);
    fireEvent.click(
      await within(
        await screen.findByRole("group", { name: "Схема вагона" }),
      ).findByRole("button", { name: /Пассажирский салон/ }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Поговорить" }));
    await screen.findByRole("button", { name: "Проверить последнее действие" });
    const command = p.write.mock.calls[0];
    mounted.unmount();
    render(<SimulationPanel {...p} />);
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Проверить последнее действие",
      }),
    );
    await waitFor(() => expect(p.write).toHaveBeenCalledTimes(2));
    expect(p.write.mock.calls[1].slice(0, 3)).toEqual(command.slice(0, 3));
  });
  it("acknowledges an old receipt without replacing a newer active shift", async () => {
    const command = {
      path: "/simulations/old-run/actions",
      key: "old-key",
      body: {
        command_id: "old-key",
        expected_revision: 1,
        action_id: "talk",
        incident_id: "request",
        zone_id: "salon",
      },
    };
    sessionStorage.setItem(
      "vsm.simulation.command.demo",
      JSON.stringify(command),
    );
    const p = setup();
    p.write.mockResolvedValue({
      ...simulationFixture(),
      id: "old-run",
      status: "completed",
      elapsed_seconds: 1200,
      title: "Старая смена",
    });
    render(<SimulationPanel {...p} />);
    await screen.findByRole("heading", { name: "Рейс 001 · Учебная смена" });
    fireEvent.click(
      screen.getByRole("button", { name: "Проверить последнее действие" }),
    );
    await waitFor(() => expect(p.read.mock.calls.length).toBeGreaterThan(1));
    expect(
      screen.getByRole("heading", { name: "Рейс 001 · Учебная смена" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("heading", { name: "Старая смена" }),
    ).not.toBeInTheDocument();
    expect(sessionStorage.getItem("vsm.simulation.command.demo")).toBeNull();
  });
  it.each([401, 429])(
    "keeps an unacknowledged command after retry gets HTTP %s",
    async (status) => {
      const command = {
        path: "/simulations/run-1/actions",
        key: "lost-key",
        body: {
          command_id: "lost-key",
          expected_revision: 0,
          action_id: "talk",
          incident_id: "request",
          zone_id: "salon",
        },
      };
      sessionStorage.setItem(
        "vsm.simulation.command.demo",
        JSON.stringify(command),
      );
      const p = setup();
      p.write.mockRejectedValue(
        new ApiError(status, "temporarily_unavailable", null),
      );
      render(<SimulationPanel {...p} />);
      fireEvent.click(
        await screen.findByRole("button", {
          name: "Проверить последнее действие",
        }),
      );
      await waitFor(() => expect(p.write).toHaveBeenCalledTimes(1));
      await waitFor(() =>
        expect(
          screen.getByRole("button", { name: "Проверить последнее действие" }),
        ).toBeEnabled(),
      );
      expect(
        JSON.parse(sessionStorage.getItem("vsm.simulation.command.demo")!),
      ).toEqual(command);
    },
  );
  it("refreshes a stale revision without automatically choosing again", async () => {
    const p = setup();
    p.write.mockRejectedValue(new ApiError(409, "simulation_conflict", null));
    render(<SimulationPanel {...p} />);
    fireEvent.click(
      await within(
        await screen.findByRole("group", { name: "Схема вагона" }),
      ).findByRole("button", { name: /Пассажирский салон/ }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Поговорить" }));
    await waitFor(() => expect(p.read.mock.calls.length).toBeGreaterThan(1));
    expect(p.write).toHaveBeenCalledTimes(1);
    expect(
      screen.queryByRole("button", { name: "Проверить последнее действие" }),
    ).not.toBeInTheDocument();
  });
  it("renders the completed black box with consequences and authored alternatives", async () => {
    const final = {
      ...simulationFixture(),
      status: "completed" as const,
      elapsed_seconds: 1200,
      xp: 50,
      journal: [
        {
          id: "entry-1",
          at_seconds: 30,
          kind: "action",
          incident_id: "request",
          title: "Просьба уточнена",
          explanation: "Удалось сохранить проход свободным.",
          metric_changes: [
            { metric: "service", delta: 5, before: 70, after: 75 },
          ],
        },
      ],
    };
    const p = setup();
    p.read.mockImplementation(async (path: string) =>
      path.endsWith("/debrief")
        ? {
            simulation: final,
            summary: "Помощь организована вовремя.",
            incidents: [
              {
                id: "request",
                title: "Помощь с багажом",
                outcome: "Завершено",
                reported_at_seconds: 0,
                discovered_at_seconds: 20,
                first_reaction_seconds: 10,
                resolved_at_seconds: 30,
                alternatives: [
                  "Без уточнения просьбы помощь могла задержаться.",
                ],
              },
            ],
            recommendations: ["Продолжайте уточнять потребность."],
            achievements: ["Внимательный проводник"],
          }
        : { simulation: final },
    );
    render(<SimulationPanel {...p} />);
    expect(
      await screen.findByRole("heading", { name: "Как прошла ваша смена" }),
    ).toBeVisible();
    expect(
      screen.getByText("Удалось сохранить проход свободным."),
    ).toBeVisible();
    fireEvent.click(
      screen.getByText("Помощь с багажом", { selector: "summary" }),
    );
    expect(
      screen.getByText("Без уточнения просьбы помощь могла задержаться."),
    ).toBeVisible();
    expect(screen.getByText("Внимательный проводник")).toBeVisible();
  });
});
