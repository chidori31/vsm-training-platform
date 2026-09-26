import { expect, test, type Page } from "@playwright/test";

async function events(page: Page) {
  await page
    .getByRole("navigation", { name: "Учебная смена" })
    .getByRole("button", { name: "События", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Продолжить маршрут" }),
  ).toBeVisible();
  await expect(page.getByText(/^Непрочитанных:/)).toBeVisible();
}

test("internal notifications retain read/unread after reload and fit a mobile screen", async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await events(page);
  const notice = page.locator(".notification-list > li").first();
  await expect(notice).toBeVisible();
  if (
    await notice
      .getByRole("button", { name: "Отметить непрочитанным", exact: true })
      .count()
  ) {
    await notice
      .getByRole("button", { name: "Отметить непрочитанным", exact: true })
      .click();
    await expect(
      notice.getByRole("button", { name: "Отметить прочитанным", exact: true }),
    ).toBeVisible();
  }
  const mutation = page.waitForResponse(
    (r) =>
      /\/notifications\/[^/]+\/read$/.test(r.url()) &&
      r.request().method() === "PUT",
  );
  await notice
    .getByRole("button", { name: "Отметить прочитанным", exact: true })
    .click();
  const result = await (await mutation).json();
  expect(result.read_at).toBeTruthy();
  await expect(
    notice.getByRole("button", { name: "Отметить непрочитанным", exact: true }),
  ).toBeVisible();
  await page.reload();
  await events(page);
  await expect(
    notice.getByRole("button", { name: "Отметить непрочитанным", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: info.outputPath("events-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: info.outputPath("events-mobile.png"),
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await notice
    .getByRole("button", { name: "Отметить непрочитанным", exact: true })
    .focus();
  await page.keyboard.press("Enter");
  await expect(
    notice.getByRole("button", { name: "Отметить прочитанным", exact: true }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});

test("new scenario completions feed challenge progress through the real API", async ({
  page,
}) => {
  await page.goto("/");
  await events(page);
  for (const title of ["Помощь при посадке", "Забытая вещь"]) {
    await page
      .getByRole("button", { name: `Открыть: ${title}`, exact: true })
      .first()
      .click();
    await expect(
      page.getByRole("radio", { name: new RegExp(title) }),
    ).toBeChecked();
    await page
      .getByRole("button", { name: "Начать сценарий", exact: true })
      .click();
    await expect(
      page.getByRole("navigation", { name: "Учебная смена" }),
    ).not.toBeVisible();
    await page
      .getByRole("button", {
        name:
          title === "Помощь при посадке"
            ? "Уточнить, какая помощь нужна, и освободить проход"
            : "Сохранить дистанцию и сообщить ответственному сотруднику",
        exact: true,
      })
      .click();
    await page
      .getByRole("button", {
        name: "Спокойно объяснить согласованные следующие шаги",
        exact: true,
      })
      .click();
    await expect(
      page.getByRole("heading", { name: "Сценарий завершён", exact: true }),
    ).toBeVisible();
    await events(page);
  }
  const day = page.locator(".challenge-entry").filter({
    has: page.getByRole("heading", {
      name: "Две ситуации за сутки",
      exact: true,
    }),
  });
  await expect(day.getByRole("progressbar")).toHaveAttribute(
    "aria-valuenow",
    "2",
  );
  await expect(day.locator(".challenge-status")).toHaveText("Выполнено");
});
