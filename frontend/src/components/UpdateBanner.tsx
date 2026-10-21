import type { Update } from "../types";
import { primaryBtn, quietBtn } from "./ModalShell";
import Tip from "./Tip";

interface Props {
  update: Update;
  busy: boolean;
  onDownload: () => void;
  onInstall: () => void;
  onDismiss: () => void;
}

/**
 * One strip above the panes, driven by the view's `update` block: available
 * (Download), downloading (progress), ready (Restart to update), installing
 * (the app is closing), updated (this start follows an update), failed (the
 * install did not finish), error. Nothing when there is nothing to say.
 */
export default function UpdateBanner({ update, busy, onDownload, onInstall, onDismiss }: Props) {
  if (!update || update.state === "none") return null;
  const tone =
    update.state === "error" || update.state === "failed"
      ? "border-err/40 bg-err/10"
      : update.state === "ready" || update.state === "updated"
        ? "border-ok/40 bg-ok/10"
        : "border-accent/40 bg-accent/10";
  return (
    <div className={`flex items-center gap-4 border-b px-5 py-2 text-sm ${tone}`} role="status">
      <div className="min-w-0 flex-1">
        {update.state === "available" && (
          <p className="text-ink">
            Spec Surgeon {update.version} is available.
            {update.notes && (
              <span className="ml-2 text-ink-dim">{update.notes.split("\n")[0]}</span>
            )}
          </p>
        )}
        {update.state === "downloading" && (
          <div>
            <p className="text-ink">
              Downloading Spec Surgeon {update.version}…{" "}
              <span className="text-ink-dim tabular-nums">{Math.round(update.progress ?? 0)}%</span>
            </p>
            <div className="update-progress mt-1.5 max-w-sm">
              <span style={{ width: `${Math.max(0, Math.min(100, update.progress ?? 0))}%` }} />
            </div>
          </div>
        )}
        {update.state === "ready" && (
          <p className="text-ink">
            Spec Surgeon {update.version} is ready to install.
            <span className="ml-2 text-ink-dim">
              {update.text || "The app closes to install it and reopens when it is done."}
            </span>
          </p>
        )}
        {update.state === "installing" && (
          <p className="text-ink">
            Closing to install Spec Surgeon {update.version}…
            <span className="ml-2 text-ink-dim">{update.text || "It reopens when the update is done."}</span>
          </p>
        )}
        {update.state === "updated" && (
          <p className="text-ink">
            Spec Surgeon was updated to {update.version}.
            {update.text && <span className="ml-2 text-ink-dim">{update.text}</span>}
          </p>
        )}
        {update.state === "failed" && (
          <p className="text-err">
            The update to {update.version} was not installed.
            {update.text && <span className="ml-2 text-err/80">{update.text}</span>}
            {update.install_log && <span className="ml-2 text-err/80">Log: {update.install_log}</span>}
          </p>
        )}
        {update.state === "error" && (
          <p className="text-err">
            The update was not installed.
            {update.error && <span className="ml-2 text-err/80">{update.error}</span>}
          </p>
        )}
      </div>
      {update.state === "available" && (
        <button type="button" className={primaryBtn} onClick={onDownload}>
          Download
        </button>
      )}
      {update.state === "ready" && (
        <Tip tip={busy ? "Finish or stop the current work first — the installer closes the app" : "Close Spec Surgeon and install the update"}>
          <button type="button" className={primaryBtn} disabled={busy} onClick={onInstall}>
            Restart to update
          </button>
        </Tip>
      )}
      {(update.state === "available" || update.state === "error" || update.state === "updated" || update.state === "failed") && (
        <button type="button" className={quietBtn} onClick={onDismiss} title="Hide this until the next check">
          {update.state === "updated" || update.state === "failed" ? "OK" : "Not now"}
        </button>
      )}
    </div>
  );
}
