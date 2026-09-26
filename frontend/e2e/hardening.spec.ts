import { expect, test } from "@playwright/test";

test("happy path keeps commands free of scores and reaches a durable two-decision debrief", async ({
  page,
}) => {
  const commands: Record<string, unknown>[] = [];
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      /\/sessions\/[^/]+\/decisions$/.test(request.url())
    ) {
      commands.push(request.postDataJSON() as Record<string, unknown>);
    }
  });
  await page.goto("/");
  await page.getByRole("radio", { name: /забытая вещь/i }).check();
  await page
    .getByRole("button", { name: "Начать сценарий", exact: true })
    .click();
  await page
    .getByRole("button", {
      name: "Сохранить дистанцию и сообщить ответственному сотруднику",
      exact: true,
    })
    .click();
  await expect(page.getByTestId("safety-value")).toHaveText("55");
  const debrief = page.waitForResponse((response) =>
    /\/sessions\/[^/]+\/debrief$/.test(response.url()),
  );
  await page
    .getByRole("button", {
      name: "Спокойно объяснить согласованные следующие шаги",
      exact: true,
    })
    .click();
  await expect(
    page.getByRole("heading", { name: "Сценарий завершён", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Разбор решений", exact: true }),
  ).toBeVisible();
  const response = await debrief;
  expect(response.headers()["cache-control"]).toBe("no-store");
  const body = await response.json();
  expect(
    body.decisions.map((row: { choice_id: string }) => row.choice_id),
  ).toEqual(["coordinate", "explain"]);
  expect(commands).toHaveLength(2);
  for (const command of commands) {
    expect(Object.keys(command).sort()).toEqual([
      "choice_id",
      "decision_id",
      "expected_sequence",
      "node_id",
    ]);
  }
  await expect(page.getByTestId("decision-history")).toContainText(
    "Понятное объяснение уменьшает неопределённость",
  );
  const restored = page.waitForResponse((r) =>
    r.url().endsWith(`/sessions/${body.session_id}/debrief`),
  );
  await page.reload();
  expect(await (await restored).json()).toEqual(body);
  expect(commands).toHaveLength(2);
});
