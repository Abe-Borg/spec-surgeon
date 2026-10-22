// Adapted from Build-a-Spec's frontend/src/main.tsx (copy-based reuse).
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App";
import ErrorBoundary from "./components/ErrorBoundary";
import { configureApi, postDiagnostics } from "./lib/api";
import { installPageDiagnostics } from "./lib/diagnostics";

declare global {
  interface Window {
    /** Injected by the native shell: the launch token and the app version
     *  (docs/WEB_API.md). The dev page carries them in the query string. */
    SPEC_SURGEON?: { token?: string; version?: string; api?: string };
  }
}

// The token and API base are read once, before anything renders; every
// /api call carries them from here on (lib/api.ts).
const connection = configureApi(window.SPEC_SURGEON, window.location.search);

// From here on the page tells the app's log what goes wrong in it, and beats
// so the app can tell a frozen window (lib/diagnostics.ts).
installPageDiagnostics(window, { send: postDiagnostics, version: connection.shellVersion });

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
);
