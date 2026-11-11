# The window's web page

Spec Surgeon's desktop window is this page (React 18, TypeScript, Vite, Tailwind v4), built once into
`src/spec_surgeon/web/static/` and hosted by the local server described in `docs/WEB_API.md`. The visual
system is Build-a-Spec's, copied and adapted (never imported across repositories): the chrome around paper
content cards, Georgia for display text, the system sans for everything else.

## Colour schemes

Every colour is a token in `src/index.css`. `@theme` holds Warm Clay, the default; Blueprint, Graphite & Teal,
Carbon & Hi-Vis and Linen (light) each redefine every token under `<html data-theme="…">`. `src/lib/theme.ts` lists
them for Settings → Appearance, which recolours the window at once and saves the choice (`POST /api/settings
{"theme"}`); the shell opens the page with `?theme=<id>` and `index.html`'s inline script applies it before the
first paint. A component never names a colour of its own: `tests/theme.test.ts` fails on a colour literal in
`src/components/` or `App.tsx`, on a scheme missing a token, and on a picker swatch that differs from its
scheme. To add a scheme: a block in `index.css`, an entry in `theme.ts`, the id in `settings.THEMES` and its
background in `web/shell.BACKGROUNDS` (`tests/test_settings.py` checks the four agree).

The page never applies edits or derives state: every `done`/`error` frame and `GET /api/state` carry the
whole view, and the page renders it (`src/lib/view.ts`). Streamed frames are presentation only.

## What is where

| Path | What |
| --- | --- |
| `src/lib/connection.ts` | the token and API base (`window.SPEC_SURGEON`, else `?token=` and `?api=`) |
| `src/lib/sse.ts` | the `data: <json>` frame parser and the stream reader |
| `src/lib/view.ts` | the reducer over the view and the live reply bubble |
| `src/lib/chat.ts`, `action.ts`, `workbench.ts`, `review.ts`, `help.ts` | the pure rules the components render (the review marks and `hold` live in `review.ts`; `help.ts`: the top bar's topics and context line, About's update line) |
| `src/lib/useFit.ts` | how far a one-line bar gives way to fit its window, measured on the page after every change of its words or width; the top bar's steps are `GIVE_WAY` in `Header.tsx` |
| `src/lib/reveal.ts`, `rehypeReveal.ts`, `useReveal.ts` | how a reply's words come onto the page: the reading pace, a finished reply carrying on from the words already shown, the blocks a reply renders as, the pieces that fade in and the caret; `useReveal` runs it frame by frame |
| `src/components/MessageBubble.tsx`, `RevealMarkdown.tsx`, `Collapse.tsx` | one bubble per reply from its first status to the finished message (the working line, the log, the words); a reply's markdown block by block; the folds that open and close by height |
| `src/lib/api.ts` | every route of the contract |
| `src/lib/theme.ts` | the colour schemes Settings offers, and the one the page opens in |
| `src/components/` | the header (the top bar), the chat, the workbench, the modals; `HelpModal.tsx` is the in-app guide and About |
| `mock/server.mjs` | a Node-only mock of the whole contract |
| `tools/screenshots.mjs` | Playwright screenshots against the mock |
| `tools/flows.mjs` | Playwright walk through the other paths (a failed job, a pause, Stop, a drop, Settings and its colour schemes, the top bar's help topics, About and its update check, the update banner, the review marks and the to-do list, Safe mode's buttons, and Obliterate mode with its own tools: the hands-on part browser, Word actions, Try to rescue and Export anyway) |
| `tools/browser.mjs` | shared by both: the mock's launch and the Chromium lookup |
| `tests/*.test.ts` | `node --test` over the pure rules |

## Development

Install once (Node 22 or newer; the tests depend on its TypeScript type stripping):

```
cd frontend
npm install
```

Start the mock server in one terminal. It answers every route with canned data and scripted jobs; the
job a request runs is chosen by its text: `question` asks one thing with choices and chips (in two model calls, as
the real server streams them: the first says what it will do and reads, the second writes the reply), `fail` ends in an
error, `pause` ends in a budget pause, anything else plans and previews (Create counted as the server counts it,
`action.copies` and `action.unpreviewed`), and the primary action creates copies (the previews marked with the red X are sent as `hold` and land on the to-do list; the reply "Work on the
documents I set aside" begins the pass over them).

```
npm run mock
```

Start Vite in another terminal and open the page it prints (it proxies `/api` to the mock on 8765):

```
npm run dev
```

The built page can also be opened straight from the mock server, which serves `src/spec_surgeon/web/static/`:
`http://127.0.0.1:8765/?token=dev-token`. Against the real server, open the page from Vite with
`?token=…&api=http://127.0.0.1:<port>` (the server allows the dev origin when `SPEC_SURGEON_DEV=1`).

Mock-only routes, never in the real server: `POST /mock/scenario` with `{"name": "preview"}` (one of
`empty`, `attached`, `question`, `preview`, `layouts`, `done`, `todo`, `paused`, `update-available`,
`update-ready`, `up-to-date`, `no-key`, `environment-key`, `safe`, `safe-done`, `standing`, `failed-preview`,
`failed-every-preview`, and Obliterate's, below) sets the view to a canned scenario (a check for updates finds
1.7.0, except after `up-to-date`, where it answers "You're up to date"); `POST /mock/reset` empties it.
`MOCK_DELAY=0` removes the pauses between frames; `MOCK_PICK=1` makes `/api/pick` answer with canned paths instead
of 501.

Obliterate mode's own tools answer from canned data too, with no Word and no file system behind them. Their
scenarios: `obliterate-offered` (Default mode, with Obliterate shown in the top bar: its warning opens from here),
`obliterate` (the mode on, nothing done yet), `obliterate-rescue` (three refused documents with something to try:
one Word must repair, one protected with a password, one with a document type declaration), `obliterate-no-word`
(the same, on a computer where Word cannot be reached) and `obliterate-done` (three copies, one delivered
UNVERIFIED). What the mock decides in them:

- **The hands-on tools** (`POST /api/hands-on`, `/api/parts`, `/api/part`, `/api/parts/search`, `/api/part/save`,
  `/api/part/replace`): each document has a small package made up from its name and triage's notes, its main part
  long enough for two pages. **Save this part** answers as if the native folder dialog had returned a folder (so
  does Export). **Replace this part** previews the file it is given: its words are compared with the canned
  part's, a file that is not well-formed XML fails V1 and is delivered UNVERIFIED, and once created the browser
  shows the file in the edited copy while the original keeps its own.
- **Word actions** (`POST /api/word-actions`): each action finds in each of the three canned documents what the
  table `WORD_FINDS` says (the second has no tracked change and types its numbers by hand; only the third is
  restricted), so *Remove protection* on every document gives one copy and two documents without one.
- **Try to rescue** (`POST /api/rescue`): any password opens the protected document except one that starts with
  `wrong`; in Obliterate mode an attached `.doc` or `.docm` is refused with a rescue to try.
- **Export anyway** (`POST /api/export` with `anyway`): a clean export — Word (Tracked Changes OFF) or PDF
  (Clean) — leaves a copy delivered UNVERIFIED without a file, and that kind is then offered for that copy under
  *Made by Word, unchecked*.

Not modelled: a mark carried from an earlier copy (`in an earlier copy: `), a pass over the to-do list that stays
open after a Word run, a file past the size limits (`too-large`), `recovery_blocked`, and Claude planning a raw
XML edit or a Word action in the conversation (the mock's conversation has one canned plan).

On Windows (PowerShell or Command Prompt), one command per line:

```
cd frontend
npm install
npm run mock
```

```
cd frontend
npm run dev
```

## Tests

The pure rules (the frame parser, the reducer, the chat rules, the action label, the token and base
resolution, the workbench rules, the review marks, the top bar's rules, the colour schemes against the
stylesheet, the reveal's pace and blocks) run under `node --test`:

```
cd frontend
npm test
```

The paths the screenshots do not show — a failed job, a pause and its Continue, Stop now mid-job, typing
ahead, a chip click, the draft hold on the action button, the folding documents tray, the file-chooser fallback,
a drop, Settings and a colour scheme kept across a reload, About, the update banner, Start over, the checkmark every
preview starts with, the red X on a preview (Create's count, Include all), the to-do list's pass, Safe mode's
buttons, and Obliterate mode with the hands-on tools (the warning's box, the strip's switch, the part browser, Save
and Replace this part), Word actions, Try to rescue and Export anyway — run
in a real browser against the mock, one PASS/FAIL line each (build first). They are not part of CI: run them by hand
after a change to the page or to the mock.

```
cd frontend
npm run flows
```

## Build

Builds into `src/spec_surgeon/web/static/` (type check first, then Vite; the folder is emptied and
rewritten). The built assets are committed: the Python package ships them, and CI rebuilds and compares.

```
cd frontend
npm run build
```

## Screenshots

Builds are screenshotted against the mock server with Playwright (a devDependency). The browsers Playwright
expects are found through `PLAYWRIGHT_BROWSERS_PATH`; when the installed revision does not match, the newest
Chromium found there (or `CHROMIUM_PATH`) is launched by path. Pictures land in `frontend/screenshots/`
(ignored by git); `--docs ../docs` also writes the three pictures `docs/CONVERSATIONAL_DESKTOP.md` shows.

```
cd frontend
npm run build
npm run screenshots
```

```
cd frontend
node tools/screenshots.mjs --docs ../docs
```

On Windows, with the browsers in the default location, the same commands run as written; to point at a
specific Chromium:

```
$env:CHROMIUM_PATH = "C:\path\to\chrome.exe"
node tools\screenshots.mjs --docs ..\docs
```

```
set CHROMIUM_PATH=C:\path\to\chrome.exe
node tools\screenshots.mjs --docs ..\docs
```
