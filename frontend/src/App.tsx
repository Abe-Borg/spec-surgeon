// Adapted from Build-a-Spec's frontend/src/App.tsx (copy-based reuse): the
// layout and the one place the job stream's events dispatch.
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import type { Attachment, Choice, JobEvent, View } from "./types";
import * as api from "./lib/api";
import { actionButton } from "./lib/action";
import { composerState, transcript } from "./lib/chat";
import { INITIAL_STATE, reduce } from "./lib/view";
import { ACCEPT } from "./lib/workbench";
import Header from "./components/Header";
import Chat from "./components/Chat";
import Workbench from "./components/Workbench";
import UpdateBanner from "./components/UpdateBanner";
import SettingsModal from "./components/SettingsModal";
import AboutModal from "./components/AboutModal";
import ZoomModal from "./components/ZoomModal";

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
  const [draft, setDraft] = useState(false);
  const [prefill, setPrefill] = useState({ text: "", nonce: 0 });
  const [workbenchOpen, setWorkbenchOpen] = useState(true);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [aboutOpen, setAboutOpen] = useState(false);
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
    async (stream: AsyncGenerator<JobEvent>, userText: string | null) => {
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
        if (!terminal) dispatch({ type: "job_ended" });
      } catch (failure) {
        dispatch({ type: "job_failed", message: failureText(failure) });
      } finally {
        busyRef.current = false;
        if (resend) setPrefill((p) => ({ text: resend, nonce: p.nonce + 1 }));
        // The view that ends a job is authoritative; the state route is the
        // same view, fetched once more in case the stream was cut short.
        void refresh();
      }
    },
    [refresh],
  );

  const onSend = useCallback((text: string, chip = false) => void runJob(api.send(text, chip), text), [runJob]);
  const onChoose = useCallback(
    (choice: Choice) => void runJob(api.choose(choice.id), choice.label),
    [runJob],
  );
  const onAct = useCallback((token: string) => void runJob(api.primary(token), null), [runJob]);
  const onStop = useCallback(() => {
    api.stop().catch((failure: unknown) => setNotice(failureText(failure)));
  }, []);

  const button = useMemo(() => actionButton(view, busy, draft), [view, busy, draft]);
  const items = useMemo(() => transcript(state), [state]);
  const composer = useMemo(() => composerState(view), [view]);
  const paused = items.some((item) => item.kind === "pause");

  // --- Attachments ---------------------------------------------------------

  // A view answered while a job stream is attached (an upload that landed after Send) must not replace the
  // streaming state with the pre-job view; the stream's own `done` carries the authoritative one.
  const applyView = useCallback((promise: Promise<View>) => {
    promise
      .then((next) => {
        if (!busyRef.current) dispatch({ type: "view", view: next });
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

  const onNewConversation = useCallback(() => {
    if (busyRef.current) return;
    applyView(api.newConversation());
  }, [applyView]);

  const onStartOver = useCallback(() => applyView(api.startOver()), [applyView]);

  const onOpen = useCallback((path: string) => {
    api.openPath(path).catch((failure: unknown) => setNotice(failureText(failure)));
  }, []);

  const onPrefill = useCallback((text: string) => setPrefill((p) => ({ text, nonce: p.nonce + 1 })), []);

  // --- Help: logs, updates, about ------------------------------------------

  const onOpenLogs = useCallback(() => {
    api.openLogs().catch((failure: unknown) => setNotice(failureText(failure)));
  }, []);

  /** Follow the update block every 2 s for up to `tries` polls, or until it
   *  settles (anything but "none" or "downloading"). Returns the stop. */
  const pollUpdate = useCallback((tries: number) => {
    let left = tries;
    const timer = window.setInterval(() => {
      left -= 1;
      api
        .getUpdate()
        .then((update) => {
          dispatch({ type: "update", update });
          if (update.state !== "downloading" && (update.state !== "none" || left <= 0)) {
            window.clearInterval(timer);
          }
        })
        .catch(() => window.clearInterval(timer));
      if (left <= 0) window.clearInterval(timer);
    }, 2000);
    return () => window.clearInterval(timer);
  }, []);

  const onCheckUpdates = useCallback(() => {
    setDismissedUpdate("");
    api
      .checkUpdate()
      .then((update) => {
        dispatch({ type: "update", update });
        setNotice("Checking for updates…");
        pollUpdate(15);
      })
      .catch((failure: unknown) => setNotice(failureText(failure)));
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
    return pollUpdate(600);
  }, [downloading, pollUpdate]);

  const updateShown =
    view.update &&
    view.update.state !== "none" &&
    !(dismissedUpdate && dismissedUpdate === `${view.update.state}:${view.update.version}`);

  return (
    <div className={`flex h-full flex-col ${dragDepth > 0 ? "drop-ring" : ""}`}>
      <Header
        view={view}
        busy={busy}
        workbenchOpen={workbenchOpen}
        onToggleWorkbench={() => setWorkbenchOpen((o) => !o)}
        onNewConversation={onNewConversation}
        onOpenSettings={() => setSettingsOpen(true)}
        onOpenAbout={() => setAboutOpen(true)}
        onOpenLogs={onOpenLogs}
        onCheckUpdates={onCheckUpdates}
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
          onSend={onSend}
          onChoose={onChoose}
          onContinue={() => {
            if (view.action?.token) onAct(view.action.token);
          }}
          onStop={onStop}
          prefill={prefill}
          onPrefill={onPrefill}
          onDraftChange={setDraft}
        />
        {workbenchOpen && (
          <Workbench
            view={view}
            busy={busy}
            uploading={uploading}
            button={button}
            paused={paused}
            onAdd={(kind) => void onAdd(kind)}
            onRemove={onRemove}
            onAct={onAct}
            onStop={onStop}
            onOpen={onOpen}
            onStartOver={onStartOver}
            onZoom={(src, title) => setZoom({ src, title })}
          />
        )}
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
      {aboutOpen && <AboutModal onClose={() => setAboutOpen(false)} fallbackVersion={view.version} />}
      {zoom && <ZoomModal src={zoom.src} title={zoom.title} onClose={() => setZoom(null)} />}
    </div>
  );
}
