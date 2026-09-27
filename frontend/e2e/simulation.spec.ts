import { execFileSync } from "node:child_process";
import { expect, test } from "@playwright/test";
import type {
  Action,
  Simulation,
  SimulationDebrief,
} from "../src/simulation/contracts";

// Explicitly opt into a synthetic local Docker database. This injects the trusted
// service clock; no client endpoint, stored score, or snapshot is edited.
function advanceClock(id: string, seconds: number) {
  const container = process.env.PLAYWRIGHT_SIMULATION_CONTAINER;
  if (!container)
    throw new Error("Set PLAYWRIGHT_SIMULATION_CONTAINER for this test");
  const script = `
import json, os, sys
from datetime import timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.application.simulations import SimulationService
from app.persistence.simulations import StoredSimulation
args=json.load(sys.stdin)
engine=create_engine(os.environ['DATABASE_URL'])
with Session(engine) as db:
    row=db.get(StoredSimulation,args['id'])
    assert row and row.employee_id == 'demo-employee'
    moment=row.started_at+timedelta(seconds=args['seconds'])
SimulationService(engine,clock=lambda db: moment).get(args['id'],'demo-employee')
engine.dispose()
`;
  execFileSync("docker", ["exec", "-i", container, "python", "-c", script], {
    input: JSON.stringify({ id, seconds }),
    timeout: 20_000,
    stdio: ["pipe", "pipe", "pipe"],
  });
}

test("operational shift: carriage, investigation, parallel events, equipment, delayed help and black box", async ({
  page,
  request,
}, info) => {
  test.skip(
    !process.env.PLAYWRIGHT_SIMULATION_CONTAINER,
    "Requires explicit local synthetic Docker clock fixture",
  );
  test.setTimeout(120_000);
  const login = await request.post("/api/v1/auth/demo", {
    data: { persona_id: "demo-employee" },
  });
  expect(login.ok()).toBe(true);
  const headers = {
    Authorization: `Bearer ${(await login.json()).access_token}`,
  };
  const prior = (
    await (await request.get("/api/v1/simulations/current", { headers })).json()
  ).simulation as Simulation | null;
  if (prior?.status === "active") advanceClock(prior.id, 1200);
  await page.goto("/#legacy-simulation");
  await expect(
    page.getByRole("button", { name: /Принять (рабочую|новую) смену/ }),
  ).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: info.outputPath("simulation-start.png"),
    fullPage: true,
  });
  const started = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/v1/simulations") &&
      r.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: /Принять (рабочую|новую) смену/ })
    .click();
  const response = await started;
  expect(response.ok()).toBe(true);
  let state = (await response.json()) as Simulation;
  const runId = state.id;
  const map = page.getByRole("group", { name: "Схема вагона" });
  await expect(map).toBeVisible();
  async function action(action: Action) {
    const response = page.waitForResponse(
      (r) =>
        r.url().endsWith(`/simulations/${runId}/actions`) &&
        r.request().method() === "POST",
    );
    await page.getByRole("button", { name: action.label, exact: true }).click();
    const result = await response;
    expect(result.ok(), await result.text()).toBe(true);
    state = (await result.json()) as Simulation;
  }
  async function global(id: string, zone?: string) {
    const a = state.actions.find(
      (a) => a.id === id && (!zone || a.zone_id === zone),
    );
    expect(a).toBeDefined();
    await action(a!);
  }
  async function incidentAction(id: string, verb: string) {
    const a = state.incidents
      .find((i) => i.id === id)
      ?.actions.find((a) => a.id === verb);
    expect(a).toBeDefined();
    await action(a!);
  }
  async function visit(id: string) {
    const incident = state.incidents.find((i) => i.id === id)!;
    const zone = state.zones.find((z) => z.id === incident.zone_id)!;
    await map.getByRole("button", { name: new RegExp(zone.title) }).click();
    if (state.location !== zone.id) await global("move", zone.id);
    const tab = page.getByRole("button", { name: incident.title, exact: true });
    if (await tab.isVisible()) await tab.click();
  }
  async function time(seconds: number) {
    advanceClock(runId, seconds);
    const response = await request.get(`/api/v1/simulations/${runId}`, {
      headers,
    });
    expect(response.ok()).toBe(true);
    state = (await response.json()) as Simulation;
    await page.reload();
    await expect(
      page.getByRole("region", { name: "Операционная смена" }),
    ).toBeVisible();
  }
  for (const id of ["radio", "flashlight", "first-aid", "documents"])
    await global(`take:${id}`);
  await visit("request");
  await incidentAction("request", "talk");
  await incidentAction("request", "inspect");
  await incidentAction("request", "verify");
  await incidentAction("request", "contact");
  await time(150);
  expect(
    state.incidents.filter((i) => i.status !== "resolved").length,
  ).toBeGreaterThanOrEqual(2);
  await visit("odor");
  await incidentAction("odor", "inspect");
  await incidentAction("odor", "restrict");
  await incidentAction("odor", "verify");
  await incidentAction("odor", "contact");
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: info.outputPath("simulation-parallel-desktop.png"),
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
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: info.outputPath("simulation-mobile.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await time(360);
  await visit("request");
  await incidentAction("request", "assist");
  await visit("odor");
  await incidentAction("odor", "assist");
  await time(500);
  await visit("wellbeing");
  await incidentAction("wellbeing", "talk");
  await incidentAction("wellbeing", "inspect");
  await incidentAction("wellbeing", "verify");
  await incidentAction("wellbeing", "contact");
  await time(700);
  await visit("arrival");
  await incidentAction("arrival", "inspect");
  await incidentAction("arrival", "verify");
  const contact = state.incidents
    .find((i) => i.id === "arrival")!
    .actions.find((a) => a.id === "contact");
  if (contact) await action(contact);
  await time(780);
  await visit("wellbeing");
  await incidentAction("wellbeing", "assist");
  await visit("arrival");
  await incidentAction("arrival", "assist");
  await time(1200);
  await expect(
    page.getByRole("heading", { name: "Как прошла ваша смена" }),
  ).toBeVisible();
  const report = await request.get(`/api/v1/simulations/${runId}/debrief`, {
    headers,
  });
  const debrief = (await report.json()) as SimulationDebrief;
  expect(debrief.simulation.status).toBe("completed");
  expect(debrief.incidents).toHaveLength(5);
  expect(
    debrief.simulation.communications.some((c) => c.status === "answered"),
  ).toBe(true);
  expect(
    debrief.simulation.journal.some((e) =>
      e.metric_changes.some((c) => c.delta < 0),
    ),
  ).toBe(true);
  await page.locator(".sim-counterfactual summary").first().click();
  await expect(
    page.getByText("Что изменил бы другой подход").first(),
  ).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: info.outputPath("simulation-debrief.png"),
    fullPage: true,
  });
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Как прошла ваша смена" }),
  ).toBeVisible();
  const again = await request.get(`/api/v1/simulations/${runId}`, { headers });
  expect((await again.json()).xp).toBe(debrief.simulation.xp);
});
