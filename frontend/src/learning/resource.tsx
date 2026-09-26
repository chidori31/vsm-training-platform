import { useEffect, useState } from "react";
import type { ReadResource } from "../career/contracts";
import { friendlyError } from "../runner/api";

export function useLearningResource<T>(
  read: ReadResource,
  identityId: string,
  path: string,
  parse: (value: unknown) => T,
) {
  const [attempt, setAttempt] = useState(0);
  const key = JSON.stringify([identityId, path, attempt]);
  const [response, setResponse] = useState<{
    key: string;
    read: ReadResource;
    data: T | null;
    error: string | null;
  } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void read(path, controller.signal)
      .then((value) => {
        if (controller.signal.aborted) return;
        const data = parse(value);
        setResponse({ key, read, data, error: null });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted)
          setResponse({ key, read, data: null, error: friendlyError(error) });
      });
    return () => controller.abort();
  }, [read, path, parse, key]);
  const current =
    response?.key === key && response.read === read ? response : null;
  return {
    data: current?.data ?? null,
    error: current?.error ?? null,
    retry: () => setAttempt((value) => value + 1),
  };
}

export function LearningLoadState({
  kind,
  error,
  retry,
}: {
  kind: "debrief" | "analytics";
  error: string | null;
  retry: () => void;
}) {
  return error ? (
    <div className="learning-error" role="alert">
      <strong>
        {kind === "debrief"
          ? "Разбор пока недоступен"
          : "Аналитика пока недоступна"}
      </strong>
      <p>{error}</p>
      <button className="text-button" onClick={retry}>
        {kind === "debrief"
          ? "Повторить загрузку разбора"
          : "Повторить загрузку аналитики"}
        <span aria-hidden="true">↗</span>
      </button>
    </div>
  ) : (
    <p className="learning-loading" role="status">
      {kind === "debrief"
        ? "Загружаем разбор решений…"
        : "Загружаем аналитику компетенций…"}
    </p>
  );
}

export function signed(value: number) {
  return value > 0 ? `+${value}` : String(value);
}

export function displayNumber(value: number) {
  return value.toLocaleString("ru-RU", { maximumFractionDigits: 1 });
}

export function displayDate(value: string) {
  return new Date(value).toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
