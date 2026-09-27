import { execFileSync } from "node:child_process";
import { expect, test } from "@playwright/test";
import type { Training } from "../src/training/contracts";
import type { Action } from "../src/simulation/contracts";

function advanceClock(id: string, seconds: number, owner = "demo-employee") {
  const container = process.env.PLAYWRIGHT_SIMULATION_CONTAINER;
  if (!container) throw new Error("Explicit synthetic Docker fixture required");
  const script = `
import json,os,sys
from datetime import timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.application.training import TrainingService
from app.persistence.training import StoredTraining
args=json.load(sys.stdin)
engine=create_engine(os.environ['DATABASE_URL'])
with Session(engine) as db:
    row=db.get(StoredTraining,args['id'])
    assert row and row.employee_id == args['owner'] and args['owner'].startswith('demo-')
    moment=row.started_at+timedelta(seconds=args['seconds'])
TrainingService(engine,clock=lambda db: moment).get(args['id'],args['owner'])
engine.dispose()
`;
  execFileSync("docker", ["exec", "-i", container, "python", "-c", script], {
    input: JSON.stringify({ id, seconds, owner }),
    timeout: 20_000,
    stdio: ["pipe", "pipe", "pipe"],
  });
}

test("training: timed decisions, cancellation, debrief, historical knowledge and independent alternative", async ({
  page,
  request,
}, info) => {
  test.skip(
    !process.env.PLAYWRIGHT_SIMULATION_CONTAINER,
    "Explicit local synthetic clock fixture required",
  );
  test.setTimeout(180_000);
  const login = await request.post("/api/v1/auth/demo", {
    data: { persona_id: "demo-employee" },
  });
  expect(login.ok()).toBe(true);
  const headers = {
    Authorization: `Bearer ${(await login.json()).access_token}`,
  };
  const previous = (
    await (await request.get("/api/v1/training/current", { headers })).json()
  ).simulation as Training | null;
  if (previous?.status === "active")
    advanceClock(previous.id, previous.duration_seconds);
  await page.goto("/");
  await page.getByRole("radio", { name: /Демонстрация/ }).check();
  await page.screenshot({
    path: info.outputPath("training-modes.png"),
    fullPage: true,
  });
  const creation = page.waitForResponse(
    (r) =>
      r.url().endsWith("/training/runs") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: /Начать: Демонстрация/ }).click();
  const created = await creation;
  expect(created.ok(), await created.text()).toBe(true);
  let state = (await created.json()) as Training;
  const sourceId = state.id;
  expect(state.duration_seconds).toBe(240);
  expect(state.reward_eligible).toBe(false);
  async function time(seconds: number) {
    advanceClock(state.id, seconds);
    state = (await (
      await request.get(`/api/v1/training/runs/${state.id}`, { headers })
    ).json()) as Training;
    await page.reload();
    if (!state.pending_action)
      await expect(
        page.getByRole("region", { name: "Выполняемое действие" }),
      ).toHaveCount(0);
    await expect(
      page.getByRole("heading", { name: state.title, exact: true }),
    ).toBeVisible();
  }
  async function act(action: Action, finish = true) {
    expect(action.enabled, action.reason ?? action.label).toBe(true);
    const response = page.waitForResponse(
      (r) =>
        r.url().endsWith(`/training/runs/${state.id}/actions`) &&
        r.request().method() === "POST",
    );
    await page
      .getByRole("button", {
        name: new RegExp(action.label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")),
      })
      .click();
    const received = await response;
    expect(received.ok(), await received.text()).toBe(true);
    state = (await received.json()) as Training;
    if (finish && state.pending_action) {
      await expect(
        page.getByRole("region", { name: "Выполняемое действие" }),
      ).toBeVisible();
      await time(state.pending_action.completes_at_seconds);
    }
  }
  async function global(id: string, zone?: string) {
    const a = state.actions.find(
      (a) => a.id === id && (!zone || a.zone_id === zone),
    );
    expect(a).toBeDefined();
    await act(a!);
  }
  async function visit(id: string) {
    const incident = state.incidents.find((i) => i.id === id)!;
    const zone = state.zones.find((z) => z.id === incident.zone_id)!;
    await page
      .getByRole("group", { name: "Схема вагона" })
      .getByRole("button", { name: new RegExp(zone.title) })
      .click();
    if (state.location !== zone.id) {
      await global("move", zone.id);
      await page
        .getByRole("group", { name: "Схема вагона" })
        .getByRole("button", { name: new RegExp(zone.title) })
        .click();
    }
    const tab = page.getByRole("button", { name: incident.title, exact: true });
    if (await tab.isVisible()) await tab.click();
  }
  async function incidentAction(id: string, verb: string, finish = true) {
    await visit(id);
    const a = state.incidents
      .find((i) => i.id === id)
      ?.actions.find((a) => a.id === verb);
    expect(a).toBeDefined();
    await act(a!, finish);
  }
  await global("take:radio");
  await global("take:flashlight");
  await incidentAction("request", "talk", false);
  expect(state.pending_action?.action_id).toBe("talk");
  const cancelled = page.waitForResponse(
    (r) =>
      r.url().endsWith(`/training/runs/${state.id}/actions`) &&
      r.request().method() === "POST",
  );
  await page.getByRole("button", { name: /Прервать действие/ }).click();
  expect((await cancelled).ok()).toBe(true);
  await time(state.elapsed_seconds + 1);
  expect(state.pending_action).toBeNull();
  await incidentAction("request", "talk");
  await incidentAction("request", "inspect");
  await incidentAction("request", "verify");
  await incidentAction("request", "assist");
  await incidentAction("odor", "inspect");
  await incidentAction("odor", "restrict");
  await incidentAction("odor", "verify");
  await incidentAction("odor", "contact");
  expect(state.incidents.length).toBeGreaterThan(1);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: info.outputPath("training-parallel.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    )
    .toBe(true);
  await page.screenshot({
    path: info.outputPath("training-mobile.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await time(240);
  await expect(
    page.getByRole("heading", { name: "Основания оценки" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Вернуться к моменту решения" }),
  ).toBeVisible();
  expect(state.xp).toBe(0);
  const replay = page.waitForResponse((r) =>
    r.url().includes(`/training/runs/${sourceId}/replay?at_seconds=10`),
  );
  await page.getByRole("slider", { name: "Момент воспроизведения" }).fill("10");
  const replayState = (await (await replay).json()) as Training;
  expect(replayState.incidents).toHaveLength(1);
  expect(replayState.replay).toBe(true);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: info.outputPath("training-debrief.png"),
    fullPage: true,
  });
  const forkResponse = page.waitForResponse(
    (r) =>
      r.url().endsWith(`/training/runs/${sourceId}/fork`) &&
      r.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "Попробовать иначе с этого момента" })
    .click();
  const fork = await forkResponse;
  expect(fork.ok(), await fork.text()).toBe(true);
  state = (await fork.json()) as Training;
  expect(state.source_id).toBe(sourceId);
  expect(state.reward_eligible).toBe(false);
  expect(state.id).not.toBe(sourceId);
  await time(240);
  await expect(
    page.getByRole("heading", { name: "Что изменил другой подход" }),
  ).toBeVisible();
  const source = (await (
    await request.get(`/api/v1/training/runs/${sourceId}`, { headers })
  ).json()) as Training;
  expect(source.status).toBe("completed");
  expect(source.xp).toBe(0);
  expect(source.source_id).toBeNull();
});

test("staff: server roles, content publication and real group assignment", async ({
  page,
  request,
}, info) => {
  test.skip(
    !process.env.PLAYWRIGHT_SIMULATION_CONTAINER,
    "Explicit local synthetic deployment required",
  );
  test.setTimeout(180_000);
  const login = await request.post("/api/v1/auth/demo", {
    data: { persona_id: "demo-employee" },
  });
  const learnerHeaders = {
    Authorization: `Bearer ${(await login.json()).access_token}`,
  };
  const previous = (
    await (
      await request.get("/api/v1/training/current", { headers: learnerHeaders })
    ).json()
  ).simulation as Training | null;
  if (previous?.status === "active")
    advanceClock(previous.id, previous.duration_seconds);
  const forbidden = await request.post("/api/v1/training/assignments", {
    headers: { ...learnerHeaders, "Idempotency-Key": crypto.randomUUID() },
    data: { title: "Forbidden learner assignment", mode: "demo" },
  });
  expect(forbidden.status()).toBe(403);
  await page.goto("/");
  await page.getByRole("button", { name: "Мой прогресс", exact: true }).click();
  await page
    .getByRole("combobox", { name: "Учебный проводник" })
    .selectOption("demo-methodist");
  await expect(
    page.getByRole("combobox", { name: "Учебный проводник" }),
  ).toHaveValue("demo-methodist");
  await page.getByRole("button", { name: "Смена", exact: true }).click();
  await page.getByRole("button", { name: "Содержание", exact: true }).click();
  await page.getByRole("button", { name: "Новый черновик" }).click();
  await expect(
    page.getByRole("textbox", { name: "Название кейса" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Проверить и показать расписание" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Содержание прошло проверку" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Опубликовать версию" }).click();
  await expect(page.getByText(/Версия опубликована/)).toBeVisible();
  await page.screenshot({
    path: info.outputPath("training-editor.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Мой прогресс", exact: true }).click();
  await page
    .getByRole("combobox", { name: "Учебный проводник" })
    .selectOption("demo-instructor");
  await expect(
    page.getByRole("combobox", { name: "Учебный проводник" }),
  ).toHaveValue("demo-instructor");
  await page.getByRole("button", { name: "Смена", exact: true }).click();
  await page
    .getByRole("button", { name: "Учебная группа", exact: true })
    .click();
  await page.getByText("Новое назначение группе").click();
  const assignmentTitle = `Общая учебная ситуация ${Date.now()}`;
  await page
    .getByRole("textbox", { name: "Название назначения" })
    .fill(assignmentTitle);
  await page
    .getByRole("combobox", { name: "Формат", exact: true })
    .selectOption("demo");
  const assignmentResponse = page.waitForResponse(
    (r) =>
      r.url().endsWith("/training/assignments") &&
      r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Назначить группе" }).click();
  const assignment = await assignmentResponse;
  expect(assignment.ok(), await assignment.text()).toBe(true);
  await expect(page.getByRole("table")).toBeVisible();
  await expect(page.getByRole("table")).toContainText("Учебный проводник 01");
  await page.screenshot({
    path: info.outputPath("training-instructor.png"),
    fullPage: true,
  });
  const assigned = (await assignment.json()) as { id: string };
  async function switchTo(id: string) {
    await page
      .getByRole("button", { name: "Мой прогресс", exact: true })
      .click();
    await page
      .getByRole("combobox", { name: "Учебный проводник" })
      .selectOption(id);
    await expect(
      page.getByRole("combobox", { name: "Учебный проводник" }),
    ).toHaveValue(id);
    await page.getByRole("button", { name: "Смена", exact: true }).click();
  }
  await switchTo("demo-employee");
  const started = page.waitForResponse(
    (r) =>
      r.url().endsWith("/training/runs") && r.request().method() === "POST",
  );
  await page
    .locator(".training-assignments > div")
    .filter({ hasText: assignmentTitle })
    .getByRole("button", { name: "Открыть назначение" })
    .click();
  const startResponse = await started;
  expect(startResponse.ok(), await startResponse.text()).toBe(true);
  const run = (await startResponse.json()) as Training;
  expect(run.assignment_id).toBe(assigned.id);
  const actionResponse = page.waitForResponse(
    (r) =>
      r.url().endsWith(`/training/runs/${run.id}/actions`) &&
      r.request().method() === "POST",
  );
  await page.getByRole("button", { name: /Взять.*Радиостанция/ }).click();
  expect((await actionResponse).ok()).toBe(true);
  advanceClock(run.id, 240);
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Основания оценки" }),
  ).toBeVisible();
  await switchTo("demo-instructor");
  await page
    .getByRole("button", { name: "Учебная группа", exact: true })
    .click();
  await page
    .getByRole("combobox", { name: "Назначение группы" })
    .selectOption(assigned.id);
  await page
    .getByRole("row")
    .filter({ hasText: "Учебный проводник 01" })
    .getByRole("button", { name: "Разобрать" })
    .click();
  await page
    .getByRole("textbox", { name: "Комментарий инструктора" })
    .fill(
      "Оборудование подготовлено. В следующем рейсе уточните потребность пассажира до эскалации.",
    );
  await page.getByRole("button", { name: "Сохранить комментарий" }).click();
  await expect(
    page.locator("blockquote").filter({ hasText: "Оборудование подготовлено" }),
  ).toBeVisible();
  await switchTo("demo-employee");
  await expect(
    page
      .locator(".training-comments blockquote")
      .filter({ hasText: "Оборудование подготовлено" }),
  ).toBeVisible();
  const personal = await request.post("/api/v1/training/runs", {
    headers: { ...learnerHeaders, "Idempotency-Key": crypto.randomUUID() },
    data: { mode: "tutorial" },
  });
  expect(personal.ok(), await personal.text()).toBe(true);
  const laterRun = (await personal.json()) as Training;
  advanceClock(laterRun.id, 180);
  await page.reload();
  let repeatStarts = 0;
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().endsWith("/training/runs"))
      repeatStarts += 1;
  });
  await page
    .locator(".training-assignments > div")
    .filter({ hasText: assignmentTitle })
    .getByRole("button", { name: "Открыть назначение" })
    .click();
  await expect(
    page
      .locator(".training-comments blockquote")
      .filter({ hasText: "Оборудование подготовлено" }),
  ).toBeVisible();
  expect(repeatStarts).toBe(0);
});
