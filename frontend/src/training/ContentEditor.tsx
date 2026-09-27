import { useState } from "react";
import type { ReadResource } from "../career/contracts";
import type { WriteResource } from "../shift/contracts";
import { clock, metricNames } from "../simulation/contracts";
import {
  check,
  list,
  number,
  object,
  text,
  modeNames,
  type TrainingMode,
} from "./contracts";
import { ResourceError, useResource } from "./TrainingTools";
import { useSessionDraft } from "./storage";
import { useMutation } from "./InstructorPanel";

type Incident = {
  id: string;
  kind: string;
  title: string;
  zones: string[];
  reported_at_seconds: number;
  observation: string;
  facts: string[];
  causes: string[];
  alternatives: string[];
  equipment_id: string | null;
  communication_type: string | null;
  station_id: string | null;
};
export interface ContentDocument {
  schema_version: 2;
  id: string;
  version: number;
  title: string;
  zones: { id: string; title: string; kind: string }[];
  equipment: {
    id: string;
    title: string;
    zone_id: string;
    carried?: boolean;
    available?: boolean;
  }[];
  incidents: Incident[];
  action_durations: Record<string, number>;
  dialogues: Record<string, string>;
  modes: Record<
    TrainingMode,
    {
      duration_seconds: number;
      incident_seconds: Record<string, number>;
      stations: {
        id: string;
        title: string;
        arrival_seconds: number;
        departure_seconds: number;
      }[];
    }
  >;
  methodology_version: string;
  source_notice: string;
  completion_effects?: Record<string, Record<string, number>>;
  completion_explanations?: Record<string, string>;
}
export interface ContentRecord {
  id: string;
  version: number;
  title: string;
  status: "draft" | "published";
  revision: number;
  document: ContentDocument;
}
type Summary = Omit<ContentRecord, "document">;
type Validation = {
  valid: boolean;
  errors: string[];
  warnings: string[];
  timeline: { at_seconds: number; title: string; kind: string }[];
};
function summary(v: unknown) {
  const s = object(v);
  text(s.id);
  text(s.title);
  number(s.version, 1);
  number(s.revision);
  check(s.status === "draft" || s.status === "published");
}
function parseList(v: unknown): Summary[] {
  const items = list(object(v).items);
  items.forEach(summary);
  return items as Summary[];
}
export function parseContent(v: unknown): ContentRecord {
  summary(v);
  const d = object(object(v).document);
  check(d.schema_version === 2);
  text(d.id);
  text(d.title);
  list(d.incidents).forEach((raw) => {
    const i = object(raw);
    ["id", "kind", "title", "observation"].forEach((k) => text(i[k]));
    ["facts", "causes", "alternatives", "zones"].forEach((k) =>
      list(i[k]).forEach(text),
    );
  });
  list(d.zones);
  list(d.equipment);
  object(d.action_durations);
  object(d.dialogues);
  const modes = object(d.modes);
  for (const k of ["work", "tutorial", "demo", "practice"]) {
    const m = object(modes[k]);
    number(m.duration_seconds);
    object(m.incident_seconds);
    list(m.stations);
  }
  return v as ContentRecord;
}
function parseValidation(v: unknown): Validation {
  const r = object(v);
  check(typeof r.valid === "boolean");
  list(r.errors).forEach(text);
  list(r.warnings).forEach(text);
  list(r.timeline).forEach((raw) => {
    const t = object(raw);
    number(t.at_seconds);
    text(t.title);
    text(t.kind);
  });
  return v as Validation;
}
const actionNames: Record<string, string> = {
  move: "Перемещение",
  take: "Подготовка оборудования",
  inspect: "Осмотр",
  talk: "Разговор",
  verify: "Проверка фактов",
  contact: "Вызов службы",
  restrict: "Ограничение доступа",
  assist: "Служебная помощь",
  record: "Запись результата",
  defer: "Отложить обращение",
};
const kinds: Record<string, string> = {
  service: "Сервис",
  safety: "Безопасность",
  conflict: "Конфликт",
  health: "Самочувствие",
  station: "Станция",
};
function Lines({
  label,
  value,
  onChange,
  disabled = false,
}: {
  label: string;
  value: string[];
  onChange: (value: string[]) => void;
  disabled?: boolean;
}) {
  return (
    <label>
      {label}
      <textarea
        disabled={disabled}
        value={value.join("\n")}
        onChange={(e) => onChange(e.target.value.split("\n"))}
      />
      <small>Один пункт на строку</small>
    </label>
  );
}
function ContentForm({
  initial,
  write,
  replace,
  onBack,
  onPublished,
}: {
  initial: ContentRecord;
  write: WriteResource;
  replace: WriteResource;
  onBack: () => void;
  onPublished: () => void;
}) {
  const [savedDraft, setSavedDraft, clearSavedDraft] = useSessionDraft(
    `content.${initial.id}`,
    { document: initial.document, revision: initial.revision },
    (v) => {
      const d = object(v);
      number(d.revision);
      const parsed = parseContent({ ...initial, document: d.document });
      return { document: parsed.document, revision: d.revision };
    },
  );
  const recovered =
    initial.status !== "published" &&
    JSON.stringify(savedDraft.document) !== JSON.stringify(initial.document);
  const [record, setRecord] = useState(() =>
      recovered ? { ...initial, revision: savedDraft.revision } : initial,
    ),
    [document, setDocument] = useState(() =>
      recovered ? savedDraft.document : initial.document,
    ),
    [mode, setMode] = useState<TrainingMode>("work"),
    [dirty, setDirty] = useState(recovered),
    [validation, setValidation] = useState<Validation | null>(null),
    [notice, setNotice] = useState("");
  const post = useMutation(write),
    put = useMutation(replace, "PUT");
  const busy = post.busy || put.busy,
    readonly = record.status === "published";
  function change(update: (draft: ContentDocument) => void) {
    if (readonly || busy) return;
    const draft = structuredClone(document);
    update(draft);
    setSavedDraft({ document: draft, revision: record.revision });
    setDocument(draft);
    setDirty(true);
    setValidation(null);
    setNotice("");
  }
  async function save() {
    const next = parseContent(
      await put.send(`/training/content/${encodeURIComponent(record.id)}`, {
        expected_revision: record.revision,
        document,
      }),
    );
    setRecord(next);
    setDocument(next.document);
    setDirty(false);
    clearSavedDraft();
    setNotice("Черновик сохранён.");
    return next;
  }
  async function validate() {
    try {
      const latest = dirty ? await save() : record;
      setValidation(
        parseValidation(
          await post.send(
            `/training/content/${encodeURIComponent(latest.id)}/validate`,
            {},
          ),
        ),
      );
    } catch {
      /* Mutation reports retained draft errors. */
    }
  }
  async function publish() {
    try {
      const next = parseContent(
        await post.send(
          `/training/content/${encodeURIComponent(record.id)}/publish`,
          { expected_revision: record.revision },
        ),
      );
      setRecord(next);
      setDocument(next.document);
      setNotice(
        "Версия опубликована. Уже начатые поездки продолжаются по своей версии.",
      );
      onPublished();
    } catch {
      /* Keep review and draft visible. */
    }
  }
  return (
    <section className="training-staff">
      <button disabled={busy} onClick={onBack}>
        ← К версиям содержания
      </button>
      <div className="training-section-heading">
        <span className="micro-label">
          {readonly ? "ОПУБЛИКОВАННАЯ ВЕРСИЯ" : "ЧЕРНОВИК МЕТОДИСТА"} ·{" "}
          {record.version}
        </span>
        <h1>{record.title}</h1>
        <p>
          Настройте наблюдения, скрытые факты, ресурсы и последствия.
          Действующая поездка сохраняет свою версию кейса.
        </p>
      </div>
      {recovered && dirty && (
        <p className="quiet-note">
          Восстановлены локальные правки этой вкладки. Сохраните черновик перед
          публикацией.
        </p>
      )}
      {!readonly && savedDraft.revision < initial.revision && dirty && (
        <section
          className="validation-preview"
          aria-label="Восстановление после конфликта"
        >
          <h2>Серверный черновик уже изменён</h2>
          <p>
            Локальные правки сделаны в ревизии {savedDraft.revision}; на сервере
            ревизия {initial.revision}. Сравните документы перед сохранением.
            Перенос использует весь локальный документ и может заменить
            изменения другого методиста.
          </p>
          <details>
            <summary>Актуальный документ сервера</summary>
            <pre className="training-source">
              {JSON.stringify(initial.document, null, 2)}
            </pre>
          </details>
          <div className="staff-toolbar">
            <button
              disabled={busy}
              onClick={() => {
                setRecord(initial);
                setSavedDraft({ document, revision: initial.revision });
                setValidation(null);
                setNotice(
                  "Правки перенесены на актуальную ревизию. Проверьте документ, сохраните и выполните валидацию.",
                );
              }}
            >
              Перенести мои правки на актуальную ревизию
            </button>
            <button
              disabled={busy}
              onClick={() => {
                setRecord(initial);
                setDocument(initial.document);
                setSavedDraft({
                  document: initial.document,
                  revision: initial.revision,
                });
                clearSavedDraft();
                setDirty(false);
                setValidation(null);
                setNotice("Загружен актуальный документ сервера.");
              }}
            >
              Загрузить серверный черновик вместо локального
            </button>
          </div>
        </section>
      )}
      {notice && <p role="status">{notice}</p>}
      {(post.error || put.error) && (
        <p className="training-error" role="alert">
          {post.error || put.error} Введённые изменения остаются в форме. При
          конфликте версии откройте актуальный черновик через список.
        </p>
      )}
      <fieldset
        disabled={busy || readonly}
        className="training-editor-fieldset"
      >
        <label>
          Название кейса
          <input
            value={document.title}
            maxLength={500}
            onChange={(e) =>
              change((d) => {
                d.title = e.target.value;
              })
            }
          />
        </label>
        <div className="staff-toolbar">
          <label>
            Расписание режима
            <select
              value={mode}
              onChange={(e) => setMode(e.target.value as TrainingMode)}
            >
              {Object.keys(modeNames).map((m) => (
                <option key={m} value={m}>
                  {modeNames[m as TrainingMode]}
                </option>
              ))}
            </select>
          </label>
          <span>
            {document.modes[mode].duration_seconds} секунд · длительность режима
            задана платформой
          </span>
        </div>
        {document.incidents.map((incident, index) => (
          <section className="editor-incident" key={incident.id}>
            <h3>
              {kinds[incident.kind] ?? incident.kind} · {incident.title}
            </h3>
            <div className="editor-fields">
              <label>
                Название обращения
                <input
                  value={incident.title}
                  maxLength={500}
                  onChange={(e) =>
                    change((d) => {
                      d.incidents[index].title = e.target.value;
                    })
                  }
                />
              </label>
              <label>
                Начало события в этом режиме, секунд
                <input
                  type="number"
                  min={0}
                  max={document.modes[mode].duration_seconds - 1}
                  disabled={
                    document.modes[mode].incident_seconds[incident.id] ===
                    undefined
                  }
                  value={
                    document.modes[mode].incident_seconds[incident.id] ?? ""
                  }
                  onChange={(e) =>
                    change((d) => {
                      d.modes[mode].incident_seconds[incident.id] = Number(
                        e.target.value,
                      );
                      if (mode === "work")
                        d.incidents[index].reported_at_seconds = Number(
                          e.target.value,
                        );
                    })
                  }
                />
                <small>
                  {document.modes[mode].incident_seconds[incident.id] ===
                  undefined
                    ? "Обращение не входит в расписание этого режима"
                    : "От начала поездки; время одинаково в общем назначении"}
                </small>
              </label>
              <label className="wide">
                Что видит проводник
                <textarea
                  value={incident.observation}
                  onChange={(e) =>
                    change((d) => {
                      d.incidents[index].observation = e.target.value;
                    })
                  }
                />
              </label>
              <Lines
                label="Факты, открываемые при проверке"
                value={incident.facts}
                onChange={(v) =>
                  change((d) => {
                    d.incidents[index].facts = v;
                  })
                }
              />
              <Lines
                label="Варианты скрытой причины"
                value={incident.causes}
                onChange={(v) =>
                  change((d) => {
                    d.incidents[index].causes = v;
                  })
                }
              />
              <label>
                Необходимое оборудование
                <select
                  value={incident.equipment_id ?? ""}
                  onChange={(e) =>
                    change((d) => {
                      d.incidents[index].equipment_id = e.target.value || null;
                    })
                  }
                >
                  <option value="">Не требуется</option>
                  {document.equipment.map((e) => (
                    <option key={e.id} value={e.id}>
                      {e.title}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Ответственная служба
                <select
                  value={incident.communication_type ?? ""}
                  onChange={(e) =>
                    change((d) => {
                      d.incidents[index].communication_type =
                        e.target.value || null;
                    })
                  }
                >
                  <option value="">Не требуется</option>
                  <option value="chief">Начальник поезда</option>
                  <option value="technical">Техническая служба</option>
                  <option value="medical">Медицинская служба</option>
                </select>
              </label>
              <label>
                Реплика после разговора
                <textarea
                  value={document.dialogues[incident.kind] ?? ""}
                  onChange={(e) =>
                    change((d) => {
                      d.dialogues[incident.kind] = e.target.value;
                    })
                  }
                />
              </label>
              <Lines
                label="Объяснения альтернатив и последствий"
                value={incident.alternatives}
                onChange={(v) =>
                  change((d) => {
                    d.incidents[index].alternatives = v;
                  })
                }
              />
              {document.completion_effects && (
                <>
                  <label className="wide">
                    Объяснение дополнительного последствия
                    <textarea
                      value={
                        document.completion_explanations?.[incident.kind] ?? ""
                      }
                      onChange={(e) =>
                        change((d) => {
                          d.completion_explanations ??= {};
                          d.completion_explanations[incident.kind] =
                            e.target.value;
                        })
                      }
                    />
                  </label>
                  {Object.entries(metricNames).map(([metric, title]) => (
                    <label key={metric}>
                      {title}: дополнительное изменение после помощи
                      <input
                        type="number"
                        min={-20}
                        max={20}
                        value={
                          document.completion_effects?.[incident.kind]?.[
                            metric
                          ] ?? 0
                        }
                        onChange={(e) =>
                          change((d) => {
                            d.completion_effects ??= {};
                            d.completion_effects[incident.kind] ??= {};
                            d.completion_effects[incident.kind][metric] =
                              Number(e.target.value);
                          })
                        }
                      />
                    </label>
                  ))}
                </>
              )}
            </div>
          </section>
        ))}
        <details>
          <summary>Длительность действий и окна остановок</summary>
          <div className="editor-fields">
            {Object.entries(document.action_durations).map(([id, seconds]) => (
              <label key={id}>
                {actionNames[id] ?? id}, секунд
                <input
                  type="number"
                  min={1}
                  max={60}
                  value={seconds}
                  onChange={(e) =>
                    change((d) => {
                      d.action_durations[id] = Number(e.target.value);
                    })
                  }
                />
              </label>
            ))}
          </div>
          {document.modes[mode].stations.map((station, index) => (
            <div className="editor-incident" key={station.id}>
              <strong>{station.title}</strong>
              <div className="editor-fields">
                <label>
                  Прибытие, секунд
                  <input
                    type="number"
                    min={0}
                    value={station.arrival_seconds}
                    onChange={(e) =>
                      change((d) => {
                        d.modes[mode].stations[index].arrival_seconds = Number(
                          e.target.value,
                        );
                      })
                    }
                  />
                </label>
                <label>
                  Отправление, секунд
                  <input
                    type="number"
                    min={0}
                    value={station.departure_seconds}
                    onChange={(e) =>
                      change((d) => {
                        d.modes[mode].stations[index].departure_seconds =
                          Number(e.target.value);
                      })
                    }
                  />
                </label>
              </div>
            </div>
          ))}
        </details>
      </fieldset>
      <div className="staff-toolbar">
        {!readonly && (
          <>
            <button
              disabled={busy || !dirty}
              onClick={() => void save().catch(() => {})}
            >
              Сохранить черновик
            </button>
            <button disabled={busy} onClick={() => void validate()}>
              Проверить и показать расписание
            </button>
            <button
              disabled={busy || dirty || !validation?.valid}
              onClick={() => void publish()}
            >
              Опубликовать версию
            </button>
          </>
        )}
        <span className="quiet-note">
          {readonly
            ? "Для изменения создайте новый черновик в списке версий."
            : dirty
              ? "Есть несохранённые изменения"
              : "Изменения сохранены"}
        </span>
      </div>
      {validation && (
        <section
          className="validation-preview"
          aria-label="Проверка содержания"
        >
          <h2>
            {validation.valid
              ? "Содержание прошло проверку"
              : "Публикация пока недоступна"}
          </h2>
          {validation.errors.map((e, n) => (
            <p className="validation-error" key={n}>
              Ошибка: {e}
            </p>
          ))}
          {validation.warnings.map((w, n) => (
            <p className="validation-warning" key={n}>
              Обратите внимание: {w}
            </p>
          ))}
          <ol>
            {validation.timeline.map((e, n) => (
              <li key={n}>
                T+{clock(e.at_seconds)} · {e.title} <small>{e.kind}</small>
              </li>
            ))}
          </ol>
        </section>
      )}
      <details>
        <summary>Исходный JSON этой версии</summary>
        <pre className="training-source">
          {JSON.stringify(document, null, 2)}
        </pre>
      </details>
    </section>
  );
}
function LoadContent({
  read,
  write,
  replace,
  id,
  onBack,
  onPublished,
}: {
  read: ReadResource;
  write: WriteResource;
  replace: WriteResource;
  id: string;
  onBack: () => void;
  onPublished: () => void;
}) {
  const resource = useResource(
    read,
    `/training/content/${encodeURIComponent(id)}`,
    parseContent,
  );
  return (
    <>
      <ResourceError message={resource.error} retry={resource.reload} />
      {resource.value ? (
        <ContentForm
          initial={resource.value}
          write={write}
          replace={replace}
          onBack={onBack}
          onPublished={onPublished}
        />
      ) : (
        !resource.error && <p role="status">Открываем версию…</p>
      )}
    </>
  );
}
export function ContentEditor({
  read,
  write,
  replace,
}: {
  read: ReadResource;
  write: WriteResource;
  replace: WriteResource;
}) {
  const versions = useResource(read, "/training/content", parseList),
    command = useMutation(write);
  const [selected, setSelected] = useState<string | null>(null);
  async function draft(sourceId?: string) {
    try {
      const result = parseContent(
        await command.send(
          "/training/content/drafts",
          sourceId ? { source_id: sourceId } : {},
        ),
      );
      versions.reload();
      setSelected(result.id);
    } catch {
      /* The list remains available. */
    }
  }
  if (selected)
    return (
      <LoadContent
        key={selected}
        read={read}
        write={write}
        replace={replace}
        id={selected}
        onBack={() => {
          setSelected(null);
          versions.reload();
        }}
        onPublished={versions.reload}
      />
    );
  return (
    <section className="training-staff">
      <span className="micro-label">РАБОЧЕЕ МЕСТО МЕТОДИСТА</span>
      <h1>Содержание учебных поездок</h1>
      <p>
        Изменяйте кейс в черновике. Перед публикацией проверяются ссылки, сроки
        действий и доступность ресурсов.
      </p>
      <div className="staff-toolbar">
        <button disabled={command.busy} onClick={() => void draft()}>
          Новый черновик
        </button>
      </div>
      {command.error && <p role="alert">{command.error}</p>}
      <ResourceError message={versions.error} retry={versions.reload} />
      {versions.value ? (
        <ul className="staff-version-list">
          {versions.value.map((v) => (
            <li key={v.id}>
              <div>
                <strong>{v.title}</strong>
                <small>
                  Версия {v.version} ·{" "}
                  {v.status === "published" ? "Опубликована" : "Черновик"}
                </small>
              </div>
              <div className="staff-toolbar">
                <button onClick={() => setSelected(v.id)}>Открыть</button>
                <button
                  disabled={command.busy}
                  onClick={() => void draft(v.id)}
                >
                  Создать на основе
                </button>
              </div>
            </li>
          ))}
        </ul>
      ) : (
        !versions.error && <p role="status">Загружаем версии…</p>
      )}
    </section>
  );
}
