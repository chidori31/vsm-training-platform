import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import content from "../../../scenarios/training/demo-v2.json";
import { ContentEditor } from "./ContentEditor";
import { InstructorPanel } from "./InstructorPanel";
import { TrainingHub } from "./TrainingHub";

beforeEach(() => sessionStorage.clear());

describe("training staff workspace", () => {
  it("hides instructor and content tools for a learner", async () => {
    const read = vi.fn(async (path: string) =>
      path === "/training/access"
        ? { role: "employee", group_id: "demo" }
        : path === "/training/current"
          ? { simulation: null }
          : path === "/training/assignments"
            ? { items: [] }
            : {
                competencies: [],
                patterns: [],
                recommendations: [],
                statistics: { completed_runs: 0, completed_scenarios: 0 },
                source_notice: "Учебная модель",
              },
    );
    render(
      <TrainingHub
        read={read}
        write={vi.fn()}
        replace={vi.fn()}
        identityId="demo"
      />,
    );
    await screen.findByRole("button", { name: /Принять рабочую смену/ });
    expect(
      screen.queryByRole("button", { name: "Учебная группа" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Содержание" }),
    ).not.toBeInTheDocument();
  });
  it("keeps the same assignment key after an uncertain acknowledgement", async () => {
    const read = vi.fn(async () => ({ items: [] }));
    const write = vi
      .fn()
      .mockRejectedValueOnce(new Error("Связь прервана"))
      .mockResolvedValue({ id: "a1" });
    const mounted = render(<InstructorPanel read={read} write={write} />);
    fireEvent.click(screen.getByText("Новое назначение группе"));
    fireEvent.click(screen.getByRole("button", { name: "Назначить группе" }));
    await screen.findByText("Связь прервана");
    mounted.unmount();
    render(<InstructorPanel read={read} write={write} />);
    fireEvent.click(screen.getByText("Новое назначение группе"));
    fireEvent.click(screen.getByRole("button", { name: "Назначить группе" }));
    await waitFor(() => expect(write).toHaveBeenCalledTimes(2));
    expect(write.mock.calls[0].slice(0, 3)).toEqual(
      write.mock.calls[1].slice(0, 3),
    );
    expect(write.mock.calls[0][1]).toEqual({
      title: "Практика учебной бригады",
      mode: "work",
    });
  });
  it("requires validation after edits and publishes the saved revision", async () => {
    const record = {
      id: "content-1",
      version: 2,
      title: content.title,
      status: "draft",
      revision: 1,
      document: content,
    };
    const read = vi.fn(async (path: string) =>
      path === "/training/content"
        ? { items: [record] }
        : structuredClone(record),
    );
    const replace = vi.fn(async (_path: string, body: unknown) => ({
      ...record,
      revision: 2,
      document: (body as { document: unknown }).document,
    }));
    const write = vi.fn(async (path: string) =>
      path.endsWith("/validate")
        ? {
            valid: true,
            errors: [],
            warnings: ["Одновременные обращения"],
            timeline: [
              { at_seconds: 0, title: "Просьба о помощи", kind: "report" },
            ],
          }
        : { ...record, status: "published", revision: 3 },
    );
    render(<ContentEditor read={read} write={write} replace={replace} />);
    fireEvent.click(await screen.findByRole("button", { name: "Открыть" }));
    const title = await screen.findByRole("textbox", {
      name: "Название кейса",
    });
    fireEvent.change(title, { target: { value: "Изменённый кейс" } });
    expect(
      screen.getByRole("button", { name: "Опубликовать версию" }),
    ).toBeDisabled();
    fireEvent.click(
      screen.getByRole("button", { name: "Проверить и показать расписание" }),
    );
    await screen.findByText("Содержание прошло проверку");
    expect(replace.mock.calls[0][1]).toEqual(
      expect.objectContaining({
        expected_revision: 1,
        document: expect.objectContaining({ title: "Изменённый кейс" }),
      }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Опубликовать версию" }),
    );
    await waitFor(() =>
      expect(write).toHaveBeenLastCalledWith(
        "/training/content/content-1/publish",
        { expected_revision: 2 },
        expect.any(String),
        expect.any(AbortSignal),
      ),
    );
    expect(await screen.findByText(/Версия опубликована/)).toBeVisible();
  });
  it("keeps publish disabled when content cannot be completed", async () => {
    const record = {
      id: "content-1",
      version: 2,
      title: content.title,
      status: "draft",
      revision: 1,
      document: content,
    };
    const read = vi.fn(async (path: string) =>
      path === "/training/content"
        ? { items: [record] }
        : structuredClone(record),
    );
    render(
      <ContentEditor
        read={read}
        replace={vi.fn()}
        write={vi.fn(async () => ({
          valid: false,
          errors: ["Передача не помещается в окно станции"],
          warnings: [],
          timeline: [],
        }))}
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Открыть" }));
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Проверить и показать расписание",
      }),
    );
    await screen.findByText(/Передача не помещается/);
    expect(
      screen.getByRole("button", { name: "Опубликовать версию" }),
    ).toBeDisabled();
  });
});

it("restores unsaved content across back navigation and component remount", async () => {
  const record = {
    id: "draft-preserved",
    version: 2,
    title: content.title,
    status: "draft",
    revision: 1,
    document: content,
  };
  const read = vi.fn(async (path: string) =>
    path === "/training/content"
      ? { items: [record] }
      : structuredClone(record),
  );
  const props = { read, write: vi.fn(), replace: vi.fn() };
  const mounted = render(<ContentEditor {...props} />);
  fireEvent.click(await screen.findByRole("button", { name: "Открыть" }));
  fireEvent.change(
    await screen.findByRole("textbox", { name: "Название кейса" }),
    { target: { value: "Сохранить мою методическую правку" } },
  );
  fireEvent.click(
    screen.getByRole("button", { name: "← К версиям содержания" }),
  );
  fireEvent.click(await screen.findByRole("button", { name: "Открыть" }));
  expect(
    await screen.findByRole("textbox", { name: "Название кейса" }),
  ).toHaveValue("Сохранить мою методическую правку");
  mounted.unmount();
  render(<ContentEditor {...props} />);
  fireEvent.click(await screen.findByRole("button", { name: "Открыть" }));
  expect(
    await screen.findByRole("textbox", { name: "Название кейса" }),
  ).toHaveValue("Сохранить мою методическую правку");
  expect(
    screen.getByRole("button", { name: "Опубликовать версию" }),
  ).toBeDisabled();
});

it("allows reviewed recovery of a local draft after a concurrent server revision", async () => {
  let record = {
    id: "draft-conflict",
    version: 2,
    title: content.title,
    status: "draft",
    revision: 1,
    document: structuredClone(content),
  };
  const read = vi.fn(async (path: string) =>
    path === "/training/content"
      ? { items: [record] }
      : structuredClone(record),
  );
  const replace = vi.fn(async (_path: string, body: unknown) => ({
    ...record,
    revision: 3,
    document: (body as { document: unknown }).document,
  }));
  const props = { read, write: vi.fn(), replace };
  const mounted = render(<ContentEditor {...props} />);
  fireEvent.click(await screen.findByRole("button", { name: "Открыть" }));
  fireEvent.change(
    await screen.findByRole("textbox", { name: "Название кейса" }),
    { target: { value: "Моя сохранённая локальная правка" } },
  );
  mounted.unmount();
  record = {
    ...record,
    revision: 2,
    document: { ...record.document, title: "Изменение другого методиста" },
  };
  render(<ContentEditor {...props} />);
  fireEvent.click(await screen.findByRole("button", { name: "Открыть" }));
  await screen.findByRole("heading", {
    name: "Серверный черновик уже изменён",
  });
  fireEvent.click(
    screen.getByRole("button", {
      name: "Перенести мои правки на актуальную ревизию",
    }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Сохранить черновик" }));
  await waitFor(() =>
    expect(replace).toHaveBeenCalledWith(
      "/training/content/draft-conflict",
      expect.objectContaining({
        expected_revision: 2,
        document: expect.objectContaining({
          title: "Моя сохранённая локальная правка",
        }),
      }),
      expect.any(String),
      expect.any(AbortSignal),
    ),
  );
});
