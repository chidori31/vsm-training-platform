import { createContext, useContext, useState } from "react";

export const TrainingIdentity = createContext("isolated");
export function readStored<T>(
  key: string,
  validate: (v: unknown) => T,
): T | null {
  try {
    const value = sessionStorage.getItem(key);
    return value === null ? null : validate(JSON.parse(value));
  } catch {
    return null;
  }
}
export function store(key: string, value: unknown) {
  try {
    if (value === null) sessionStorage.removeItem(key);
    else sessionStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* Storage is optional; the mounted form retains the draft. */
  }
}
export function useSessionDraft<T>(
  scope: string,
  initial: T,
  validate: (v: unknown) => T,
) {
  const identity = useContext(TrainingIdentity);
  const key = `vsm.training.draft.${identity}.${scope}`;
  const [value, setValue] = useState<T>(
    () => readStored(key, validate) ?? initial,
  );
  function update(next: T) {
    store(key, next);
    setValue(next);
  }
  return [value, update, () => store(key, null)] as const;
}
