# The window's web page

Spec Surgeon's desktop window is this page (React 18, TypeScript, Vite, Tailwind v4), built once into
`src/spec_surgeon/web/static/` and hosted by the local server described in `docs/WEB_API.md`. The visual
system is Build-a-Spec's, copied and adapted (never imported across repositories): the warm dark chrome, the
paper content cards, Georgia for display text, the system sans for everything else.

The page never applies edits or derives state: every `done`/`error` frame and `GET /api/state` carry the
whole view, and the page renders it (`src/lib/view.ts`). Streamed frames are presentation only.

## What is where

| Path | What |
| --- | --- |
| `src/lib/connection.ts` | the token and API base (`window.SPEC_SURGEON`, else `?token=` and `?api=`) |
| `src/lib/sse.ts` | the `data: <json>` frame parser and the stream reader |
| `src/lib/view.ts` | the reducer over the view and the live reply bubble |
| `src/lib/chat.ts`, `action.ts`, `workbench.ts`, `review.ts`, `help.ts` | the pure rules the components render (the review marks and `hold` live in `review.ts`; `help.ts`: the top bar's topics and context line, About's update line) |
| `src/lib/api.ts` | every route of the contract |
| `src/components/` | the header (the top bar), the chat, the workbench, the modals; `HelpModal.tsx` is the in-app guide and About |
| `mock/server.mjs` | a Node-only mock of the whole contract |
| `tools/screenshots.mjs` | Playwright screenshots against the mock |
| `tools/flows.mjs` | Playwright walk through the other paths (a failed job, a pause, Stop, a drop, Settings, the top bar's help topics, About and its update check, the update banner, the review marks and the to-do list) |
| `tools/browser.mjs` | shared by both: the mock's launch and the Chromium lookup |
| `tests/*.test.ts` | `node --test` over the pure rules |

## Development

Install once (Node 22 or newer; the tests depend on its TypeScript type stripping):

```
cd frontend
npm install
```

Start the mock server in one terminal. It answers every route with canned data and scripted jobs; the
job a request runs is chosen by its text: `question` asks one thing with choices and chips, `fail` ends in an
error, `pause` ends in a budget pause, anything else plans and previews, and the primary action creates
copies (the previews marked with the red X are sent as `hold` and land on the to-do list; the reply "Work on the
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
`update-ready`, `up-to-date`, `no-key`) sets the view to a canned scenario (a check for updates finds 1.7.0,
except after `up-to-date`, where it answers "You're up to date"); `POST /mock/reset` empties it. `MOCK_DELAY=0`
removes the pauses between frames; `MOCK_PICK=1` makes `/api/pick` answer with canned paths instead of 501.

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
resolution, the workbench rules, the review marks, the top bar's rules) run under `node --test`:

```
cd frontend
npm test
```

The paths the screenshots do not show — a failed job, a pause and its Continue, Stop now mid-job, typing
ahead, a chip click, the draft hold on the action button, the folding documents tray, the file-chooser fallback,
a drop, Settings, About, the update banner, Start over, the red X on a preview and the to-do list's pass — run
in a real browser against the mock, one PASS/FAIL line each (build first):

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
