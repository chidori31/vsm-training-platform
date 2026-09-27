import { TrainingIdentity } from "./storage";
import { useState } from "react";
import type { ReadResource } from "../career/contracts";
import type { WriteResource } from "../shift/contracts";
import { SimulationPanel } from "../simulation/SimulationPanel";
import {
  modeNames,
  parseAccess,
  object,
  list,
  text,
  number,
  check,
  type TrainingMode,
} from "./contracts";
import { ResourceError, useResource } from "./TrainingTools";
import { InstructorPanel } from "./InstructorPanel";
import { ContentEditor } from "./ContentEditor";

interface HistoryPage {
  items: {
    id: string;
    title: string;
    mode: TrainingMode;
    status: string;
    started_at: string;
    source_id: string | null;
  }[];
  total: number;
  limit: number;
  offset: number;
}
function parseHistory(v: unknown): HistoryPage {
  const h = object(v);
  ["total", "limit", "offset"].forEach((k) => number(h[k]));
  list(h.items).forEach((raw) => {
    const i = object(raw);
    ["id", "title", "started_at", "status"].forEach((k) => text(i[k]));
    check(["work", "tutorial", "demo", "practice"].includes(String(i.mode)));
  });
  return v as HistoryPage;
}
function History({
  read,
  onOpen,
}: {
  read: ReadResource;
  onOpen: (id: string) => void;
}) {
  const [offset, setOffset] = useState(0);
  const data = useResource(
    read,
    `/training/history?limit=20&offset=${offset}`,
    parseHistory,
  );
  return (
    <section className="training-staff">
      <span className="micro-label">СОХРАНЁННЫЕ ПОЕЗДКИ</span>
      <h1>Журнал учебных смен</h1>
      <p>
        Откройте результат, восстановите момент решения или продолжите активную
        поездку.
      </p>
      <ResourceError message={data.error} retry={data.reload} />
      {data.value ? (
        <>
          <div className="training-table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Поездка</th>
                  <th>Режим</th>
                  <th>Начало</th>
                  <th>Результат</th>
                </tr>
              </thead>
              <tbody>
                {data.value.items.map((i) => (
                  <tr key={i.id}>
                    <td>
                      {i.title}
                      {i.source_id && <small> · Альтернативная попытка</small>}
                    </td>
                    <td>{modeNames[i.mode]}</td>
                    <td>{new Date(i.started_at).toLocaleString("ru-RU")}</td>
                    <td>
                      <button onClick={() => onOpen(i.id)}>
                        {i.status === "active"
                          ? "Продолжить"
                          : "Открыть разбор"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!data.value.items.length && (
            <p>
              Пока нет сохранённых поездок. Начните со знакомства с рабочим
              местом.
            </p>
          )}
          <div className="staff-toolbar">
            <button
              disabled={offset === 0}
              onClick={() => setOffset((n) => Math.max(0, n - 20))}
            >
              Предыдущие
            </button>
            <span>
              {Math.min(offset + 1, data.value.total)}–
              {Math.min(offset + 20, data.value.total)} из {data.value.total}
            </span>
            <button
              disabled={offset + 20 >= data.value.total}
              onClick={() => setOffset((n) => n + 20)}
            >
              Следующие
            </button>
          </div>
        </>
      ) : (
        !data.error && <p role="status">Загружаем журнал…</p>
      )}
    </section>
  );
}
export function TrainingHub({
  read,
  write,
  replace,
  identityId,
}: {
  read: ReadResource;
  write: WriteResource;
  replace: WriteResource;
  identityId: string;
}) {
  const access = useResource(read, "/training/access", parseAccess);
  const [selectedTab, setTab] = useState<
    "run" | "history" | "group" | "content" | "legacy" | null
  >(null);
  const [runId, setRunId] = useState<string>();
  const role = access.value?.role;
  const tab =
    selectedTab ??
    (role === "instructor"
      ? "group"
      : role === "methodist"
        ? "content"
        : "run");
  return (
    <TrainingIdentity value={identityId}>
      <div className="training-hub">
        <nav className="training-hub-tabs" aria-label="Рабочее место обучения">
          {role === "employee" && (
            <>
              <button
                aria-current={tab === "run" ? "page" : undefined}
                onClick={() => {
                  setRunId(undefined);
                  setTab("run");
                }}
              >
                Поездка
              </button>
              <button
                aria-current={tab === "history" ? "page" : undefined}
                onClick={() => setTab("history")}
              >
                Журнал смен
              </button>
            </>
          )}
          {role === "instructor" && (
            <button
              aria-current={tab === "group" ? "page" : undefined}
              onClick={() => setTab("group")}
            >
              Учебная группа
            </button>
          )}
          {role === "methodist" && (
            <button
              aria-current={tab === "content" ? "page" : undefined}
              onClick={() => setTab("content")}
            >
              Содержание
            </button>
          )}
          {role === "employee" && (
            <button
              aria-current={tab === "legacy" ? "page" : undefined}
              onClick={() => setTab("legacy")}
            >
              Ранние смены
            </button>
          )}
        </nav>
        <ResourceError message={access.error} retry={access.reload} />
        {!role ? (
          !access.error && <p role="status">Подключаем рабочее место…</p>
        ) : tab === "run" ? (
          <SimulationPanel
            key={`${identityId}:${runId ?? "current"}`}
            training
            read={read}
            write={write}
            identityId={identityId}
            runId={runId}
          />
        ) : tab === "legacy" ? (
          <SimulationPanel read={read} write={write} identityId={identityId} />
        ) : tab === "history" ? (
          <History
            read={read}
            onOpen={(id) => {
              setRunId(id);
              setTab("run");
            }}
          />
        ) : tab === "group" && role === "instructor" ? (
          <InstructorPanel read={read} write={write} />
        ) : tab === "content" && role === "methodist" ? (
          <ContentEditor read={read} write={write} replace={replace} />
        ) : (
          <p role="alert">
            Для этого раздела требуется назначенная сервером роль.
          </p>
        )}
      </div>
    </TrainingIdentity>
  );
}
