/**
 * Where the page finds its server and its launch token (docs/WEB_API.md).
 *
 * The native shell injects `window.SPEC_SURGEON = {token, version, api?}`;
 * the dev page carries `?token=…&api=http://127.0.0.1:<port>` instead. The
 * shell's values win over the URL's, and the API base falls back to the
 * page's own origin (same-origin requests, Vite's proxy in development).
 *
 * Pure: the lookup takes the injected object and the query string as values
 * so `node --test` can run it without a DOM.
 */

export interface ShellInjection {
  token?: unknown;
  version?: unknown;
  api?: unknown;
}

export interface ConnectionSetup {
  /** Sent as `X-Spec-Surgeon-Token` on every /api call; "" when none was given. */
  token: string;
  /** Origin the /api routes live on, without a trailing slash; "" = same origin. */
  apiBase: string;
  /** The shell's version string when it injected one, else "". */
  shellVersion: string;
}

const TOKEN_HEADER = "X-Spec-Surgeon-Token";

function str(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

/** Strip trailing slashes so `${apiBase}/api/state` never doubles one. */
export function normalizeApiBase(base: string): string {
  const trimmed = base.trim();
  if (!trimmed) return "";
  return trimmed.replace(/\/+$/, "");
}

export function resolveConnection(
  injected: ShellInjection | undefined | null,
  search: string,
): ConnectionSetup {
  const params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  const token = str(injected?.token) || str(params.get("token"));
  const apiBase = normalizeApiBase(str(injected?.api) || str(params.get("api")));
  return { token, apiBase, shellVersion: str(injected?.version) };
}

/** `/api/state` → `http://127.0.0.1:8765/api/state` (or itself when same-origin). */
export function apiUrl(setup: Pick<ConnectionSetup, "apiBase">, path: string): string {
  const route = path.startsWith("/") ? path : `/${path}`;
  return `${setup.apiBase}${route}`;
}

/** The headers every /api request carries; `extra` adds a content type and the like. */
export function apiHeaders(
  setup: Pick<ConnectionSetup, "token">,
  extra?: Record<string, string>,
): Record<string, string> {
  const headers: Record<string, string> = { ...(extra ?? {}) };
  if (setup.token) headers[TOKEN_HEADER] = setup.token;
  return headers;
}

/**
 * An image URL the view hands out (`/api/image?path=…`) rebased onto the API
 * origin with the token appended as `token=` — an <img> cannot send a header.
 * A URL that already carries a token keeps it.
 */
export function imageSrc(setup: ConnectionSetup, url: string): string {
  if (!url) return "";
  if (/^(https?:)?\/\//.test(url) || url.startsWith("data:")) return url;
  const [path, query = ""] = url.split("?", 2);
  const params = new URLSearchParams(query);
  if (setup.token && !params.has("token")) params.set("token", setup.token);
  const qs = params.toString();
  return apiUrl(setup, qs ? `${path}?${qs}` : path);
}

export { TOKEN_HEADER };
