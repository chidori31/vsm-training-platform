import { expect, test, type Page } from "@playwright/test";

async function profile(page: Page) {
  await page
    .getByRole("navigation", { name: "Учебная смена" })
    .getByRole("button", { name: "Мой прогресс", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Учебный паспорт" }),
  ).toBeVisible();
}

async function start(page: Page, scenario: RegExp) {
  await page.getByRole("radio", { name: scenario }).check();
  await page
    .getByRole("button", { name: "Начать сценарий", exact: true })
    .click();
}

interface Debrief {
  session_id: string;
  decisions: {
    sequence: number;
    choice_id: string;
    was_timeout: boolean;
    loyalty: { delta: number; before: number; after: number };
    safety: { delta: number; before: number; after: number };
    alternatives: { choice_id: string; available: boolean }[];
  }[];
}

test("real completion explains both decisions and alternatives and survives refresh", async ({
  page,
}, info) => {
  await page.goto("/");
  await start(page, /конфликт пассажиров/i);
  await page
    .getByRole("button", {
      name: "Спокойно выслушать обе стороны",
      exact: true,
    })
    .click();
  const response = page.waitForResponse((r) =>
    /\/sessions\/[^/]+\/debrief$/.test(r.url()),
  );
  await page
    .getByRole("button", {
      name: "Предложить общий вариант и проверить согласие",
      exact: true,
    })
    .click();
  await expect(
    page.getByRole("heading", { name: "Разбор решений", exact: true }),
  ).toBeVisible();
  const body = (await (await response).json()) as Debrief;
  expect(body.decisions.map((d) => d.choice_id)).toEqual(["listen", "agree"]);
  expect(body.decisions[0].loyalty).toMatchObject({
    before: 50,
    after: 52,
    delta: 2,
  });
  expect(body.decisions[0].safety.delta).toBe(0);
  expect(body.decisions[1].safety.delta).toBe(1);
  const timeline = page.getByTestId("decision-history");
  await expect(timeline).toContainText("Спокойно выслушать обе стороны");
  await expect(timeline).toContainText(
    "Предложить общий вариант и проверить согласие",
  );
  const alternatives = timeline.getByText("Альтернативы решения 1", {
    exact: true,
  });
  await alternatives.focus();
  await page.keyboard.press("Enter");
  await expect(
    timeline.getByText("Сразу обвинить одного из участников", { exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: info.outputPath("debrief-desktop.png"),
    fullPage: true,
  });
  const restored = page.waitForResponse((r) =>
    r.url().endsWith(`/sessions/${body.session_id}/debrief`),
  );
  await page.reload();
  const replay = (await (await restored).json()) as Debrief;
  expect(replay).toEqual(body);
  await expect(
    page.getByRole("heading", { name: "Разбор решений", exact: true }),
  ).toBeVisible();
});

test("mobile repeated service problems appear in real competency analytics with retry", async ({
  page,
}, info) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await profile(page);
  await page
    .getByRole("combobox", { name: "Учебный проводник" })
    .selectOption("demo-north-03");
  await expect(
    page.getByRole("heading", { name: "Учебный проводник 03", exact: true }),
  ).toBeVisible();
  for (let i = 0; i < 3; i++) {
    await page
      .getByRole("button", { name: "Продолжить смену", exact: true })
      .click();
    await start(page, /сервисная ситуация/i);
    await page
      .getByRole("button", {
        name: "Пообещать недоступную услугу",
        exact: true,
      })
      .click();
    await expect(
      page.getByRole("heading", { name: "Разбор решений", exact: true }),
    ).toBeVisible();
    await expect(page.getByTestId("decision-history")).toContainText(
      "Пообещать недоступную услугу",
    );
    await profile(page);
  }
  let fail = true;
  await page.route("**/api/v1/analytics/me/competencies", async (route) => {
    if (fail) {
      fail = false;
      await route.abort("failed");
    } else await route.continue();
  });
  await page
    .getByRole("button", { name: "Аналитика компетенций", exact: true })
    .click();
  const analytics = page.getByTestId("competency-analytics");
  await expect(analytics.getByRole("alert")).toBeVisible();
  const response = page.waitForResponse(
    (r) => r.url().endsWith("/analytics/me/competencies") && r.ok(),
  );
  await analytics.getByRole("button", { name: /повторить/i }).click();
  const body = (await (await response).json()) as {
    competencies: {
      competency_id: string;
      negative_decisions: number;
      trend: { delta: number }[];
    }[];
    patterns: { code: string; session_count: number; recurring: boolean }[];
  };
  const service = body.competencies.find((c) => c.competency_id === "service")!;
  expect(service.negative_decisions).toBeGreaterThanOrEqual(3);
  expect(service.trend.slice(-3).map((p) => p.delta)).toEqual([-1, -1, -1]);
  const pattern = body.patterns.find(
    (p) => p.code === "competency_regression",
  )!;
  expect(pattern.session_count).toBeGreaterThanOrEqual(3);
  expect(pattern.recurring).toBe(true);
  await expect(page.getByTestId("competency-analysis-service")).toContainText(
    "Сервис",
  );
  await expect(page.getByTestId("learning-patterns")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: info.outputPath("analytics-mobile.png"),
    fullPage: true,
  });
});
