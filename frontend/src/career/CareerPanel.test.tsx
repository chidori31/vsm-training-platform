import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CareerPanel, CompletionReward } from "./CareerPanel";

const profile = {
  id: "demo-employee",
  display_name: "Учебный проводник 01",
  organization: {
    company_id: "demo",
    company_name: "Учебная компания",
    depot_id: "north",
    depot_name: "Депо Север",
    brigade_id: "01",
    brigade_name: "Бригада 01",
  },
  xp: 165,
  level: 2,
  level_start_xp: 100,
  next_level_xp: 300,
  completed_sessions: 3,
  rule_version: 1,
  competencies: [{ competency_id: "communication", value: 3 }],
  achievements: [
    {
      id: "communication-growth",
      name: "Мастер диалога",
      description: "Накопите 3 очка коммуникации",
      current: 3,
      target: 3,
      unlocked: true,
      unlocked_at: "2026-09-26T12:00:00Z",
      session_id: "s3",
    },
    {
      id: "safe-shift",
      name: "Надёжная смена",
      description: "3 безопасных завершения",
      current: 2,
      target: 3,
      unlocked: false,
      unlocked_at: null,
      session_id: null,
    },
  ],
  reward: {
    session_id: "s3",
    xp: 55,
    competencies: [{ competency_id: "communication", value: 1 }],
    unlocks: ["communication-growth"],
  },
};
const personas = [
  {
    id: "demo-employee",
    display_name: "Учебный проводник 01",
    company_id: "demo",
    depot_id: "north",
    brigade_id: "01",
  },
];

describe("conductor progress", () => {
  it("shows server XP, next level and unlocked/progress states", async () => {
    const read = vi.fn(async (path: string) =>
      path.includes("personas") ? personas : profile,
    );
    render(
      <CareerPanel
        mode="profile"
        read={read}
        identityId="demo-employee"
        busy={false}
        switchPersona={async () => {}}
        onPlay={() => {}}
      />,
    );
    expect(await screen.findByText("165 XP")).toBeVisible();
    expect(screen.getByText("До уровня 3 — 135 XP")).toBeVisible();
    expect(
      screen.getByRole("progressbar", { name: "Надёжная смена" }),
    ).toHaveAttribute("aria-valuenow", "2");
    expect(screen.getByText("Открыто")).toBeVisible();
    expect(screen.getByText("Коммуникация")).toBeVisible();
  });
  it("renders an earned session reward without calculating it in the browser", async () => {
    render(
      <CompletionReward
        read={async () => profile}
        sessionId="s3"
        onProfile={() => {}}
      />,
    );
    expect(await screen.findByText("+55 XP")).toBeVisible();
    expect(screen.getByText(/Мастер диалога/)).toBeVisible();
  });
  it("requests group-specific real rankings and retries a failed load", async () => {
    let fail = true;
    const read = vi.fn(async (path: string) => {
      if (fail) {
        fail = false;
        throw new Error("Нет связи");
      }
      const scope = path.includes("scope=company") ? "company" : "brigade";
      return {
        scope,
        group_name: scope === "company" ? "Учебная компания" : "Бригада 01",
        assigned: true,
        items: [
          {
            rank: 1,
            employee_id: "e",
            display_name: "Проводник из результатов",
            xp: 120,
            level: 2,
            completed_sessions: 1,
            is_me: false,
          },
        ],
        total: 1,
        limit: 20,
        offset: 0,
      };
    });
    render(
      <CareerPanel
        mode="leaderboard"
        read={read}
        identityId="demo-employee"
        busy={false}
        switchPersona={async () => {}}
        onPlay={() => {}}
      />,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("Нет связи");
    fireEvent.click(screen.getByRole("button", { name: "Повторить загрузку" }));
    expect(await screen.findByText("Проводник из результатов")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Компания" }));
    await waitFor(() =>
      expect(read.mock.calls.at(-1)?.[0]).toContain("scope=company"),
    );
    expect(await screen.findByText("Учебная компания")).toBeVisible();
  });
  it("rejects malformed progress instead of showing fabricated points", async () => {
    render(
      <CompletionReward
        read={async () => ({ ...profile, xp: "invalid" })}
        sessionId="s3"
        onProfile={() => {}}
      />,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("некорректный");
    expect(screen.queryByText("+55 XP")).not.toBeInTheDocument();
  });
});
