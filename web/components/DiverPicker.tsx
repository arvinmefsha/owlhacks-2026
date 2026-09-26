"use client";

import { useEffect, useState, useSyncExternalStore } from "react";

import { api, type Diver } from "@/lib/api";

const STORAGE_KEY = "diverId";
const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

/** The selected diver id, remembered across pages. */
export function useSelectedDiver(): [string | null, (id: string) => void] {
  const diverId = useSyncExternalStore(subscribe, () => localStorage.getItem(STORAGE_KEY), () => null);
  const select = (id: string) => {
    localStorage.setItem(STORAGE_KEY, id);
    listeners.forEach((l) => l());
  };
  return [diverId, select];
}

export function DiverPicker({ value, onChange }: { value: string | null; onChange: (id: string) => void }) {
  const [divers, setDivers] = useState<Diver[]>([]);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [height, setHeight] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api<Diver[]>("/divers")
      .then((list) => {
        setDivers(list);
        if (list.length === 0) setAdding(true);
        else if (!list.some((d) => d.id === value)) onChange(list[0].id);
      })
      .catch((e: Error) => setError(e.message));
    // Only load once; `value` is checked against the first response.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function addDiver(event: React.FormEvent) {
    event.preventDefault();
    try {
      const diver = await api<Diver>("/divers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, height_cm: height ? Number(height) : null }),
      });
      setDivers((list) => [...list, diver]);
      onChange(diver.id);
      setAdding(false);
      setName("");
      setHeight("");
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const current = divers.find((d) => d.id === value);
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <select
          className="rounded-md border border-slate-300 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-900"
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value)}
          disabled={divers.length === 0}
        >
          {divers.length === 0 && <option value="">No divers yet</option>}
          {divers.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name}
            </option>
          ))}
        </select>
        <button type="button" className="text-sm text-sky-600 hover:underline" onClick={() => setAdding((a) => !a)}>
          {adding ? "Cancel" : "Add diver"}
        </button>
      </div>
      {current && !current.height_cm && !adding && (
        <p className="text-sm text-amber-600">No height set for {current.name}, so distances will be approximate.</p>
      )}
      {adding && (
        <form onSubmit={addDiver} className="flex flex-wrap items-end gap-2">
          <label className="text-sm">
            Name
            <input
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="mt-1 block rounded-md border border-slate-300 px-3 py-2 dark:border-slate-700 dark:bg-slate-900"
            />
          </label>
          <label className="text-sm">
            Height (cm)
            <input
              type="number"
              min={100}
              max={230}
              value={height}
              onChange={(e) => setHeight(e.target.value)}
              className="mt-1 block w-28 rounded-md border border-slate-300 px-3 py-2 dark:border-slate-700 dark:bg-slate-900"
            />
          </label>
          <button className="rounded-md bg-sky-600 px-3 py-2 text-sm font-medium text-white hover:bg-sky-700">Save</button>
        </form>
      )}
      {error && <p className="text-sm text-red-600">{error}</p>}
    </div>
  );
}
