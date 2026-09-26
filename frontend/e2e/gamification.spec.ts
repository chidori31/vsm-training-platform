import { expect, test, type Page } from "@playwright/test";

async function openProgress(page: Page) {
  await page
    .getByRole("navigation", { name: "Учебная смена" })
    .getByRole("button", { name: "Мой прогресс", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Учебный паспорт" }),
  ).toBeVisible();
  await expect(
    page.getByRole("combobox", { name: "Учебный проводник" }),
  ).toBeVisible();
}

async function passConflict(page: Page) {
  await page
    .getByRole("button", { name: "Продолжить смену", exact: true })
    .click();
  await page
    .getByRole("radio", { name: "Конфликт пассажиров", exact: true })
    .check();
  await page
    .getByRole("button", { name: "Начать сценарий", exact: true })
    .click();
  await page
    .getByRole("button", {
      name: "Спокойно выслушать обе стороны",
      exact: true,
    })
    .click();
  await page
    .getByRole("button", {
      name: "Предложить общий вариант и проверить согласие",
      exact: true,
    })
    .click();
  await expect(
    page.getByRole("heading", { name: "Сценарий завершён", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("+55 XP", { exact: true })).toBeVisible();
  await openProgress(page);
}

test("three real completions grow profile, unlock behavior awards and enter all organization rankings", async ({
  page,
}, testInfo) => {
  await page.goto("/");
  await openProgress(page);
  const changed = page.waitForResponse(
    (r) =>
      r.url().endsWith("/profiles/me/progress") &&
      r.request().method() === "GET",
  );
  await page
    .getByRole("combobox", { name: "Учебный проводник" })
    .selectOption("demo-north-02");
  const initial = (await (await changed).json()) as { id: string; xp: number };
  expect(initial.id).toBe("demo-north-02");
  await expect(
    page.getByRole("heading", { name: "Учебный проводник 02", exact: true }),
  ).toBeVisible();
  for (let i = 0; i < 3; i++) await passConflict(page);
  await expect(
    page.getByText(`${initial.xp + 165} XP`, { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("progressbar", { name: "Надёжная смена" }),
  ).toHaveAttribute("aria-valuenow", "3");
  await expect(
    page.getByRole("progressbar", { name: "Точно в срок" }),
  ).toHaveAttribute("aria-valuenow", "3");
  await expect(
    page.getByRole("progressbar", { name: "Мастер диалога" }),
  ).toHaveAttribute("aria-valuenow", "3");
  await page.screenshot({
    path: testInfo.outputPath("conductor-passport.png"),
    fullPage: true,
  });
  await page
    .getByRole("navigation", { name: "Учебная смена" })
    .getByRole("button", { name: "Рейтинг", exact: true })
    .click();
  for (const scope of ["Бригада", "Депо", "Компания"]) {
    await page.getByRole("button", { name: scope, exact: true }).click();
    const row = page
      .getByRole("row")
      .filter({ hasText: "Учебный проводник 02" });
    await expect(row).toContainText(String(initial.xp + 165));
    await expect(row).toContainText("Вы");
  }
  await page.screenshot({
    path: testInfo.outputPath("conductor-leaderboard.png"),
    fullPage: true,
  });
  await page.reload();
  await openProgress(page);
  await expect(
    page.getByRole("heading", { name: "Учебный проводник 02", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(`${initial.xp + 165} XP`, { exact: true }),
  ).toBeVisible();
});

test("mobile passport recovers a failed query, switches personas, and supports keyboard group filters", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  let fail = true;
  await page.route("**/api/v1/profiles/me/progress", async (route) => {
    if (fail) {
      fail = false;
      await route.abort("failed");
    } else await route.continue();
  });
  await page.goto("/");
  await page
    .getByRole("navigation", { name: "Учебная смена" })
    .getByRole("button", { name: "Мой прогресс", exact: true })
    .click();
  await expect(page.getByRole("alert")).toBeVisible();
  await page
    .getByRole("button", { name: "Повторить загрузку", exact: true })
    .click();
  await expect(
    page.getByRole("combobox", { name: "Учебный проводник" }),
  ).toBeVisible();
  await page
    .getByRole("combobox", { name: "Учебный проводник" })
    .selectOption("demo-other-05");
  await expect(
    page.getByRole("heading", { name: "Учебный проводник 05", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Учебная компания 02", { exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("passport-mobile.png"),
    fullPage: true,
  });
  await page
    .getByRole("navigation", { name: "Учебная смена" })
    .getByRole("button", { name: "Рейтинг", exact: true })
    .click();
  const company = page.getByRole("button", { name: "Компания", exact: true });
  await company.focus();
  await page.keyboard.press("Enter");
  await expect(company).toHaveAttribute("aria-pressed", "true");
  await expect(
    page.getByText("Учебная компания 02", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("row").filter({ hasText: "Учебный проводник 02" }),
  ).toHaveCount(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("leaderboard-mobile.png"),
    fullPage: true,
  });
});
