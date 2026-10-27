/**
 * The guided tour's place, kept across a reload of the page within one launch (adapted from Build-a-Spec's
 * frontend/src/lib/onboardingStorage.ts, copy-based reuse).
 *
 * The record names the tour (the server's id) and the chapter and step the user was at. It only has to survive a
 * reload: the practice workspace it reconnects to lives in the app's memory, so it cannot outlive the launch anyway,
 * which is exactly what browser storage can do in the native window. Wrapped, because a WebView origin may refuse
 * storage. Whether this computer FINISHED the tour is not here: it outlives the launch, so settings.json keeps it
 * (`tutorial_version`, read from the view's `tutorial.finished_version`).
 */
const PROGRESS_KEY = "spec-surgeon:tour-progress";

export interface StoredTourProgress {
  version: number;
  tutorialId: string;
  chapter: number;
  step: number;
}

export function loadTourProgress(expectedVersion: number): StoredTourProgress | null {
  try {
    const raw = localStorage.getItem(PROGRESS_KEY);
    if (!raw) return null;
    const value = JSON.parse(raw) as StoredTourProgress;
    if (
      value.version !== expectedVersion ||
      typeof value.tutorialId !== "string" ||
      !value.tutorialId ||
      !Number.isInteger(value.chapter) ||
      !Number.isInteger(value.step)
    ) {
      localStorage.removeItem(PROGRESS_KEY);
      return null;
    }
    return value;
  } catch {
    return null;
  }
}

export function saveTourProgress(progress: StoredTourProgress): void {
  try {
    localStorage.setItem(PROGRESS_KEY, JSON.stringify(progress));
  } catch {
    // A blocked origin only loses the place after a reload.
  }
}

export function clearTourProgress(): void {
  try {
    localStorage.removeItem(PROGRESS_KEY);
  } catch {
    // Cosmetic state only.
  }
}

/** Pure: the record to keep, or null when the tour is not at a step (a test's helper, and the hook's). */
export function progressRecord(
  version: number,
  tutorialId: string | null,
  chapter: number,
  step: number,
): StoredTourProgress | null {
  if (!tutorialId) return null;
  return { version, tutorialId, chapter, step };
}
