import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { parseSessionState } from "./runner/api";
import {
  active,
  completedState,
  nextState,
  scenario,
} from "./runner/testFixtures";
import { useScenarioRunner } from "./runner/useScenarioRunner";

vi.mock("./runner/useScenarioRunner");
let runner: ReturnType<typeof useScenarioRunner>;

beforeEach(() => {
  runner = {
    phase: "ready",
    catalog: [scenario],
    state: null,
    result: null,
    error: null,
    connection: "online",
    busy: false,
    remainingSeconds: null,
    pendingChoiceId: null,
    start: vi.fn(async () => {}),
    choose: vi.fn(async () => {}),
    reconnect: vi.fn(async () => {}),
    leave: vi.fn(),
  };
  vi.mocked(useScenarioRunner).mockImplementation(() => runner);
});

describe("scenario runner interface", () => {
  it("announces loading and does not offer a start before the catalogue arrives", () => {
    runner = {
      ...runner,
      phase: "loading",
      catalog: [],
      connection: "reconnecting",
    };
    render(<App />);
    expect(screen.getByRole("status")).toHaveTextContent(
      "Подключаемся к учебной смене…",
    );
    expect(
      screen.queryByRole("button", { name: "Начать сценарий" }),
    ).not.toBeInTheDocument();
  });

  it("starts the selected server scenario and exposes labelled native radio inputs", () => {
    render(<App />);
    expect(
      screen.getByRole("radio", { name: "Сервисная ситуация" }),
    ).toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "Начать сценарий" }));
    expect(runner.start).toHaveBeenCalledWith(scenario);
  });

  it("renders only server choices and sends their ids, without changing scores", () => {
    runner = {
      ...runner,
      phase: "active",
      state: parseSessionState(active),
      remainingSeconds: 40,
    };
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Объяснить" }));
    expect(runner.choose).toHaveBeenCalledWith("explain");
    expect(screen.getByTestId("loyalty-value")).toHaveTextContent("50");
    expect(screen.getByRole("timer")).toHaveAccessibleName(
      "Осталось 40 секунд",
    );
    expect(screen.getByRole("meter", { name: "Безопасность" })).toHaveAttribute(
      "aria-valuenow",
      "50",
    );
  });

  it("locks choices while an uncertain decision waits for reconnection", () => {
    runner = {
      ...runner,
      phase: "active",
      state: parseSessionState(active),
      busy: true,
      connection: "offline",
      pendingChoiceId: "explain",
      error: "Соединение потеряно.",
    };
    render(<App />);
    expect(screen.getByRole("button", { name: "Объяснить" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Игнорировать" })).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Повторно выбирать его не нужно",
    );
    fireEvent.click(screen.getByRole("button", { name: "Восстановить связь" }));
    expect(runner.reconnect).toHaveBeenCalledOnce();
  });

  it("stops input at displayed deadline without inventing a terminal state", () => {
    runner = {
      ...runner,
      phase: "active",
      state: parseSessionState(active),
      remainingSeconds: 0,
    };
    render(<App />);
    expect(screen.getByRole("button", { name: "Объяснить" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("Время истекло");
    expect(
      screen.queryByRole("heading", { name: "Сценарий завершён" }),
    ).not.toBeInTheDocument();
  });

  it("focuses the new scene heading and keeps next choices immediately available", () => {
    runner = { ...runner, phase: "active", state: parseSessionState(active) };
    const { rerender } = render(<App />);
    runner = { ...runner, state: parseSessionState(nextState()) };
    rerender(<App />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveFocus();
    expect(screen.getByRole("button", { name: "Предложить" })).toBeEnabled();
    expect(screen.getByRole("status")).toHaveTextContent("Решение принято");
    expect(
      screen.getByRole("list", { name: "Пройденные сцены" }).children,
    ).toHaveLength(2);
  });

  it("uses the supplied text for unknown scenarios instead of authored dialogue", () => {
    const state = parseSessionState(active);
    state.session.scenario_id = "new-scenario";
    state.current_node.text = "Новая ситуация, добавленная командой.";
    runner = { ...runner, phase: "active", state };
    render(<App />);
    expect(
      screen.getAllByText("Новая ситуация, добавленная командой.").length,
    ).toBeGreaterThan(0);
    expect(document.querySelector("blockquote")).not.toBeInTheDocument();
  });

  it("focuses a new visit even when a supported loop returns to the same node", () => {
    runner = { ...runner, phase: "active", state: parseSessionState(active) };
    const { rerender } = render(<App />);
    screen.getByRole("button", { name: "Объяснить" }).focus();
    const next = parseSessionState(nextState());
    next.current_node = parseSessionState(active).current_node;
    next.session.current_node_id = next.current_node.id;
    runner = { ...runner, state: next };
    rerender(<App />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveFocus();
  });

  it("distinguishes a server conflict from a disconnected network", () => {
    runner = {
      ...runner,
      phase: "active",
      state: parseSessionState(nextState()),
      error: "Ситуация уже изменилась.",
      connection: "online",
    };
    render(<App />);
    expect(screen.getByRole("alert")).toHaveTextContent("Ситуация обновлена");
    expect(screen.queryByText("Связь прервана")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Обновить состояние" }),
    ).toBeEnabled();
    expect(screen.getByRole("button", { name: "Предложить" })).toBeEnabled();
  });

  it("shows the saved timeout result, explanations and a return to the catalogue", () => {
    const state = parseSessionState(completedState());
    state.session.decisions[0].choice_id = "__timeout__";
    runner = { ...runner, phase: "completed", state };
    render(<App />);
    expect(
      screen.getByRole("heading", { name: "Сценарий завершён" }),
    ).toHaveFocus();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Время на решение истекло",
    );
    expect(screen.getByTestId("decision-history")).toHaveTextContent(
      "Переход выполнен",
    );
    expect(
      screen.queryByRole("button", { name: "Объяснить" }),
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Выбрать другую ситуацию" }),
    );
    expect(runner.leave).toHaveBeenCalledOnce();
  });
});
