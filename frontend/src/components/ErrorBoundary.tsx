import { Component, type ErrorInfo, type ReactNode } from "react";
import * as api from "../lib/api";
import { describeFailure, reportPage } from "../lib/diagnostics";
import { primaryBtn, quietBtn } from "./ModalShell";

interface State {
  message: string | null;
  note: string;
}

/**
 * The page's last line of defence (docs/CONVERSATIONAL_DESKTOP.md, "When the
 * window has a problem"): an error while drawing would otherwise empty the
 * window and leave nothing to click. Instead the error goes to the app's log
 * with the parts of the window it happened in (lib/diagnostics.ts), and this
 * screen offers to reload the page. The app behind the page keeps running, so
 * the conversation and any work in progress carry on through a reload.
 */
export default class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { message: null, note: "" };

  static getDerivedStateFromError(error: unknown): Partial<State> {
    return { message: describeFailure(error).message };
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    const { message, stack } = describeFailure(error);
    reportPage({ kind: "render", message, stack, component_stack: info.componentStack ?? "" }, true);
  }

  private saveBundle = () => {
    this.setState({ note: "Saving…" });
    api
      .saveDiagnosticBundle()
      .then((saved) => this.setState({ note: `Saved ${saved.name}; its folder is open.` }))
      .catch((failure: unknown) => this.setState({ note: describeFailure(failure).message }));
  };

  private openLogs = () => {
    api.openLogs().catch((failure: unknown) => this.setState({ note: describeFailure(failure).message }));
  };

  render() {
    if (this.state.message === null) return this.props.children;
    return (
      <div className="flex h-full items-center justify-center bg-bg p-8 text-ink" role="alert">
        <div className="w-full max-w-xl rounded-2xl border border-warn/40 bg-surface px-6 py-5 shadow-2xl">
          <p className="font-[family-name:var(--font-display)] text-lg font-semibold">
            The window ran into a problem and stopped drawing
          </p>
          <p className="mt-2 text-sm leading-relaxed text-ink-dim">
            The app behind it is still running: your conversation is saved and work in progress carries on.
            Reload the window to pick up where you were. What went wrong is in the diagnostic log.
          </p>
          <p className="mt-3 break-words rounded-lg bg-raised/60 px-3 py-2 font-mono text-xs text-ink-dim">
            {this.state.message}
          </p>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <button type="button" className={primaryBtn} onClick={() => window.location.reload()}>
              Reload the window
            </button>
            <button type="button" className={quietBtn} onClick={this.saveBundle}>
              Save a diagnostic bundle
            </button>
            <button type="button" className={quietBtn} onClick={this.openLogs}>
              Open diagnostic logs
            </button>
          </div>
          {this.state.note && (
            <p className="mt-3 text-xs text-ink-dim" role="status">
              {this.state.note}
            </p>
          )}
        </div>
      </div>
    );
  }
}
