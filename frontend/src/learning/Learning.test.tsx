import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Debrief } from "./Debrief";
import { CompetencyAnalytics } from "./CompetencyAnalytics";
import { analyticsFixture, debriefFixture } from "./testFixtures";

describe("decision debrief", () => {
  it("connects saved choices to consequences, both score reasons, skills and server advice", async () => {
    render(
      <Debrief
        read={async (path) =>
          path === "/sessions/session-1/debrief" ? debriefFixture() : null
        }
        sessionId="session-1"
        identityId="one"
      />,
    );
    expect(await screen.findByText("Объяснить порядок действий")).toBeVisible();
    const history = screen.getByTestId("decision-history");
    expect(history).toHaveTextContent("Пассажир просит помощь.");
    expect(history).toHaveTextContent("Пассажир знает, куда обратиться.");
    expect(history).toHaveTextContent("Решение не меняет безопасность.");
    expect(history).toHaveTextContent("Верхняя граница ограничила прирост.");
    expect(history).toHaveTextContent("Вы объяснили пассажиру следующий шаг.");
    expect(history).toHaveTextContent("Попробуйте предложить сопровождение");
    const disclosure = screen.getByText("Альтернативы решения 1");
    expect(disclosure.closest("details")).not.toHaveAttribute("open");
    fireEvent.click(disclosure);
    expect(screen.getByText("Проводить пассажира")).toBeVisible();
    expect(screen.getByText("Недоступно в момент решения")).toBeVisible();
    expect(
      within(screen.getByText("Проводить пассажира").closest("li")!).getByText(
        "Можно попробовать",
      ),
    ).toBeVisible();
    expect(
      within(
        screen.getByText("Открыть служебный проход").closest("li")!,
      ).queryByText("Можно попробовать"),
    ).not.toBeInTheDocument();
  });
  it("marks a timeout explicitly while preserving the server suggestion", async () => {
    const data = debriefFixture();
    data.summary.timeout_count = 1;
    data.decisions[0].was_timeout = true;
    data.decisions[0].choice_id = "__timeout__";
    data.decisions[0].choice_text = "Время истекло";
    data.decisions[0].suggestion.text = "Примите решение до истечения времени.";
    render(
      <Debrief
        read={async () => data}
        sessionId="session-1"
        identityId="one"
      />,
    );
    expect(
      await screen.findByText("Переход по истечении времени"),
    ).toBeVisible();
    expect(
      screen.getByText("Примите решение до истечения времени."),
    ).toBeVisible();
  });
  it("offers a recoverable loading error and rejects a response for another session", async () => {
    let request = 0;
    const read = async () => {
      request++;
      if (request === 1) throw new Error("Нет связи с разбором");
      const data = debriefFixture();
      if (request === 2) data.session_id = "wrong-session";
      return data;
    };
    render(<Debrief read={read} sessionId="session-1" identityId="one" />);
    expect(screen.getByRole("status")).toHaveTextContent("Загружаем разбор");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Нет связи с разбором",
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Повторить загрузку разбора" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("некорректный");
    fireEvent.click(
      screen.getByRole("button", { name: "Повторить загрузку разбора" }),
    );
    expect(await screen.findByText("Объяснить порядок действий")).toBeVisible();
  });
});

describe("competency analytics", () => {
  it("shows supplied evidence, trends, recurring patterns and per-version scenario statistics", async () => {
    render(
      <CompetencyAnalytics
        read={async (path) =>
          path === "/analytics/me/competencies" ? analyticsFixture() : null
        }
        identityId="one"
      />,
    );
    const skill = await screen.findByTestId(
      "competency-analysis-communication",
    );
    expect(skill).toHaveTextContent("Сильная сторона");
    expect(skill).toHaveTextContent("Наблюдений: 5");
    expect(skill).toHaveTextContent("Попыток с наблюдениями: 3");
    expect(
      screen.getByTestId("competency-analysis-coordination"),
    ).toHaveTextContent("Зона развития");
    const insufficient = screen.getByTestId("competency-analysis-service");
    expect(insufficient).toHaveTextContent("Недостаточно данных");
    expect(insufficient).not.toHaveTextContent("Зона развития");
    fireEvent.click(within(skill).getByText("Динамика по попыткам"));
    expect(within(skill).getByRole("table")).toHaveTextContent("+2");
    expect(screen.getByTestId("learning-patterns")).toHaveTextContent(
      "Решений: 2 · попыток: 2",
    );
    expect(screen.getByTestId("learning-patterns")).toHaveTextContent(
      "Сначала оцените срочность запроса.",
    );
    expect(
      screen.getByRole("table", { name: "Статистика сценариев" }),
    ).toHaveTextContent("Новая ситуация");
    expect(
      screen.getByRole("table", { name: "Статистика сценариев" }),
    ).toHaveTextContent("Нет завершений");
  });
  it("explains an empty learning history without inventing weaknesses", async () => {
    const data = {
      rule_version: 1,
      total_sessions: 0,
      completed_sessions: 0,
      active_sessions: 0,
      decision_count: 0,
      timeout_count: 0,
      competencies: [],
      strengths: [],
      weaknesses: [],
      patterns: [],
      scenarios: [],
    };
    render(<CompetencyAnalytics read={async () => data} identityId="one" />);
    expect(
      await screen.findByText(/Пока нет завершённых попыток/),
    ).toBeVisible();
    expect(screen.getByText("Повторяющихся паттернов пока нет.")).toBeVisible();
    expect(screen.queryByText("Зона развития")).not.toBeInTheDocument();
  });
  it("cancels old identity reads and never displays a late response for the previous conductor", async () => {
    let completeFirst!: (value: unknown) => void;
    let firstSignal!: AbortSignal;
    let calls = 0;
    const read = async (_path: string, signal: AbortSignal) => {
      calls++;
      if (calls === 1) {
        firstSignal = signal;
        return new Promise((resolve) => {
          completeFirst = resolve;
        });
      }
      return {
        ...analyticsFixture(),
        competencies: [],
        strengths: [],
        weaknesses: [],
      };
    };
    const { rerender } = render(
      <CompetencyAnalytics read={read} identityId="one" />,
    );
    rerender(<CompetencyAnalytics read={read} identityId="two" />);
    expect(firstSignal.aborted).toBe(true);
    await screen.findByTestId("learning-patterns");
    await act(async () => {
      completeFirst(analyticsFixture());
    });
    expect(
      screen.queryByTestId("competency-analysis-communication"),
    ).not.toBeInTheDocument();
  });
  it("does not retain already loaded conclusions after the identity changes", async () => {
    let calls = 0;
    const read = async () =>
      ++calls === 1 ? analyticsFixture() : new Promise<unknown>(() => {});
    const { rerender } = render(
      <CompetencyAnalytics read={read} identityId="one" />,
    );
    await screen.findByTestId("competency-analysis-communication");
    rerender(<CompetencyAnalytics read={read} identityId="two" />);
    expect(
      screen.queryByTestId("competency-analysis-communication"),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Загружаем аналитику");
  });
  it("retries invalid analytics without displaying malformed facts", async () => {
    let calls = 0;
    render(
      <CompetencyAnalytics
        read={async () =>
          ++calls === 1
            ? { ...analyticsFixture(), rule_version: 9 }
            : analyticsFixture()
        }
        identityId="one"
      />,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("некорректный");
    expect(
      screen.queryByTestId("competency-analysis-communication"),
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Повторить загрузку аналитики" }),
    );
    expect(
      await screen.findByTestId("competency-analysis-communication"),
    ).toBeVisible();
  });
});
