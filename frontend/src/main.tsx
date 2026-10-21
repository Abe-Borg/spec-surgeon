// Adapted from Build-a-Spec's frontend/src/main.tsx (copy-based reuse).
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App";
import { configureApi } from "./lib/api";

declare global {
  interface Window {
    /** Injected by the native shell: the launch token and the app version
     *  (docs/WEB_API.md). The dev page carries them in the query string. */
    SPEC_SURGEON?: { token?: string; version?: string; api?: string };
  }
}

// The token and API base are read once, before anything renders; every
// /api call carries them from here on (lib/api.ts).
configureApi(window.SPEC_SURGEON, window.location.search);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
