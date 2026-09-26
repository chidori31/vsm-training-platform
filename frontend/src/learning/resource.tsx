import { useCallback, useEffect, useRef, useState } from "react";
import type { ReadResource } from "../career/contracts";
import { friendlyError } from "../runner/api";

export function useLearningResource<T>(
  read: ReadResource,
  identityId: string,
  path: string,
  parse: (value: unknown) => T,
  keepPreviousData = false,
) {
  const [attempt, setAttempt] = useState(0);
  const generation = useRef(0);
  const sourceKey = JSON.stringify([identityId, path]);
  const key = JSON.stringify([sourceKey, attempt]);
  const [response, setResponse] = useState<{
    key: string;
    sourceKey: string;
    read: ReadResource;
    data: T | null;
    error: string | null;
  } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    const requestGeneration = ++generation.current;
    void read(path, controller.signal)
      .then((value) => {
        if (
          controller.signal.aborted ||
          requestGeneration !== generation.current
        )
          return;
        const data = parse(value);
        setResponse({ key, sourceKey, read, data, error: null });
      })
      .catch((error: unknown) => {
        if (
          !controller.signal.aborted &&
          requestGeneration === generation.current
        )
          setResponse((previous) => ({
            key,
            sourceKey,
            read,
            data:
              keepPreviousData &&
              previous?.sourceKey === sourceKey &&
              previous.read === read
                ? previous.data
                : null,
            error: friendlyError(error),
          }));
      });
    return () => controller.abort();
  }, [read, path, parse, key, sourceKey, keepPreviousData]);
  const current =
    response?.sourceKey === sourceKey &&
    response.read === read &&
    (response.key === key || keepPreviousData)
      ? response
      : null;
  const retry = useCallback(() => setAttempt((value) => value + 1), []);
  const updateData = useCallback(
    (update: (data: T) => T) => {
      generation.current += 1;
      setResponse((previous) =>
        previous?.sourceKey === sourceKey &&
        previous.read === read &&
        previous.data
          ? { ...previous, data: update(previous.data), error: null }
          : previous,
      );
    },
    [sourceKey, read],
  );
  return {
    updateData,
    data: current?.data ?? null,
    error: current?.error ?? null,
    retry,
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
