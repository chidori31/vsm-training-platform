import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { RetentionPanel } from "./RetentionPanel";
import { parseChallenges, parseNotifications } from "./contracts";

const time = "2026-09-26T10:00:00Z";
const end = "2026-09-27T10:00:00Z";
const challenge = {
  id: "day",
  title: "Две ситуации за сутки",
  description: "Без таймаутов",
  starts_at: time,
  expires_at: end,
  target: 2,
  progress: 1,
  status: "active",
  scenarios: [
    { id: "one", version: 1, title: "Помощь при посадке", completed: true },
    { id: "two", version: 1, title: "Забытая вещь", completed: false },
  ],
};
const notification = {
  id: "notice",
  kind: "new_scenario",
  title: "Новый сценарий",
  body: "Забытая вещь",
  created_at: time,
  expires_at: end,
  read_at: null as string | null,
  expired: false,
};
const page = { total: 1, limit: 20, offset: 0, server_time: time };

function fixture() {
  const notice = { ...notification };
  const read = vi.fn(async (path: string) =>
    path.startsWith("/challenges")
      ? { ...page, items: [challenge] }
      : {
          ...page,
          unread_count: notice.read_at ? 0 : 1,
          items: [{ ...notice }],
        },
  );
  const write = vi.fn(async (_id: string, value: boolean) => {
    notice.read_at = value ? time : null;
    return { ...notice };
  });
  return { read, write };
}

describe("retention", () => {
  it("shows server progress and opens the exact scenario version", async () => {
    const data = fixture();
    const onScenario = vi.fn();
    render(
      <RetentionPanel {...data} identityId="one" onScenario={onScenario} />,
    );
    expect(
      await screen.findByRole("heading", { name: challenge.title }),
    ).toBeVisible();
    expect(screen.getByRole("progressbar")).toHaveAttribute(
      "aria-valuenow",
      "1",
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Открыть: Забытая вещь" }),
    );
    expect(onScenario).toHaveBeenCalledWith({
      id: "two",
      version: 1,
      title: "Забытая вещь",
      completed: false,
    });
    expect(screen.getByText("Непрочитанных: 1")).toBeVisible();
  });
  it("persists read and unread, retaining server expiry", async () => {
    const data = fixture();
    render(<RetentionPanel {...data} identityId="one" onScenario={vi.fn()} />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Отметить прочитанным" }),
    );
    expect(
      await screen.findByRole("button", { name: "Отметить непрочитанным" }),
    ).toBeVisible();
    expect(data.write).toHaveBeenCalledWith(
      "notice",
      true,
      expect.any(AbortSignal),
    );
    expect(screen.getByText("Непрочитанных: 0")).toBeVisible();
    fireEvent.click(
      screen.getByRole("button", { name: "Отметить непрочитанным" }),
    );
    expect(
      await screen.findByRole("button", { name: "Отметить прочитанным" }),
    ).toBeVisible();
  });
  it("keeps failed mutations retryable without falsely marking read", async () => {
    const data = fixture();
    data.write.mockRejectedValueOnce(new Error("Нет связи"));
    render(<RetentionPanel {...data} identityId="one" onScenario={vi.fn()} />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Отметить прочитанным" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("Нет связи");
    expect(
      screen.getByRole("button", { name: "Отметить прочитанным" }),
    ).toBeEnabled();
  });
  it("recovers a failed page and renders completed/expired states", async () => {
    const data = fixture();
    data.read.mockRejectedValueOnce(new Error("Нет связи"));
    render(<RetentionPanel {...data} identityId="one" onScenario={vi.fn()} />);
    const error = await screen.findByRole("alert");
    fireEvent.click(within(error).getByRole("button", { name: "Повторить" }));
    expect(await screen.findByText(challenge.title)).toBeVisible();
    await waitFor(() => expect(data.read.mock.calls.length).toBeGreaterThan(2));
  });
  it("rejects malformed deadlines, impossible progress and unknown notification types", () => {
    expect(() =>
      parseChallenges({ ...page, items: [{ ...challenge, progress: 3 }] }),
    ).toThrow();
    expect(() =>
      parseChallenges({
        ...page,
        items: [{ ...challenge, expires_at: "broken" }],
      }),
    ).toThrow();
    expect(() =>
      parseNotifications({
        ...page,
        unread_count: 1,
        items: [{ ...notification, kind: "email" }],
      }),
    ).toThrow();
  });
});

it("preserves keyboard focus and loaded content during background refresh", async () => {
  const data = fixture();
  render(<RetentionPanel {...data} identityId="one" onScenario={vi.fn()} />);
  const button = await screen.findByRole("button", {
    name: "Отметить прочитанным",
  });
  button.focus();
  data.read.mockImplementationOnce(() => new Promise(() => {}));
  data.read.mockImplementationOnce(() => new Promise(() => {}));
  await act(async () => {
    window.dispatchEvent(new Event("focus"));
  });
  expect(button).toHaveFocus();
  expect(screen.getByRole("heading", { name: challenge.title })).toBeVisible();
  expect(screen.queryByText("Загружаем события…")).not.toBeInTheDocument();
});

it("shows acknowledged read state even when the following reload fails", async () => {
  const data = fixture();
  render(<RetentionPanel {...data} identityId="one" onScenario={vi.fn()} />);
  const button = await screen.findByRole("button", {
    name: "Отметить прочитанным",
  });
  data.read.mockRejectedValueOnce(new Error("Нет связи после сохранения"));
  fireEvent.click(button);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Нет связи после сохранения",
  );
  expect(
    screen.getByRole("button", { name: "Отметить непрочитанным" }),
  ).toBeEnabled();
  expect(screen.getByText("Непрочитанных: 0")).toBeVisible();
});
