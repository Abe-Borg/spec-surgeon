// Adapted from Build-a-Spec's frontend/src/App.tsx (copy-based reuse): the
// layout and the one place the job stream's events dispatch.
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import type { Attachment, Choice, JobEvent, Mode, Recipe, RecipeInputs, Update, View } from "./types";
import * as api from "./lib/api";
import { actionButton, discardButton } from "./lib/action";
import { composerState, transcript } from "./lib/chat";
import { canReview, heldSources, keptAcrossTour, reviewKey, setAsideCount, setMark, type KeptReviews, type Mark, type Reviews } from "./lib/review";
import { canUntick, occurrencesKey, previewedUnticks, toggleUntick, untickedKeys, unticksPending, type Unticks } from "./lib/occurrences";
import { INITIAL_STATE, reduce } from "./lib/view";
import { ACCEPT } from "./lib/workbench";
import { checkSettled, updatePill, type HelpTopic } from "./lib/help";
import { reportPage } from "./lib/diagnostics";
import Header from "./components/Header";
import Chat from "./components/Chat";
import Workbench from "./components/Workbench";
import UpdateBanner from "./components/UpdateBanner";
import SettingsModal from "./components/SettingsModal";
import HelpModal from "./components/HelpModal";
import ZoomModal from "./components/ZoomModal";
import SafePanel from "./components/SafePanel";
import { EMPTY_SAFE_FORM, openForm, type SafeFormState } from "./lib/recipes";
import ObliterateWarning from "./components/ObliterateWarning";
import { modeChoice } from "./lib/modes";
import TourOverlay from "./components/TourOverlay";
import { TOUR_COMPLETION_VERSION, TOUR_FORM, type TourStep } from "./lib/tour";
import { useTour } from "./lib/useTour";

type PickKind = "documents" | "template" | "references";

function failureText(failure: unknown): string {
  return failure instanceof Error ? failure.message : String(failure);
}

export default function App() {
  const [state, dispatch] = useReducer(reduce, INITIAL_STATE);
  const { view } = state;
  const busy = view.busy;
  const busyRef = useRef(false);
  const viewRef = useRef(view);
  viewRef.current = view;

  const [notice, setNotice] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [draft, setDraft] = useState("");
  const [safeForm, setSafeForm] = useState<SafeFormState>(EMPTY_SAFE_FORM);
  const [prefill, setPrefill] = useState({ text: "", nonce: 0 });
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [helpTopic, setHelpTopic] = useState<HelpTopic | null>(null);
  const [checkingUpdate, setCheckingUpdate] = useState(false);
  // About asked for a check while this window was open: its answer stays on About, closed and reopened or not.
  const [updateAsked, setUpdateAsked] = useState(false);
  const [zoom, setZoom] = useState<{ src: string; title: string } | null>(null);
  const [dismissedUpdate, setDismissedUpdate] = useState("");
  const [dragDepth, setDragDepth] = useState(0);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const pendingPickRef = useRef<PickKind>("documents");

  // --- The view ------------------------------------------------------------

  const refresh = useCallback(async () => {
    try {
      const view = await api.getState();
      // A job attached to a stream owns the view until its `done`: a fetch
      // answered after a new job started must not idle the page under it.
      if (busyRef.current) return;
      dispatch({ type: "view", view });
    } catch (failure) {
      setNotice(failureText(failure));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    document.title = view.version ? `Spec Surgeon ${view.version}` : "Spec Surgeon";
  }, [view.version]);

  // A notice clears itself after a while; the next one replaces it.
  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 9000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  // The server says busy but no stream is attached here (the page was opened
  // while a job ran): follow it by polling until the job ends.
  useEffect(() => {
    if (!busy || busyRef.current) return;
    const timer = window.setInterval(() => void refresh(), 2000);
    return () => window.clearInterval(timer);
  }, [busy, refresh]);

  // --- Jobs ----------------------------------------------------------------

  const runJob = useCallback(
    async (stream: AsyncGenerator<JobEvent>, userText: string | null, action: string) => {
      if (busyRef.current) return;
      busyRef.current = true;
      dispatch({ type: "job_started", userText });
      let terminal = false;
      let resend = "";
      try {
        for await (const event of stream) {
          dispatch({ type: "event", event });
          if (event.type === "done" || event.type === "error") terminal = true;
          // A request refused before it was recorded (no API key yet) comes back with the error; the
          // composer takes it back so nothing the user typed is lost.
          if (event.type === "error" && typeof event.resend === "string" && event.resend) resend = event.resend;
        }
        if (!terminal) {
          // The stream closed without its done/error: the app's log should know the page lost the job.
          reportPage({ kind: "stream", action, reason: "cut" }, true);
          dispatch({ type: "job_ended" });
        }
      } catch (failure) {
        // A refusal (busy, no token) is an answer; anything else lost the job's stream on the way.
        if (!(failure instanceof api.ApiError)) {
          reportPage({ kind: "stream", action, reason: "failed", message: failureText(failure) }, true);
        }
        dispatch({ type: "job_failed", message: failureText(failure) });
      } finally {
        busyRef.current = false;
        if (resend) {
          setDraft(resend);
          setPrefill((p) => ({ text: resend, nonce: p.nonce + 1 }));
        }
        // The view that ends a job is authoritative; the state route is the
        // same view, fetched once more in case the stream was cut short.
        void refresh();
      }
    },
    [refresh],
  );

  const onSend = useCallback((text: string, chip = false) => void runJob(api.send(text, chip), text, "send"), [runJob]);
  const onChoose = useCallback(
    (choice: Choice, wording?: string) =>
      void runJob(api.choose(choice.id, wording), wording === undefined ? choice.label : `${choice.label} — “${wording}”`, "choose"),
    [runJob],
  );
  const onRecipe = useCallback(
    (recipe: Recipe, inputs: RecipeInputs) => void runJob(api.recipe(recipe.id, inputs), recipe.label, "recipe"),
    [runJob],
  );
  const onStop = useCallback(() => {
    api.stop().catch((failure: unknown) => setNotice(failureText(failure)));
  }, []);

  // --- The review of the previews ------------------------------------------
  // A green checkmark or a red X per preview; the marks belong to one set of
  // previews and start over when a new plan or preview arrives.
  const [reviews, setReviews] = useState<Reviews>({});
  const key = reviewKey(view);
  const keyRef = useRef(key);
  useEffect(() => {
    if (keyRef.current === key) return;
    keyRef.current = key;
    setReviews({});
  }, [key]);
  // The guided tour takes the view and gives it back: the marks on the user's own previews are set aside with the
  // key they belong to when it starts (below, once the tour exists) and put back when that key returns.
  const reviewsRef = useRef(reviews);
  reviewsRef.current = reviews;
  const tourReviewsRef = useRef<KeptReviews | null>(null);
  const onMark = useCallback((source: string, mark: Mark) => setReviews((r) => setMark(r, source, mark)), []);
  const reviewing = canReview(view, busy);
  const held = useMemo(() => (reviewing ? heldSources(reviews, view.samples) : []), [reviewing, reviews, view.samples]);

  const onAct = useCallback(
    (token: string) => void runJob(api.primary(token, view.action.kind === "approve" ? held : []), null, "primary"),
    [runJob, view.action.kind, held],
  );

  // --- Find and replace, occurrence by occurrence ---------------------------
  // The ticks belong to one preview's list and start as the previewed plan has them (its unticked occurrences);
  // Update the preview sends the whole unticked set, and the one button waits while the ticks differ.
  const [unticks, setUnticks] = useState<Unticks>({});
  const occKey = occurrencesKey(view);
  const occKeyRef = useRef(occKey);
  useEffect(() => {
    if (occKeyRef.current === occKey) return;
    occKeyRef.current = occKey;
    setUnticks(previewedUnticks(view));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [occKey]);
  const onToggleUntick = useCallback((key: string) => setUnticks((u) => toggleUntick(u, key)), []);
  const unticking = canUntick(view, busy);
  const pendingUnticks = unticking && unticksPending(unticks, view);
  const onUpdateOccurrences = useCallback(
    () => void runJob(api.occurrences(untickedKeys(unticks, view.occurrences ?? [])), null, "occurrences"),
    [runJob, unticks, view.occurrences],
  );

  const button = useMemo(
    () => actionButton(view, busy, view.mode !== "safe" && !!draft.trim(), held.length, pendingUnticks),
    [view, busy, draft, held.length, pendingUnticks],
  );
  const items = useMemo(() => transcript(state), [state]);
  const composer = useMemo(() => composerState(view), [view]);

  // --- Attachments ---------------------------------------------------------

  // A view answered while a job stream is attached (an upload that landed after Send) must not replace the
  // streaming state with the pre-job view; the stream's own `done` carries the authoritative one.
  const applyView = useCallback((promise: Promise<View>, onApplied?: () => void) => {
    promise
      .then((next) => {
        if (!busyRef.current) {
          onApplied?.();
          dispatch({ type: "view", view: next });
        }
      })
      .catch((failure: unknown) => setNotice(failureText(failure)));
  }, []);

  const uploadFiles = useCallback(
    async (files: File[], template: boolean) => {
      if (files.length === 0) return;
      setUploading(true);
      try {
        const next = await api.upload(files, template);
        if (!busyRef.current) dispatch({ type: "view", view: next });
      } catch (failure) {
        setNotice(failureText(failure));
      } finally {
        setUploading(false);
      }
    },
    [],
  );

  const onAdd = useCallback(
    async (kind: PickKind) => {
      try {
        const paths = await api.pick(kind);
        if (paths === null) {
          // No native dialog (a browser, the dev page): the browser's own.
          pendingPickRef.current = kind;
          const input = fileInputRef.current;
          if (!input) return;
          input.accept = ACCEPT[kind];
          input.multiple = kind !== "template";
          input.value = "";
          input.click();
          return;
        }
        if (paths.length === 0) return;
        applyView(api.attach(paths, kind === "template"));
      } catch (failure) {
        setNotice(failureText(failure));
      }
    },
    [applyView],
  );

  const onFileInput = useCallback(
    (list: FileList | null) => {
      const files = list ? Array.from(list) : [];
      void uploadFiles(files, pendingPickRef.current === "template");
    },
    [uploadFiles],
  );

  const onRemove = useCallback(
    (attachment: Attachment) => applyView(api.remove(attachment.path, attachment.kind === "template")),
    [applyView],
  );

  // The whole window takes a drop. A counter survives the enter/leave pairs
  // every child element fires while a file crosses it.
  useEffect(() => {
    const hasFiles = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes("Files");
    const onEnter = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      setDragDepth((d) => d + 1);
    };
    const onOver = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      if (e.dataTransfer) e.dataTransfer.dropEffect = busyRef.current ? "none" : "copy";
    };
    const onLeave = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      // Leaving the window altogether (no element under the pointer) ends
      // the drag as far as the ring is concerned, whatever the counter says.
      if (e.relatedTarget === null) setDragDepth(0);
      else setDragDepth((d) => Math.max(0, d - 1));
    };
    const onDrop = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      setDragDepth(0);
      if (busyRef.current) {
        setNotice("Finish or stop the current work before adding documents.");
        return;
      }
      void uploadFiles(Array.from(e.dataTransfer?.files ?? []), false);
    };
    window.addEventListener("dragenter", onEnter);
    window.addEventListener("dragover", onOver);
    window.addEventListener("dragleave", onLeave);
    window.addEventListener("drop", onDrop);
    return () => {
      window.removeEventListener("dragenter", onEnter);
      window.removeEventListener("dragover", onOver);
      window.removeEventListener("dragleave", onLeave);
      window.removeEventListener("drop", onDrop);
    };
  }, [uploadFiles]);

  // --- Conversation, results -----------------------------------------------

  const resetInputs = useCallback(() => {
    setDraft("");
    setSafeForm(EMPTY_SAFE_FORM);
    setPrefill({ text: "", nonce: 0 });
    setReviews({});
  }, []);

  // Declared below, once the tour exists: New conversation ends a running tour first (the conversation comes back,
  // then starts afresh).
  const newConversationRef = useRef<() => void>(() => {});
  const onNewConversation = useCallback(() => newConversationRef.current(), []);

  // Export → Save the copies…: a dismissed dialog changes nothing (the view is fetched again, which is harmless).
  const onExport = useCallback(() => {
    if (busyRef.current) return;
    applyView(api.exportCopies().then((next) => next ?? api.getState()));
  }, [applyView]);

  const onStartOver = useCallback(() => applyView(api.startOver(), resetInputs), [applyView, resetInputs]);
  // The ways back, in every mode: Discard withdraws a preview (Safe: the form keeps what was typed; Default and
  // Obliterate: the request ends) or a conversation's open request; Undo takes the working set back to the copies
  // from before the last run.
  const onDiscard = useCallback(() => {
    if (!busyRef.current) applyView(api.discard());
  }, [applyView]);
  const onUndo = useCallback(() => {
    if (!busyRef.current) applyView(api.undo());
  }, [applyView]);
  const discardWhat = discardButton(view, busy);
  const discard = discardWhat ? onDiscard : undefined;
  // Obliterate opens its warning first, every time; the other modes switch at once.
  const [warning, setWarning] = useState(false);
  const onMode = useCallback((mode: Mode) => {
    if (busyRef.current) return;
    const choice = modeChoice(view.mode, mode);
    if (choice === "warn") setWarning(true);
    else if (choice === "switch") applyView(api.setMode(mode));
  }, [applyView, view.mode]);
  const onObliterate = useCallback(() => {
    setWarning(false);
    if (!busyRef.current) applyView(api.setMode("obliterate", true));
  }, [applyView]);
  const onHandOff = useCallback((paths: string[]) => applyView(api.handOff(paths)), [applyView]);

  // Safe mode's buttons: the same list for the whole session, fetched once.
  const [recipes, setRecipes] = useState<Recipe[]>([]);
  useEffect(() => {
    api.getRecipes().then(setRecipes).catch((failure: unknown) => setNotice(failureText(failure)));
  }, []);
  // The note above the buttons: Hide and What is Safe mode? are kept in settings.json, and the view carries them.
  const onSafeNoteHidden = useCallback(
    (hidden: boolean) => applyView(api.setSafeNoteHidden(hidden).then(() => api.getState())),
    [applyView],
  );
  const onDefaultMode = useCallback(() => onMode("default"), [onMode]);
  const onOpenSettings = useCallback(() => setSettingsOpen(true), []);
  const tourRunning = view.tutorial.active;
  const panel = useMemo(
    () => (
      <SafePanel
        recipes={recipes}
        view={view}
        busy={busy}
        onPreview={onRecipe}
        onDiscard={onDiscard}
        form={safeForm}
        onFormChange={setSafeForm}
        keyReady={view.connection.ready}
        noteHidden={view.safe_note_hidden}
        tourActive={tourRunning}
        onNoteHidden={onSafeNoteHidden}
        onDefaultMode={onDefaultMode}
        onAddKey={onOpenSettings}
      />
    ),
    [recipes, view, busy, onRecipe, onDiscard, safeForm, tourRunning, onSafeNoteHidden, onDefaultMode, onOpenSettings],
  );

  const onPutBack = useCallback((path: string) => applyView(api.putBack(path)), [applyView]);

  // --- The guided tour -------------------------------------------------------
  // The server builds each chapter's state on bundled practice documents and answers with the view; the page shows
  // it like any other. The one page-side preparation is the Safe-mode form a step points at (lib/tour.ts TOUR_FORM).
  const applyTourView = useCallback((next: View) => dispatch({ type: "view", view: next }), []);
  const prepareTourStep = useCallback(
    (step: TourStep) => {
      if (step.prepare !== "safe-form") return;
      const recipe = recipes.find((r) => r.id === TOUR_FORM.recipe);
      if (!recipe) return;
      setSafeForm((form) => {
        const opened = openForm(form, recipe, viewRef.current);
        const slots = { ...((opened.inputs.slots as Record<string, string> | undefined) ?? {}), [TOUR_FORM.slot]: TOUR_FORM.value };
        return { ...opened, inputs: { ...opened.inputs, slots } };
      });
    },
    [recipes],
  );
  const tour = useTour({ applyView: applyTourView, tutorial: view.tutorial, loaded: state.loaded, prepare: prepareTourStep });
  const tourActive = tour.active;
  useEffect(() => {
    if (tourActive) {
      // taken as the tour begins, before any practice view arrives
      tourReviewsRef.current = { key: keyRef.current, reviews: reviewsRef.current };
      return;
    }
    const kept = keptAcrossTour(tourReviewsRef.current, keyRef.current);
    tourReviewsRef.current = null;
    if (kept) setReviews(kept);
  }, [tourActive]);
  const tourFinished = state.loaded ? view.tutorial.finished_version >= TOUR_COMPLETION_VERSION : null;
  const unsavedCopies = view.export?.unsaved ?? 0;
  newConversationRef.current = () => {
    if (busyRef.current) return;
    if (tourActive) {
      void tour.abort().then((returned) => {
        if (returned) applyView(api.newConversation(), resetInputs);
      });
      return;
    }
    // Copies never saved with Export outlive a new conversation only until the window closes: say so first.
    if (
      unsavedCopies > 0 &&
      !window.confirm(
        `${unsavedCopies === 1 ? "1 file made in this window (an edited copy or a clean PDF) has" : `${unsavedCopies} files made in this window (edited copies or clean PDFs) have`} not been saved. A new conversation leaves ${unsavedCopies === 1 ? "it" : "them"} behind in the window's scratch folder, which goes when the window closes.\n\nOK starts the new conversation anyway. Cancel goes back, so you can save ${unsavedCopies === 1 ? "it" : "them"} with Export first.`,
      )
    ) {
      return;
    }
    applyView(api.newConversation(), resetInputs);
  };
  const onStartTourAt = useCallback(
    (chapter: string) => {
      setHelpTopic(null);
      tour.startAtChapter(chapter);
    },
    [tour],
  );

  const onOpen = useCallback((path: string) => {
    api.openPath(path).catch((failure: unknown) => setNotice(failureText(failure)));
  }, []);

  const onPrefill = useCallback((text: string) => {
    setDraft(text);
    setPrefill((p) => ({ text, nonce: p.nonce + 1 }));
  }, []);

  // Stable handlers: the header, the workbench and the composer are memoized and skip the renders a streamed
  // delta would otherwise cause, as long as nothing handed to them is a new function each time.
  const onAddKind = useCallback((kind: PickKind) => void onAdd(kind), [onAdd]);
  // Add a folder of documents: the server opens the folder dialog and attaches the folder's own .docx files; a
  // dismissed dialog changes nothing, and a window without a dialog says so.
  const onAddFolder = useCallback(() => {
    applyView(api.attachFolder().then((next) => next ?? api.getState()));
  }, [applyView]);
  const onZoom = useCallback((src: string, title: string) => setZoom({ src, title }), []);
  const actionToken = view.action?.token;
  const onContinue = useCallback(() => {
    if (actionToken) onAct(actionToken);
  }, [actionToken, onAct]);

  // --- About: logs, updates -------------------------------------------------

  const onOpenLogs = useCallback(() => {
    api.openLogs().catch((failure: unknown) => setNotice(failureText(failure)));
  }, []);

  // About's Save a diagnostic bundle: the answer stays on About until the window closes.
  const [bundleNote, setBundleNote] = useState("");
  const onSaveBundle = useCallback(() => {
    setBundleNote("Saving the diagnostic bundle…");
    api
      .saveDiagnosticBundle()
      .then((saved) => setBundleNote(`Saved ${saved.name} (${saved.files} files); its folder is open.`))
      .catch((failure: unknown) => setBundleNote(failureText(failure)));
  }, []);

  /** Follow the update block every 2 s for up to `tries` polls, or until
   *  `settled` says so. `done` runs once when it stops, however it stops.
   *  Returns the stop. */
  const pollUpdate = useCallback((tries: number, settled: (update: Update) => boolean, done?: () => void) => {
    let left = tries;
    let stopped = false;
    const stop = () => {
      if (stopped) return;
      stopped = true;
      window.clearInterval(timer);
      done?.();
    };
    const timer = window.setInterval(() => {
      left -= 1;
      api
        .getUpdate()
        .then((update) => {
          dispatch({ type: "update", update });
          if (settled(update)) stop();
        })
        .catch(stop);
      if (left <= 0) stop();
    }, 2000);
    return stop;
  }, []);

  // About's Check for updates: the page says "Checking…" until the server
  // writes this check's answer or finds an update (lib/help.checkSettled).
  const onCheckUpdates = useCallback(() => {
    setDismissedUpdate("");
    setUpdateAsked(true);
    setCheckingUpdate(true);
    api
      .checkUpdate()
      .then((update) => {
        dispatch({ type: "update", update });
        if (checkSettled(update)) setCheckingUpdate(false);
        else pollUpdate(15, checkSettled, () => setCheckingUpdate(false));
      })
      .catch((failure: unknown) => {
        setCheckingUpdate(false);
        setNotice(failureText(failure));
      });
  }, [pollUpdate]);

  // The download is followed by the effect below, which starts polling the
  // moment the update block says "downloading" — from this click or from a
  // view that found the download already running.
  const onDownloadUpdate = useCallback(() => {
    api
      .downloadUpdate()
      .then((update) => dispatch({ type: "update", update }))
      .catch((failure: unknown) => setNotice(failureText(failure)));
  }, []);

  const onInstallUpdate = useCallback(() => {
    api.installUpdate().catch((failure: unknown) => setNotice(failureText(failure)));
  }, []);

  const downloading = view.update?.state === "downloading";
  useEffect(() => {
    if (!downloading) return;
    return pollUpdate(600, (update) => update.state !== "downloading");
  }, [downloading, pollUpdate]);

  const updateShown =
    !!view.update &&
    view.update.state !== "none" &&
    !(dismissedUpdate && dismissedUpdate === `${view.update.state}:${view.update.version}`);

  return (
    <div className={`flex h-full flex-col ${dragDepth > 0 ? "drop-ring" : ""}`}>
      <Header
        view={view}
        busy={busy}
        updatePill={updatePill(view.update, updateShown)}
        onDownloadUpdate={onDownloadUpdate}
        onExport={onExport}
        onNewConversation={onNewConversation}
        onOpenSettings={onOpenSettings}
        onOpenHelp={setHelpTopic}
        onMode={onMode}
        onStop={onStop}
        tourActive={tourActive}
        onStartTour={tour.start}
      />
      {updateShown && (
        <UpdateBanner
          update={view.update}
          busy={busy}
          onDownload={onDownloadUpdate}
          onInstall={onInstallUpdate}
          onDismiss={() => setDismissedUpdate(`${view.update.state}:${view.update.version}`)}
        />
      )}
      {notice && (
        <div className="flex items-center gap-3 border-b border-warn/40 bg-warn/10 px-5 py-1.5 text-xs text-ink" role="status">
          <span className="min-w-0 flex-1">{notice}</span>
          <button
            type="button"
            onClick={() => setNotice(null)}
            aria-label="Dismiss"
            className="rounded px-1.5 text-ink-dim transition-colors hover:bg-raised hover:text-ink"
          >
            ✕
          </button>
        </div>
      )}
      <main className="flex min-h-0 flex-1">
        <Chat
          items={items}
          busy={busy}
          canSend={composer.canSend}
          sendReason={composer.reason}
          button={button}
          held={held.length}
          aside={setAsideCount(view.samples)}
          onSend={onSend}
          onChoose={onChoose}
          onAct={onAct}
          onContinue={onContinue}
          onStop={onStop}
          prefill={prefill}
          onPrefill={onPrefill}
          onDraftChange={setDraft}
          draft={draft}
          mode={view.mode}
          panel={panel}
          onMode={onMode}
          discard={discardWhat}
          onDiscard={discard}
          tourActive={tourActive}
          tourFinished={tourFinished}
          onStartTour={tour.start}
        />
        <Workbench
          view={view}
          busy={busy}
          uploading={uploading}
          reviews={reviews}
          canReview={reviewing}
          onMark={onMark}
          unticks={unticks}
          canUntick={unticking}
          unticksPending={pendingUnticks}
          onToggleUntick={onToggleUntick}
          onUpdateOccurrences={onUpdateOccurrences}
          onAdd={onAddKind}
          onAddFolder={onAddFolder}
          onRemove={onRemove}
          onOpen={onOpen}
          onStartOver={onStartOver}
          onUndo={onUndo}
          onPutBack={onPutBack}
          onZoom={onZoom}
          onHandOff={onHandOff}
        />
      </main>
      <input
        ref={fileInputRef}
        type="file"
        className="hidden"
        aria-hidden
        tabIndex={-1}
        onChange={(e) => onFileInput(e.target.files)}
      />
      {settingsOpen && (
        <SettingsModal
          onClose={() => setSettingsOpen(false)}
          onSaved={() => void refresh()}
        />
      )}
      <HelpModal
        topic={helpTopic}
        onClose={() => setHelpTopic(null)}
        onNavigate={setHelpTopic}
        version={view.version}
        busy={busy}
        update={view.update}
        updateAsked={updateAsked}
        checkingUpdate={checkingUpdate}
        onCheckUpdate={onCheckUpdates}
        onDownloadUpdate={onDownloadUpdate}
        onInstallUpdate={onInstallUpdate}
        onOpenLogs={onOpenLogs}
        onSaveBundle={onSaveBundle}
        bundleNote={bundleNote}
        onStartTourAt={onStartTourAt}
      />
      <TourOverlay tour={tour} view={view} busy={busy} />
      {zoom && <ZoomModal src={zoom.src} title={zoom.title} onClose={() => setZoom(null)} />}
      {warning && (
        <ObliterateWarning
          keyReady={!!view.connection?.ready}
          onCancel={() => setWarning(false)}
          onConfirm={onObliterate}
        />
      )}
    </div>
  );
}
