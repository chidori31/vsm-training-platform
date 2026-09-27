import { useContext, useEffect, useRef, useState } from "react";
import type { ReadResource } from "../career/contracts";
import type { WriteResource } from "../shift/contracts";
import {
  TrainingIdentity,
  readStored,
  store,
  useSessionDraft,
} from "./storage";
import { check, object, text as validateText } from "./contracts";
import { friendlyError } from "../runner/api";
import { clock, metricNames } from "../simulation/contracts";
import {
  parseAssignment,
  parseAssignments,
  parseComments,
  parseTrainingDebrief,
  modeNames,
} from "./contracts";
import { Assessment, ResourceError, useResource } from "./TrainingTools";

export function useMutation(write: WriteResource, method = "POST") {
  const identity = useContext(TrainingIdentity);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState<string | null>(null);
  const operation = useRef<AbortController | null>(null);
  const pending = useRef<{ signature: string; key: string } | null>(null);
  useEffect(() => () => operation.current?.abort(), []);
  async function send(path: string, body: unknown): Promise<unknown> {
    if (operation.current)
      throw new Error("Дождитесь подтверждения предыдущей операции.");
    const signature = JSON.stringify([path, body]);
    const storageKey = `vsm.training.mutation.${identity}.${method}.${path}`;
    const saved = readStored(storageKey, (v) => {
      const s = object(v);
      validateText(s.signature);
      validateText(s.key);
      return { signature: s.signature, key: s.key };
    });
    if (pending.current?.signature !== signature)
      pending.current =
        saved?.signature === signature
          ? saved
          : { signature, key: crypto.randomUUID() };
    store(storageKey, pending.current);
    const abort = new AbortController();
    operation.current = abort;
    setBusy(true);
    setError(null);
    try {
      const value = await write(path, body, pending.current.key, abort.signal);
      if (abort.signal.aborted) throw new DOMException("Aborted", "AbortError");
      pending.current = null;
      store(storageKey, null);
      return value;
    } catch (cause) {
      if (!abort.signal.aborted) setError(friendlyError(cause));
      throw cause;
    } finally {
      operation.current = null;
      if (!abort.signal.aborted) setBusy(false);
    }
  }
  return { send, busy, error };
}
function RunReview({
  read,
  write,
  runId,
  onBack,
}: {
  read: ReadResource;
  write: WriteResource;
  runId: string;
  onBack: () => void;
}) {
  const report = useResource(
    read,
    `/training/instructor/runs/${encodeURIComponent(runId)}`,
    parseTrainingDebrief,
  );
  const comments = useResource(
    read,
    `/training/instructor/runs/${encodeURIComponent(runId)}/comments`,
    parseComments,
  );
  const [draft, setDraft, clearDraft] = useSessionDraft(
    `comment.${runId}`,
    { event: "", text: "" },
    (v) => {
      const d = object(v);
      validateText(d.event);
      validateText(d.text);
      return { event: d.event, text: d.text };
    },
  );
  const selected = draft.event,
    text = draft.text;
  const setSelected = (event: string) => setDraft({ ...draft, event });
  const setText = (text: string) => setDraft({ ...draft, text });
  const command = useMutation(write);
  const event =
    report.value?.simulation.journal.find((e) => e.id === selected) ??
    report.value?.simulation.journal[0];
  return (
    <section className="training-staff">
      <button onClick={onBack}>← К учебной группе</button>
      <h1>Разбор участника</h1>
      <ResourceError message={report.error} retry={report.reload} />
      {report.value ? (
        <>
          <p>{report.value.summary}</p>
          <Assessment
            assessment={report.value.assessment}
            journal={report.value.simulation.journal}
          />
          <section className="training-comments">
            <h2>Комментарий к действию</h2>
            <form
              className="training-staff-form"
              onSubmit={(e) => {
                e.preventDefault();
                if (!event || !text.trim()) return;
                void command
                  .send(
                    `/training/instructor/runs/${encodeURIComponent(runId)}/comments`,
                    { event_id: event.id, text: text.trim() },
                  )
                  .then(() => {
                    setText("");
                    clearDraft();
                    comments.reload();
                  })
                  .catch(() => {});
              }}
            >
              <label>
                Запись журнала
                <select
                  value={event?.id ?? ""}
                  onChange={(e) => setSelected(e.target.value)}
                  disabled={command.busy}
                >
                  {report.value.simulation.journal.map((e) => (
                    <option value={e.id} key={e.id}>
                      T+{clock(e.at_seconds)} · {e.title}
                    </option>
                  ))}
                </select>
              </label>
              {event && <p>{event.explanation}</p>}
              <label>
                Комментарий инструктора
                <textarea
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  maxLength={1000}
                  required
                  disabled={command.busy}
                  placeholder="Укажите наблюдаемое действие и следующий шаг для отработки."
                />
              </label>
              <span className="quiet-note">
                Комментарий увидит участник. {text.length}/1000
              </span>
              {command.error && <p role="alert">{command.error}</p>}
              <button disabled={command.busy || !event || !text.trim()}>
                {command.busy ? "Сохраняем…" : "Сохранить комментарий"}
              </button>
            </form>
            <ResourceError message={comments.error} retry={comments.reload} />
            {comments.value?.map((c) => (
              <blockquote key={c.id}>
                <p>{c.text}</p>
                <footer>
                  {c.author_name} ·{" "}
                  {report.value?.simulation.journal.find(
                    (e) => e.id === c.event_id,
                  )?.title ?? c.event_id}{" "}
                  · {new Date(c.created_at).toLocaleString("ru-RU")}
                </footer>
              </blockquote>
            ))}
          </section>
        </>
      ) : (
        !report.error && (
          <p role="status">Восстанавливаем результат участника…</p>
        )
      )}
    </section>
  );
}
function AssignmentResults({
  read,
  id,
  onReview,
}: {
  read: ReadResource;
  id: string;
  onReview: (id: string) => void;
}) {
  const result = useResource(
    read,
    `/training/assignments/${encodeURIComponent(id)}`,
    parseAssignment,
  );
  return (
    <section>
      <ResourceError message={result.error} retry={result.reload} />
      {result.value ? (
        <>
          <div className="staff-toolbar">
            <h2>{result.value.title}</h2>
            <button onClick={result.reload}>Обновить результаты</button>
          </div>
          <p className="quiet-note">
            Одинаковые версия кейса, режим и условия. Сравнивайте участников
            этого назначения.
          </p>
          <div className="training-table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Участник</th>
                  <th>Состояние</th>
                  {Object.entries(metricNames).map(([id, title]) => (
                    <th key={id}>{title}</th>
                  ))}
                  <th>Разбор</th>
                </tr>
              </thead>
              <tbody>
                {result.value.members.map((m) => (
                  <tr key={m.employee_id}>
                    <td>{m.display_name}</td>
                    <td>
                      {m.status === "completed"
                        ? "Завершено"
                        : m.status === "active"
                          ? "В процессе"
                          : "Не начато"}
                    </td>
                    {Object.keys(metricNames).map((id) => (
                      <td key={id}>
                        {m.metrics?.[id as keyof typeof m.metrics] ?? "—"}
                      </td>
                    ))}
                    <td>
                      {m.run_id && m.status === "completed" ? (
                        <button onClick={() => onReview(m.run_id!)}>
                          Разобрать
                        </button>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <h3>Где группе было трудно</h3>
          {result.value.difficulties.length ? (
            <ul>
              {result.value.difficulties.map((d, n) => (
                <li key={n}>
                  {d.title} · {d.count}
                </li>
              ))}
            </ul>
          ) : (
            <p className="quiet-note">
              Пока нет завершённых результатов с отмеченными затруднениями.
            </p>
          )}
        </>
      ) : (
        !result.error && <p role="status">Загружаем назначение…</p>
      )}
    </section>
  );
}
export function InstructorPanel({
  read,
  write,
}: {
  read: ReadResource;
  write: WriteResource;
}) {
  const assignments = useResource(
    read,
    "/training/assignments",
    parseAssignments,
  );
  const [selected, setSelected] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [draft, setDraft] = useSessionDraft(
    "assignment",
    { title: "Практика учебной бригады", mode: "work" as "work" | "demo" },
    (v) => {
      const d = object(v);
      validateText(d.title);
      check(d.mode === "work" || d.mode === "demo");
      return { title: d.title, mode: d.mode as "work" | "demo" };
    },
  );
  const title = draft.title,
    mode = draft.mode;
  const setTitle = (title: string) => setDraft({ ...draft, title });
  const setMode = (mode: "work" | "demo") => setDraft({ ...draft, mode });
  const command = useMutation(write);
  if (runId)
    return (
      <RunReview
        key={runId}
        read={read}
        write={write}
        runId={runId}
        onBack={() => setRunId(null)}
      />
    );
  const assignmentId = selected ?? assignments.value?.[0]?.id;
  return (
    <section className="training-staff">
      <span className="micro-label">РАБОЧЕЕ МЕСТО ИНСТРУКТОРА</span>
      <h1>Одна ситуация. Разные решения.</h1>
      <p>
        Назначьте группе единый кейс, сравните реальные результаты и оставьте
        обратную связь по конкретным действиям.
      </p>
      <details>
        <summary>Новое назначение группе</summary>
        <form
          className="training-staff-form"
          onSubmit={(e) => {
            e.preventDefault();
            void command
              .send("/training/assignments", { title: title.trim(), mode })
              .then(() => {
                setSelected(null);
                assignments.reload();
              })
              .catch(() => {});
          }}
        >
          <label>
            Название назначения
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={120}
              required
              disabled={command.busy}
            />
          </label>
          <label>
            Формат
            <select
              value={mode}
              onChange={(e) => setMode(e.target.value as "work" | "demo")}
              disabled={command.busy}
            >
              <option value="work">Рабочая смена · 20 минут</option>
              <option value="demo">Демонстрация · 4 минуты</option>
            </select>
          </label>
          <p className="quiet-note">
            Участники получат одинаковый кейс и начальные условия. Состав группы
            определяется сервером.
          </p>
          {command.error && <p role="alert">{command.error}</p>}
          <button disabled={command.busy || !title.trim()}>
            {command.busy ? "Создаём…" : "Назначить группе"}
          </button>
        </form>
      </details>
      <ResourceError message={assignments.error} retry={assignments.reload} />
      {assignments.value?.length ? (
        <>
          <label className="staff-toolbar">
            Назначение
            <select
              aria-label="Назначение группы"
              value={assignmentId}
              onChange={(e) => setSelected(e.target.value)}
            >
              {assignments.value.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.title} · {modeNames[a.mode]} · {a.completed_count}/
                  {a.member_count}
                </option>
              ))}
            </select>
          </label>
          {assignmentId && (
            <AssignmentResults
              key={assignmentId}
              read={read}
              id={assignmentId}
              onReview={setRunId}
            />
          )}
        </>
      ) : (
        !assignments.error && (
          <p>Назначений пока нет. Создайте первый общий кейс.</p>
        )
      )}
    </section>
  );
}
