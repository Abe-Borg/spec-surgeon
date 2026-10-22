/**
 * The window's colour schemes. Each one is a set of colour tokens in
 * index.css: Warm Clay is the @theme block itself, every other scheme a block
 * under `<html data-theme="…">`. The server keeps the choice in settings.json
 * (`theme`, one of settings.THEMES) and the shell opens the page with
 * `?theme=<id>`, which index.html's inline script applies before the first
 * paint; Settings changes it at once.
 *
 * Pure but for `applyTheme`: `node --test` runs the rest without a DOM.
 */

export interface Theme {
  id: string;
  name: string;
  /** One line for the picker. */
  blurb: string;
  /** The scheme's own colours, for its card in the picker: chrome, paper, accent. */
  swatch: { bg: string; paper: string; accent: string };
}

export const THEMES: readonly Theme[] = [
  {
    id: "clay",
    name: "Warm Clay",
    blurb: "Warm charcoal, cream paper, terracotta.",
    swatch: { bg: "#262624", paper: "#faf9f5", accent: "#d97757" },
  },
  {
    id: "blueprint",
    name: "Blueprint",
    blurb: "Deep navy, cool white paper, drafting blue.",
    swatch: { bg: "#121a26", paper: "#f8fafc", accent: "#2e8aee" },
  },
  {
    id: "graphite",
    name: "Graphite & Teal",
    blurb: "Neutral charcoal, white paper, teal.",
    swatch: { bg: "#1b1d1f", paper: "#fbfbfa", accent: "#269c9b" },
  },
  {
    id: "carbon",
    name: "Carbon & Hi-Vis",
    blurb: "True black, pure white paper, safety orange.",
    swatch: { bg: "#0b0b0c", paper: "#ffffff", accent: "#f55b00" },
  },
  {
    id: "linen",
    name: "Linen",
    blurb: "Light and warm: linen, off-white paper, terracotta.",
    swatch: { bg: "#f5f1ea", paper: "#fffdf9", accent: "#a64b29" },
  },
];

export const DEFAULT_THEME = THEMES[0].id;

/** A known scheme's id, else the default (a hand-edited URL or an older server). */
export function resolveTheme(value: unknown): string {
  return typeof value === "string" && THEMES.some((t) => t.id === value) ? value : DEFAULT_THEME;
}

/** The `theme` the shell put in the page's query string. */
export function themeFromSearch(search: string): string {
  const params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  return resolveTheme(params.get("theme"));
}

/** `search` with its `theme` replaced, so a reload keeps the scheme just chosen. */
export function searchWithTheme(search: string, id: string): string {
  const params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  params.set("theme", id);
  return `?${params.toString()}`;
}

/** Recolour the window: `data-theme` on <html>, and the address a reload opens. */
export function applyTheme(id: string): void {
  document.documentElement.dataset.theme = resolveTheme(id);
  const search = searchWithTheme(window.location.search, resolveTheme(id));
  window.history.replaceState(window.history.state, "", `${window.location.pathname}${search}${window.location.hash}`);
}
