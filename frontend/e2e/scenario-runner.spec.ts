import { expect, test, type Locator, type Page } from "@playwright/test";
import type { CatalogPage, SessionState } from "../src/runner/types";

const firstChoice = "Спокойно выслушать обе стороны";
const secondChoice = "Предложить общий вариант и проверить согласие";
const firstExplanation =
  "В этой модели внимание к обеим сторонам открывает возможность договориться.";
const secondExplanation =
  "Предыдущее внимательное общение открыло дополнительный вариант выбора.";

interface SessionResponse {
  deadline: string | null;
  session: {
    id: string;
    current_node_id: string;
    started_at: string;
    decisions: {
      id: string;
      choice_id: string;
      explanation: string;
      decided_at: string;
    }[];
  };
  outcome?: string;
}

async function startScenario(page: Page, title = /конфликт пассажиров/i) {
  await page.goto("/");
  await page.getByRole("radio", { name: title }).check();
  const response = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/v1/sessions") &&
      response.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "Начать сценарий", exact: true })
    .click();
  const state = (await (await response).json()) as SessionResponse;
  await expect(page.getByTestId("scenario-runner")).toBeVisible();
  return state;
}

async function choose(page: Page, choice: string) {
  const response = page.waitForResponse(
    (response) =>
      /\/api\/v1\/sessions\/[^/]+\/decisions$/.test(response.url()) &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: choice, exact: true }).click();
  return (await (await response).json()) as SessionResponse;
}

async function tabTo(page: Page, target: Locator) {
  for (let attempt = 0; attempt < 25; attempt += 1) {
    if (
      await target.evaluate((element) => document.activeElement === element)
    ) {
      return;
    }
    await page.keyboard.press("Tab");
  }
  await expect(target).toBeFocused();
}

test("catalogue exposes loading and recovers after a network error", async ({
  page,
}) => {
  let release: () => void = () => {};
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  let attempts = 0;
  await page.route(/\/api\/v1\/scenarios(?:\?|$)/, async (route) => {
    attempts += 1;
    if (attempts === 1) {
      await pending;
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          error: {
            code: "service_unavailable",
            message: "Synthetic connection failure",
            details: [],
          },
          data: null,
        }),
      });
      return;
    }
    await route.continue();
  });
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await expect(
    page
      .getByRole("status")
      .filter({ hasText: "Подключаемся к учебной смене…" }),
  ).toContainText("Подключаемся к учебной смене…");
  release();
  await expect(page.getByRole("alert")).toBeVisible();
  await page
    .getByRole("button", { name: "Восстановить связь", exact: true })
    .click();
  await expect(
    page.getByRole("radio", { name: /конфликт пассажиров/i }),
  ).toBeVisible();
  expect(attempts).toBe(2);
});

test("normal decisions show server scores and explained history", async ({
  page,
}) => {
  await startScenario(page);
  const first = await choose(page, firstChoice);
  expect(first.session.decisions).toHaveLength(1);
  await expect(page.getByText(firstExplanation, { exact: true })).toBeVisible();
  const completed = await choose(page, secondChoice);
  expect(completed.session.decisions).toHaveLength(2);
  await expect(
    page.getByRole("heading", { name: "Сценарий завершён", exact: true }),
  ).toBeVisible();
  await expect(page.getByTestId("loyalty-value")).toHaveText("52");
  await expect(page.getByTestId("safety-value")).toHaveText("51");
  await expect(page.getByTestId("decision-history")).toContainText(
    firstExplanation,
  );
  await expect(page.getByTestId("decision-history")).toContainText(
    secondExplanation,
  );
  await expect(
    page.getByRole("heading", { name: /лидерборд|профиль/i }),
  ).toHaveCount(0);
});

test("the real 20 second deadline automatically takes the timeout branch", async ({
  page,
}) => {
  const state = await startScenario(page, /медицинская/i);
  expect(state.deadline).not.toBeNull();
  const remaining = Date.parse(state.deadline!) - Date.now();
  expect(remaining).toBeGreaterThan(18_000);
  const startedWaiting = Date.now();
  const expiredResponse = page.waitForResponse(async (response) => {
    if (
      !response.url().endsWith(`/api/v1/sessions/${state.session.id}`) ||
      response.request().method() !== "GET"
    )
      return false;
    const body = (await response.json()) as SessionResponse;
    return body.session.current_node_id === "expired";
  });
  await expect(
    page.getByRole("heading", { name: "Сценарий завершён", exact: true }),
  ).toBeVisible({ timeout: 30_000 });
  expect(Date.now() - startedWaiting).toBeGreaterThanOrEqual(remaining - 1000);
  const expired = (await (await expiredResponse).json()) as SessionResponse;
  expect(expired.session.decisions).toHaveLength(1);
  expect(expired.session.decisions[0].choice_id).toBe("__timeout__");
  expect(
    Date.parse(expired.session.decisions[0].decided_at) -
      Date.parse(expired.session.started_at),
  ).toBeGreaterThanOrEqual(20_000);
  await expect(
    page.getByText("Время на решение истекло", { exact: true }),
  ).toBeVisible();
  await expect(page.getByTestId("safety-value")).toHaveText("48");
  await expect(page.getByTestId("decision-history")).toContainText(
    "Таймер истёк без решения.",
  );
});

test("a lost decision acknowledgement replays one intent and records one decision", async ({
  page,
}) => {
  const requests: unknown[] = [];
  let committed: SessionResponse | undefined;
  let replay: SessionResponse | undefined;
  await page.route("**/api/v1/sessions/*/decisions", async (route) => {
    if (route.request().method() !== "POST") {
      await route.continue();
      return;
    }
    requests.push(route.request().postDataJSON());
    const response = await route.fetch();
    if (requests.length === 1) {
      committed = (await response.json()) as SessionResponse;
      await route.abort("failed");
    } else {
      replay = (await response.json()) as SessionResponse;
      await route.fulfill({ response });
    }
  });
  await startScenario(page);
  await page.getByRole("button", { name: firstChoice, exact: true }).click();
  const reconnect = page.getByRole("button", {
    name: "Восстановить связь",
    exact: true,
  });
  await expect(reconnect).toBeVisible();
  expect(committed?.session.decisions).toHaveLength(1);
  await reconnect.click();
  await expect(
    page.getByRole("button", { name: secondChoice, exact: true }),
  ).toBeEnabled();
  expect(requests).toHaveLength(2);
  expect(requests[1]).toEqual(requests[0]);
  expect(replay?.outcome).toBe("duplicate");
  expect(replay?.session.decisions).toHaveLength(1);
  await expect(page.getByText(firstExplanation, { exact: true })).toHaveCount(
    1,
  );
  await choose(page, secondChoice);
  await expect(page.getByTestId("loyalty-value")).toHaveText("52");
  await expect(page.getByTestId("safety-value")).toHaveText("51");
});

test("refresh restores the same session and its confirmed decision", async ({
  page,
}) => {
  const started = await startScenario(page);
  await choose(page, firstChoice);
  const restoredResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/api/v1/sessions/${started.session.id}`) &&
      response.request().method() === "GET",
  );
  await page.reload();
  const restored = (await (await restoredResponse).json()) as SessionResponse;
  expect(restored.session.id).toBe(started.session.id);
  expect(restored.session.decisions).toHaveLength(1);
  await expect(
    page.getByRole("button", { name: secondChoice, exact: true }),
  ).toBeVisible();
  await choose(page, secondChoice);
  await expect(
    page.getByRole("heading", { name: "Сценарий завершён", exact: true }),
  ).toBeVisible();
});

test("refresh replays a persisted intent after its acknowledgement was lost", async ({
  page,
}) => {
  const requests: unknown[] = [];
  await page.route("**/api/v1/sessions/*/decisions", async (route) => {
    if (route.request().method() !== "POST") {
      await route.continue();
      return;
    }
    requests.push(route.request().postDataJSON());
    const response = await route.fetch();
    if (requests.length === 1) {
      await route.abort("failed");
    } else {
      await route.fulfill({ response });
    }
  });
  const started = await startScenario(page);
  await page.getByRole("button", { name: firstChoice, exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Восстановить связь", exact: true }),
  ).toBeVisible();
  const replayResponse = page.waitForResponse(
    (response) =>
      response
        .url()
        .endsWith(`/api/v1/sessions/${started.session.id}/decisions`) &&
      response.request().method() === "POST",
  );
  await page.reload();
  const replay = (await (await replayResponse).json()) as SessionResponse;
  expect(requests).toHaveLength(2);
  expect(requests[1]).toEqual(requests[0]);
  expect(replay.outcome).toBe("duplicate");
  expect(replay.session.id).toBe(started.session.id);
  expect(replay.session.decisions).toHaveLength(1);
  await expect(
    page.getByRole("button", { name: secondChoice, exact: true }),
  ).toBeEnabled();
  await expect(page.getByText(firstExplanation, { exact: true })).toHaveCount(
    1,
  );
});

test("mobile keeps unknown long unbroken titles and choices visible without overflow", async ({
  page,
}) => {
  const scenarioId = "e2e-unknown-long-content";
  const title = "НеизвестнаяРабочаяСитуация".repeat(20);
  const choiceText = "ПродолжитьНовыйУчебныйРазговор".repeat(20);
  const context = "ИсходноеОписаниеНеизвестнойСитуации".repeat(20);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route(/\/api\/v1\/scenarios(?:\?|$)/, async (route) => {
    const response = await route.fetch();
    const catalog = (await response.json()) as CatalogPage;
    const source = catalog.items.find(
      (item) => item.id === "demo-passenger-conflict",
    )!;
    await route.fulfill({
      response,
      json: {
        ...catalog,
        total: 1,
        items: [{ ...source, id: scenarioId, title }],
      },
    });
  });
  await page.route(/\/api\/v1\/sessions(?:\/[^/?]+)?$/, async (route) => {
    const response = await route.fetch(
      route.request().method() === "POST"
        ? {
            postData: JSON.stringify({
              ...route.request().postDataJSON(),
              scenario_id: "demo-passenger-conflict",
            }),
          }
        : undefined,
    );
    const state = (await response.json()) as SessionState;
    state.session.scenario_id = scenarioId;
    state.current_node.text = context;
    state.available_choices[0].text = choiceText;
    await route.fulfill({ response, json: state });
  });
  await page.goto("/");
  const titleHeading = page.getByRole("heading", { name: title, exact: true });
  await expect(titleHeading).toBeVisible();
  await expect(titleHeading).toHaveText(title);
  expect(
    await titleHeading.evaluate(
      (element) => element.scrollWidth <= element.clientWidth,
    ),
  ).toBe(true);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.getByRole("radio", { name: title, exact: true }).check();
  await page
    .getByRole("button", { name: "Начать сценарий", exact: true })
    .click();
  const choice = page.getByRole("button", { name: choiceText, exact: true });
  await expect(choice).toBeVisible();
  await expect(choice.getByText(choiceText, { exact: true })).toHaveText(
    choiceText,
  );
  await expect(page.locator(".scene-statement")).toHaveText(context);
  expect(
    await choice.evaluate(
      (element) => element.scrollWidth <= element.clientWidth,
    ),
  ).toBe(true);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});

test("desktop working scene fits its viewport", async ({ page }, testInfo) => {
  await startScenario(page);
  await choose(page, firstChoice);
  await expect(
    page.getByRole("button", { name: secondChoice, exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("scenario-desktop.png"),
    fullPage: true,
  });
});

test("mobile keyboard flow respects reduced motion and has no horizontal overflow", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const scenario = page.getByRole("radio", { name: /конфликт пассажиров/i });
  await scenario.focus();
  await page.keyboard.press("Space");
  await expect(scenario).toBeChecked();
  const start = page.getByRole("button", {
    name: "Начать сценарий",
    exact: true,
  });
  await tabTo(page, start);
  await page.keyboard.press("Enter");
  const choice = page.getByRole("button", { name: firstChoice, exact: true });
  await expect(choice).toBeVisible();
  await tabTo(page, choice);
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("button", { name: secondChoice, exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    ),
  ).toBe(true);
  const layout = await page.evaluate(() => ({
    viewport: window.innerWidth,
    width: document.documentElement.scrollWidth,
    animations: document
      .getAnimations()
      .filter((animation) => animation.playState === "running").length,
  }));
  expect(layout.width).toBeLessThanOrEqual(layout.viewport);
  expect(layout.animations).toBe(0);
  await page.evaluate(() => {
    if (document.activeElement instanceof HTMLElement)
      document.activeElement.blur();
    window.scrollTo(0, 0);
  });
  await page.screenshot({
    path: testInfo.outputPath("scenario-mobile.png"),
    fullPage: true,
  });
});
