import { expect, test } from "@playwright/test";

test("a real three-event shift advances in order, survives reload, and opens its decision debrief", async ({
  page,
  request,
}, info) => {
  // Finish any synthetic route left by an interrupted prior run through the same public API.
  const login = await request.post("/api/v1/auth/demo", {
    data: { persona_id: "demo-employee" },
  });
  expect(login.ok()).toBe(true);
  const identity = (await login.json()) as { access_token: string };
  const headers = { Authorization: `Bearer ${identity.access_token}` };
  const previous = await request.get("/api/v1/shifts/current", { headers });
  let old = (
    (await previous.json()) as {
      shift: {
        id: string;
        status: string;
        current_step: number;
        current_session_id: string;
      } | null;
    }
  ).shift;
  for (let stage = 0; old?.status === "active" && stage < 3; stage++) {
    for (let i = 0; i < 8; i++) {
      const state = (await (
        await request.get(`/api/v1/sessions/${old.current_session_id}`, {
          headers,
        })
      ).json()) as {
        session: { status: string };
        current_node: { id: string };
        expected_sequence: number;
        available_choices: { id: string }[];
      };
      if (state.session.status === "completed") break;
      const decision = await request.post(
        `/api/v1/sessions/${old.current_session_id}/decisions`,
        {
          headers,
          data: {
            decision_id: crypto.randomUUID(),
            node_id: state.current_node.id,
            choice_id: state.available_choices[0].id,
            expected_sequence: state.expected_sequence,
          },
        },
      );
      expect(decision.ok()).toBe(true);
    }
    const advance = await request.post(`/api/v1/shifts/${old.id}/advance`, {
      headers,
      data: {
        command_id: crypto.randomUUID(),
        expected_step: old.current_step,
        session_id: old.current_session_id,
      },
    });
    expect(advance.ok()).toBe(true);
    old = (await advance.json()) as NonNullable<typeof old>;
  }
  await page.goto("/");
  await expect(
    page.getByRole("region", { name: "Рабочая смена" }),
  ).toBeVisible();
  const shiftPanel = page.getByRole("region", { name: "Рабочая смена" });
  const start = shiftPanel.getByRole("button", {
    name: /Заступить|Открыть новую смену/,
  });
  const resume = shiftPanel.getByRole("button", {
    name: "Продолжить учебную смену",
  });
  if (await resume.isVisible()) await resume.click();
  else await start.click();
  await expect(page.getByTestId("scenario-runner")).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: info.outputPath("shift-route-desktop.png"),
    fullPage: true,
  });
  await page.reload();
  await expect(
    page.getByRole("list", { name: "Маршрут учебной смены" }),
  ).toBeVisible();
  const seenSessions = new Set<string>();
  const commands: Record<string, unknown>[] = [];
  page.on("request", (request) => {
    if (/\/decisions$/.test(request.url()) && request.method() === "POST")
      commands.push(request.postDataJSON() as Record<string, unknown>);
  });
  for (let stage = 0; stage < 3; stage++) {
    for (let choice = 0; choice < 8; choice++) {
      const next = shiftPanel.getByRole("button", {
        name: /К следующему событию|Завершить смену/,
      });
      if (await next.isVisible()) break;
      const button = page.locator(".choices .choice").first();
      await expect(button).toBeEnabled();
      const response = page.waitForResponse(
        (r) => /\/decisions$/.test(r.url()) && r.request().method() === "POST",
      );
      await button.click();
      const body = (await (await response).json()) as {
        session: { id: string; status: string };
      };
      seenSessions.add(body.session.id);
      if (body.session.status === "completed") break;
    }
    const advance = shiftPanel.getByRole("button", {
      name: /К следующему событию|Завершить смену/,
    });
    await expect(advance).toBeEnabled();
    await advance.click();
    if (stage < 2)
      await expect(page.locator(".choices .choice").first()).toBeEnabled();
  }
  await expect(
    shiftPanel.getByRole("heading", {
      name: "Смена завершена. Разберём результат.",
    }),
  ).toBeVisible();
  expect(seenSessions.size).toBeGreaterThanOrEqual(2);
  expect(commands.length).toBeGreaterThanOrEqual(4);
  for (const command of commands)
    expect(Object.keys(command).sort()).toEqual([
      "choice_id",
      "decision_id",
      "expected_sequence",
      "node_id",
    ]);
  await shiftPanel.locator(".shift-review-button").first().click();
  await expect(shiftPanel.getByTestId("decision-history")).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: info.outputPath("shift-summary-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(shiftPanel).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: info.outputPath("shift-summary-mobile.png"),
    fullPage: true,
  });
  await page.reload();
  await expect(
    shiftPanel.getByRole("heading", {
      name: "Смена завершена. Разберём результат.",
    }),
  ).toBeVisible();
});
