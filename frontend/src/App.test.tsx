import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import App from "./App";

describe("Проверка подключения", () => {
  it("показывает ожидание, затем подтверждает ответ backend", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(Response.json({ status: "ok" })),
    );
    render(<App />);
    expect(screen.getByRole("status")).toHaveTextContent("Проверяем");
    expect(await screen.findByText("Backend подключён")).toBeInTheDocument();
  });

  it.each([
    ["HTTP 503", () => Promise.resolve(new Response("", { status: 503 }))],
    [
      "неверный контракт",
      () => Promise.resolve(Response.json({ status: "down" })),
    ],
    ["не JSON", () => Promise.resolve(new Response("<html>error</html>"))],
    ["сетевая ошибка", () => Promise.reject(new TypeError("Failed to fetch"))],
  ])("сообщает об ошибке: %s", async (_name, response) => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(response));
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Нет соединения",
    );
  });

  it("позволяет восстановить соединение повторной проверкой", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockRejectedValueOnce(new TypeError("offline"))
        .mockResolvedValueOnce(Response.json({ status: "ok" })),
    );
    render(<App />);
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "Проверить снова" }));
    expect(await screen.findByText("Backend подключён")).toBeInTheDocument();
  });

  it("завершает зависшую проверку через пять секунд", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_url: string, options: RequestInit) =>
          new Promise((_resolve, reject) => {
            options.signal?.addEventListener("abort", () =>
              reject(new DOMException("Aborted", "AbortError")),
            );
          }),
      ),
    );
    render(<App />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(screen.getByRole("alert")).toHaveTextContent("Нет соединения");
  });
});
