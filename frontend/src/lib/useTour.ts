/**
 * The guided tour's lifecycle (adapted from Build-a-Spec's frontend/src/lib/useOnboarding.ts, copy-based reuse):
 * start the practice workspace, move through the chapters (asking the server for each chapter's state), and end
 * the one way a tour ends — the user's own conversation back on screen (docs/WEB_API.md, the tour).
 *
 * The tour is a fixed track the user only watches, so it drives no app action of its own; what it needs from the
 * window is small (`TourHost`): a way to show the view the server answers with, the current view's `tutorial` block
 * (to pick a reload back up), and the one page-side preparation a step asks for (the Safe-mode form).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { View } from "../types.ts";
import {
  ApiError,
  getState,
  getTutorialStatus,
  restoreTutorial,
  saveTutorialVersion,
  startTutorial,
  tutorialScenario,
} from "./api.ts";
import { TOUR, TOUR_COMPLETION_VERSION, TOUR_VERSION, chapterIndex, type TourStep } from "./tour.ts";
import { clearTourProgress, loadTourProgress, saveTourProgress } from "./tourStorage.ts";

export type TourPhase =
  | { kind: "idle" }
  | {
      kind: "preparing";
      stage: "starting" | "chapter" | "finishing";
      label?: string;
      target?: { chapter: number; step: number };
      error: string | null;
    }
  | { kind: "touring"; chapter: number; step: number }
  | { kind: "break"; next: number };

export interface TourHost {
  /** Show the view the server answered a tour transition with. */
  applyView: (view: View) => void;
  /** The current view's tour block (a reload is picked back up from it). */
  tutorial: View["tutorial"];
  /** The first view has arrived (the resume check waits for it). */
  loaded: boolean;
  /** A step's page-side preparation (lib/tour.ts `prepare`): the Safe-mode form it points at. */
  prepare: (step: TourStep) => void;
}

export interface TourApi {
  phase: TourPhase;
  /** The tour holds the practice workspace (any phase but idle). */
  active: boolean;
  endConfirm: boolean;
  start: () => void;
  startAtChapter: (chapter: string | number) => void;
  retry: () => void;
  advance: () => void;
  back: () => void;
  continueChapter: () => void;
  requestEnd: () => void;
  cancelEnd: () => void;
  /** End the tour; the last step's Continue is this too. */
  end: () => void;
  /** Leave a failed ending without retrying it: the tour is still live at its last step. */
  stayInTour: () => void;
  /** Teardown from outside (New conversation): the conversation back, nothing marked finished. */
  abort: () => Promise<boolean>;
}

interface Workspace {
  id: string;
  scenario: string | null;
}

/** How long the ending waits for the finished-tour save, at most: a window closed right after Finish must not lose
 *  it, and a hung request must not strand the finishing card. */
export const COMPLETION_SAVE_WAIT_MS = 5000;

const requestId = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `tour-${Date.now()}-${Math.random().toString(36).slice(2)}`;

const failureText = (failure: unknown) => (failure instanceof Error ? failure.message : String(failure));

/** A refusal that means the tour is not what the page thinks (ended elsewhere, another tour): 409 from the server. */
const conflict = (failure: unknown) => failure instanceof ApiError && failure.status === 409;

export function useTour(host: TourHost): TourApi {
  const [phase, setPhase] = useState<TourPhase>({ kind: "idle" });
  const [endConfirm, setEndConfirm] = useState(false);
  const hostRef = useRef(host);
  hostRef.current = host;
  const phaseRef = useRef(phase);
  phaseRef.current = phase;
  const workspaceRef = useRef<Workspace | null>(null);
  const runRef = useRef(0);
  const startRequestRef = useRef<string | null>(null);
  const startInFlightRef = useRef(false);
  const pendingChapterRef = useRef(0);
  const lastStepRef = useRef({ chapter: 0, step: 0 });
  const pendingCompletedRef = useRef(false);
  const restoreInFlightRef = useRef<Promise<boolean> | null>(null);
  const enterChapterRef = useRef<((chapter: number, step?: number) => void) | null>(null);
  const restoreRef = useRef<((completed: boolean) => void) | null>(null);

  const persist = useCallback((chapter: number, step: number) => {
    lastStepRef.current = { chapter, step };
    const workspace = workspaceRef.current;
    if (!workspace) return;
    saveTourProgress({ version: TOUR_VERSION, tutorialId: workspace.id, chapter, step });
  }, []);

  const showStep = useCallback(
    (chapter: number, step: number) => {
      setPhase({ kind: "touring", chapter, step });
      persist(chapter, step);
      const current = TOUR[chapter].steps[step];
      if (current.prepare) hostRef.current.prepare(current);
    },
    [persist],
  );

  /** The ending: the user's conversation back; `completed` only records that the tour was finished. */
  const runRestore = useCallback(async (completed: boolean): Promise<boolean> => {
    const workspace = workspaceRef.current;
    setEndConfirm(false);
    pendingCompletedRef.current = completed;
    const settle = (view?: View) => {
      if (view) hostRef.current.applyView(view);
      clearTourProgress();
      workspaceRef.current = null;
      startRequestRef.current = null;
      runRef.current += 1;
      setPhase({ kind: "idle" });
    };
    if (!workspace) {
      settle();
      return true;
    }
    const run = (runRef.current += 1);
    setPhase({ kind: "preparing", stage: "finishing", error: null });
    // Saved beside the restore and awaited before the tour goes idle, bounded so it can never strand the card.
    const completionSaved: Promise<unknown> = completed
      ? Promise.race([
          saveTutorialVersion(TOUR_COMPLETION_VERSION).catch((failure: unknown) => {
            console.debug("Finishing the tour could not be saved", failure);
          }),
          new Promise((resolve) => setTimeout(resolve, COMPLETION_SAVE_WAIT_MS)),
        ])
      : Promise.resolve();
    try {
      let view: View;
      try {
        view = await restoreTutorial(workspace.id);
      } catch (first) {
        if (!conflict(first)) throw first;
        // The tour ended elsewhere: the goal state is reached, the conversation is what the server shows now.
        const status = await getTutorialStatus();
        if (status.active) throw first;
        view = await getState();
      }
      await completionSaved;
      if (runRef.current !== run) return true;
      settle(view);
      return true;
    } catch (failure) {
      if (runRef.current !== run) return true;
      setPhase({ kind: "preparing", stage: "finishing", error: failureText(failure) });
      return false;
    }
  }, []);

  /** Restores are serialized: a second End joins the running one instead of racing it. */
  const restore = useCallback(
    (completed: boolean): Promise<boolean> => {
      const inFlight = restoreInFlightRef.current;
      if (inFlight) return inFlight;
      const attempt = runRestore(completed);
      restoreInFlightRef.current = attempt;
      const clear = () => {
        if (restoreInFlightRef.current === attempt) restoreInFlightRef.current = null;
      };
      attempt.then(clear, clear);
      return attempt;
    },
    [runRestore],
  );
  restoreRef.current = (completed) => {
    void restore(completed);
  };

  const enterChapter = useCallback(
    async (chapter: number, step = 0) => {
      const workspace = workspaceRef.current;
      if (!workspace) return;
      const run = runRef.current;
      const wanted = TOUR[chapter].scenario;
      if (workspace.scenario === wanted) {
        showStep(chapter, step);
        return;
      }
      setPhase({
        kind: "preparing",
        stage: "chapter",
        label: TOUR[chapter].title,
        target: { chapter, step },
        error: null,
      });
      try {
        const view = await tutorialScenario(workspace.id, wanted);
        if (runRef.current !== run) return;
        hostRef.current.applyView(view);
        workspace.scenario = view.tutorial.scenario ?? wanted;
        showStep(chapter, step);
      } catch (failure) {
        if (runRef.current !== run) return;
        if (conflict(failure)) {
          const status = await getTutorialStatus().catch(() => null);
          if (status && !status.active) {
            // The tour ended elsewhere (a close, a restore that raced this): the conversation is back already.
            const view = await getState().catch(() => null);
            if (view) hostRef.current.applyView(view);
            clearTourProgress();
            workspaceRef.current = null;
            setPhase({ kind: "idle" });
            return;
          }
        }
        setPhase({
          kind: "preparing",
          stage: "chapter",
          label: TOUR[chapter].title,
          target: { chapter, step },
          error: failureText(failure),
        });
      }
    },
    [showStep],
  );
  enterChapterRef.current = (chapter, step) => {
    void enterChapter(chapter, step);
  };

  const begin = useCallback(async () => {
    // A second click while a start is in flight joins it: the request keeps its id, and the resolved start reads
    // pendingChapterRef, so a click that only moved the chapter has had its effect.
    if (startInFlightRef.current) return;
    startInFlightRef.current = true;
    const run = (runRef.current += 1);
    setPhase({ kind: "preparing", stage: "starting", error: null });
    try {
      const pending = startRequestRef.current ?? requestId();
      startRequestRef.current = pending;
      let view: View;
      try {
        view = await startTutorial(pending);
      } catch (failure) {
        // A tour is already running (an earlier start landed, or this page reloaded mid-start): adopt it.
        if (!conflict(failure)) throw failure;
        const status = await getTutorialStatus();
        if (!status.active || !status.id) throw failure;
        view = await getState();
      }
      if (runRef.current !== run) {
        await restoreTutorial(view.tutorial.id).catch(() => undefined);
        return;
      }
      if (!view.tutorial.active || !view.tutorial.id) throw new Error("The tour did not start.");
      workspaceRef.current = { id: view.tutorial.id, scenario: view.tutorial.scenario };
      startRequestRef.current = null;
      hostRef.current.applyView(view);
      enterChapterRef.current?.(pendingChapterRef.current, 0);
    } catch (failure) {
      if (runRef.current !== run) return;
      setPhase({ kind: "preparing", stage: "starting", error: failureText(failure) });
    } finally {
      startInFlightRef.current = false;
    }
  }, []);

  const start = useCallback(() => {
    setEndConfirm(false);
    // Already holding the practice workspace: nothing to start (the chip and the Tour button sit inside the tour).
    if (workspaceRef.current) return;
    pendingChapterRef.current = 0;
    void begin();
  }, [begin]);

  const startAtChapter = useCallback(
    (chapter: string | number) => {
      const index = chapterIndex(chapter);
      pendingChapterRef.current = index;
      setEndConfirm(false);
      if (workspaceRef.current) {
        runRef.current += 1;
        enterChapterRef.current?.(index, 0);
        return;
      }
      void begin();
    },
    [begin],
  );

  const retry = useCallback(() => {
    const current = phaseRef.current;
    if (current.kind !== "preparing") return;
    if (current.stage === "chapter" && current.target) {
      enterChapterRef.current?.(current.target.chapter, current.target.step);
    } else if (current.stage === "finishing") {
      restoreRef.current?.(pendingCompletedRef.current);
    } else {
      void begin();
    }
  }, [begin]);

  const advance = useCallback(() => {
    const current = phaseRef.current;
    if (current.kind !== "touring") return;
    const chapter = TOUR[current.chapter];
    if (current.step + 1 < chapter.steps.length) {
      showStep(current.chapter, current.step + 1);
    } else if (current.chapter + 1 < TOUR.length) {
      setPhase({ kind: "break", next: current.chapter + 1 });
      persist(current.chapter, current.step);
    } else {
      // The last step's Continue says what it does: the click is the consent, nothing else is asked.
      restoreRef.current?.(true);
    }
  }, [persist, showStep]);

  const back = useCallback(() => {
    const current = phaseRef.current;
    if (current.kind !== "touring") return;
    if (current.step > 0) showStep(current.chapter, current.step - 1);
    else if (current.chapter > 0) {
      const chapter = current.chapter - 1;
      void enterChapter(chapter, TOUR[chapter].steps.length - 1);
    }
  }, [enterChapter, showStep]);

  const continueChapter = useCallback(() => {
    const current = phaseRef.current;
    if (current.kind === "break") void enterChapter(current.next);
  }, [enterChapter]);

  const requestEnd = useCallback(() => setEndConfirm(true), []);
  const cancelEnd = useCallback(() => setEndConfirm(false), []);
  const end = useCallback(() => {
    void restore(true);
  }, [restore]);
  const abort = useCallback(() => restore(false), [restore]);

  const stayInTour = useCallback(() => {
    if (!workspaceRef.current) {
      setPhase({ kind: "idle" });
      return;
    }
    const { chapter, step } = lastStepRef.current;
    setPhase({ kind: "touring", chapter, step });
  }, []);

  // A reload while the practice workspace is up: the first view says so, and the saved place says where. Without a
  // place to go back to, the tour ends and the conversation comes back — the only outcome there has ever been.
  const resumedRef = useRef(false);
  useEffect(() => {
    if (!host.loaded || resumedRef.current) return;
    resumedRef.current = true;
    const stored = loadTourProgress(TOUR_VERSION);
    const current = host.tutorial;
    if (!current.active || !current.id) {
      if (stored) clearTourProgress();
      return;
    }
    if (phaseRef.current.kind !== "idle") return;
    if (!stored || stored.tutorialId !== current.id) {
      clearTourProgress();
      workspaceRef.current = { id: current.id, scenario: current.scenario };
      restoreRef.current?.(false);
      return;
    }
    workspaceRef.current = { id: current.id, scenario: current.scenario };
    const chapter = Math.min(stored.chapter, TOUR.length - 1);
    enterChapterRef.current?.(chapter, Math.min(stored.step, TOUR[chapter].steps.length - 1));
  }, [host.loaded, host.tutorial]);

  return {
    phase,
    active: phase.kind !== "idle",
    endConfirm,
    start,
    startAtChapter,
    retry,
    advance,
    back,
    continueChapter,
    requestEnd,
    cancelEnd,
    end,
    stayInTour,
    abort,
  };
}
