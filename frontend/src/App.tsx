import { useEffect, useState } from "react";

type Connection = "loading" | "success" | "error";

export default function App() {
  const [connection, setConnection] = useState<Connection>("loading");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    const timeout = window.setTimeout(() => controller.abort(), 5000);

    async function checkConnection() {
      try {
        const response = await fetch("/api/health", {
          signal: controller.signal,
          cache: "no-store",
        });
        if (!response.ok) throw new Error("Backend unavailable");
        const body: unknown = await response.json();
        if (
          !body ||
          typeof body !== "object" ||
          !("status" in body) ||
          body.status !== "ok"
        ) {
          throw new Error("Unexpected health response");
        }
        if (active) setConnection("success");
      } catch {
        if (active) setConnection("error");
      } finally {
        window.clearTimeout(timeout);
      }
    }

    void checkConnection();
    return () => {
      active = false;
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [attempt]);

  return (
    <main>
      <header>
        <span className="brand">ВСМ 400</span>
        <span>Геймификация для ВСМ</span>
      </header>
      <p className="eyebrow">Этап 01 · Технический фундамент</p>
      <h1>
        Готовим платформу
        <br />
        для новых скоростей
      </h1>
      <p className="intro">
        Проверяем связь между приложением и сервером. Назначение продукта и
        целевая аудитория уточняются.
      </p>
      <section aria-labelledby="connection-title">
        <h2 id="connection-title">Подключение к платформе</h2>
        <div
          className={`connection ${connection}`}
          role={connection === "error" ? "alert" : "status"}
        >
          <span className="dot" aria-hidden="true" />
          {connection === "loading" && "Проверяем соединение…"}
          {connection === "success" && "Backend подключён"}
          {connection === "error" && "Нет соединения с backend"}
        </div>
        <p className="note">
          {connection === "error"
            ? "Проверьте, запущен ли сервер, и повторите попытку."
            : "Проверка подтверждает доступность API. Готовность базы данных проверяется отдельно."}
        </p>
        <button
          disabled={connection === "loading"}
          onClick={() => {
            setConnection("loading");
            setAttempt((value) => value + 1);
          }}
        >
          Проверить снова
        </button>
      </section>
      <footer>
        PRODUCT SCOPE — pending clarification. Продуктовый scope не
        зафиксирован.
      </footer>
    </main>
  );
}
