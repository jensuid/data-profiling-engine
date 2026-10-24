/**
 * M6-T2 — cross-screen UI selection state.
 *
 * HashRouter reloads a route from the URL alone (ADR-013), so data the user
 * confirmed on one screen must survive navigation to the next: the Run
 * Configuration screen reads the dataset confirmed on the Dataset screen, and
 * the Run Progress screen (M6-T3) reads the job started here. These are tiny
 * in-memory external stores (React 18 `useSyncExternalStore`), NOT persistence
 * — anything that must survive an app restart lives in the SQLite metadata
 * store and is recovered via the job manager (§6.3), never in the WebView.
 */
import { useSyncExternalStore } from "react";

/** A source file the user confirmed via a successful ``dataset.inspect``. */
export interface SelectedDataset {
  sourcePath: string;
  datasetId: string;
  /** Column names in file order (from the inspect result), for rule pickers. */
  columns: string[];
}

/** The most recent job accepted by ``profile.start`` (§5.3). */
export interface StartedJob {
  jobId: string;
  datasetId: string;
  sourcePath: string;
  /** ISO-8601 wall-clock of acceptance — display only, never authoritative. */
  startedAt: string;
}

type Listener = () => void;

function createSlot<T>() {
  let value: T | null = null;
  const listeners = new Set<Listener>();
  return {
    get: (): T | null => value,
    set: (next: T | null): void => {
      value = next;
      listeners.forEach((l) => l());
    },
    subscribe: (l: Listener): (() => void) => {
      listeners.add(l);
      return () => {
        listeners.delete(l);
      };
    },
  };
}

const datasetSlot = createSlot<SelectedDataset>();
const jobSlot = createSlot<StartedJob>();

export const setSelectedDataset = datasetSlot.set;
export const getSelectedDataset = datasetSlot.get;
export const setStartedJob = jobSlot.set;
export const getStartedJob = jobSlot.get;

/** Reactive hook: the dataset confirmed on the Dataset screen, or null. */
export function useSelectedDataset(): SelectedDataset | null {
  return useSyncExternalStore(datasetSlot.subscribe, datasetSlot.get);
}

/** Reactive hook: the last accepted job (consumed by M6-T3 progress screen). */
export function useStartedJob(): StartedJob | null {
  return useSyncExternalStore(jobSlot.subscribe, jobSlot.get);
}
