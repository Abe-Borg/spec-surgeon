// Adapted from Build-a-Spec's frontend/src/App.tsx (copy-based reuse): the
// layout and the one place the job stream's events dispatch.
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import type { Attachment, Choice, JobEvent, Mode, Recipe, RecipeInputs, Update, View } from "./types";
import * as api from "./lib/api";
import { actionButton, canDiscard } from "./lib/action";
import { composerState, transcript } from "./lib/chat";
import { canReview, heldSources, reviewKey, setMark, type Mark, type Reviews } from "./lib/review";
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
import { EMPTY_SAFE_FORM, type SafeFormState } from "./lib/recipes";
import ObliterateWarning from "./components/ObliterateWarning";
import { modeChoice } from "./lib/modes";

type PickKind = "documents" | "template" | "references";

function failureText(failure: unknown): string {
  return failure instanceof Error ? failure.message : String(failure);
}

export default function App() {
  const [state, dispatch] = useReducer(reduce, INITIAL_STATE);
  const { view } = state;
  const busy = view.busy;
  const busyRef = useRef(false);

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
  const onMark = useCallback((source: string, mark: Mark) => setReviews((r) => setMark(r, source, mark)), []);
  const reviewing = canReview(view, busy);
  const held = useMemo(() => (reviewing ? heldSources(reviews, view.samples) : []), [reviewing, reviews, view.samples]);

  const onAct = useCallback(
    (token: string) => void runJob(api.primary(token, view.action.kind === "approve" ? held : []), null, "primary"),
    [runJob, view.action.kind, held],
  );

  const button = useMemo(
    () => actionButton(view, busy, view.mode !== "safe" && !!draft.trim(), held.length),
    [view, busy, draft, held.length],
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

  const onNewConversation = useCallback(() => {
    if (busyRef.current) return;
    applyView(api.newConversation(), resetInputs);
  }, [applyView, resetInputs]);

  const onStartOver = useCallback(() => applyView(api.startOver(), resetInputs), [applyView, resetInputs]);
  // Safe mode's ways back: Discard withdraws a button's preview (the form keeps what was typed); Undo takes the
  // working set back to the copies from before the last button.
  const onDiscard = useCallback(() => {
    if (!busyRef.current) applyView(api.discard());
  }, [applyView]);
  const onUndo = useCallback(() => {
    if (!busyRef.current) applyView(api.undo());
  }, [applyView]);
  const discard = canDiscard(view, busy) ? onDiscard : undefined;
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
      />
    ),
    [recipes, view, busy, onRecipe, onDiscard, safeForm],
  );

  const onPutBack = useCallback((path: string) => applyView(api.putBack(path)), [applyView]);

  const onOpen = useCallback((path: string) => {
    api.openPath(path).catch((failure: unknown) => setNotice(failureText(failure)));
  }, []);

  const onPrefill = useCallback((text: string) => {
    setDraft(text);
    setPrefill((p) => ({ text, nonce: p.nonce + 1 }));
  }, []);

  // Stable handlers: the header, the workbench and the composer are memoized and skip the renders a streamed
  // delta would otherwise cause, as long as nothing handed to them is a new function each time.
  const onOpenSettings = useCallback(() => setSettingsOpen(true), []);
  const onAddKind = useCallback((kind: PickKind) => void onAdd(kind), [onAdd]);
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
        onNewConversation={onNewConversation}
        onOpenSettings={onOpenSettings}
        onOpenHelp={setHelpTopic}
        onMode={onMode}
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
          onDiscard={discard}
        />
        <Workbench
          view={view}
          busy={busy}
          uploading={uploading}
          reviews={reviews}
          canReview={reviewing}
          onMark={onMark}
          onAdd={onAddKind}
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
          stepsPerPause={view.budget?.steps_per_pause ?? 48}
          onClose={() => setSettingsOpen(false)}
          onSaved={() => void refresh()}
        />
      )}
      <HelpModal
        topic={helpTopic}
        onClose={() => setHelpTopic(null)}
        onNavigate={setHelpTopic}
        version={view.version}
        stepsPerPause={view.budget?.steps_per_pause ?? 48}
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
      />
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
