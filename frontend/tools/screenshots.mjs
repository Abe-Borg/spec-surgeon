#!/usr/bin/env node
/**
 * Screenshots of the built page against the mock server, with Playwright.
 *
 *   npm run build
 *   node tools/screenshots.mjs                 # frontend/screenshots/*.png
 *   node tools/screenshots.mjs --docs ../docs  # also the three README pictures
 *
 * Uses the browsers Playwright already has (PLAYWRIGHT_BROWSERS_PATH); when
 * the installed revision does not match this Playwright, the newest Chromium
 * binary found there (or CHROMIUM_PATH) is launched by path.
 */
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, readdirSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND = resolve(HERE, "..");
const args = process.argv.slice(2);
const docsIndex = args.indexOf("--docs");
const DOCS_DIR = docsIndex !== -1 ? resolve(process.cwd(), args[docsIndex + 1] || "../docs") : null;
const OUT = resolve(FRONTEND, "screenshots");
const PORT = Number(process.env.PORT || 8799);
const TOKEN = "shot-token";
const BASE = `http://127.0.0.1:${PORT}`;

mkdirSync(OUT, { recursive: true });

function findChromium() {
  if (process.env.CHROMIUM_PATH && existsSync(process.env.CHROMIUM_PATH)) return process.env.CHROMIUM_PATH;
  const roots = [process.env.PLAYWRIGHT_BROWSERS_PATH, "/opt/pw-browsers"].filter(Boolean);
  const candidates = [];
  for (const root of roots) {
    if (!existsSync(root)) continue;
    for (const entry of readdirSync(root)) {
      if (!/^chromium-\d+$/.test(entry)) continue;
      for (const rel of ["chrome-linux/chrome", "chrome-win/chrome.exe", "chrome-mac/Chromium.app/Contents/MacOS/Chromium"]) {
        const file = join(root, entry, rel);
        if (existsSync(file) && statSync(file).isFile()) candidates.push({ file, rev: Number(entry.split("-")[1]) });
      }
    }
  }
  candidates.sort((a, b) => b.rev - a.rev);
  return candidates[0]?.file ?? null;
}

async function launch() {
  try {
    return await chromium.launch();
  } catch (failure) {
    const file = findChromium();
    if (!file) throw failure;
    console.log(`Playwright's own Chromium is not installed; launching ${file}`);
    return chromium.launch({ executablePath: file });
  }
}

function startMock() {
  const child = spawn(process.execPath, [join(FRONTEND, "mock", "server.mjs")], {
    env: { ...process.env, PORT: String(PORT), MOCK_TOKEN: TOKEN, MOCK_DELAY: "25" },
    stdio: ["ignore", "pipe", "inherit"],
  });
  child.stdout.on("data", () => undefined);
  return child;
}

async function waitForMock() {
  for (let i = 0; i < 100; i++) {
    try {
      const resp = await fetch(`${BASE}/mock/health`);
      if (resp.ok) return;
    } catch {
      // not up yet
    }
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error("The mock server did not start");
}

async function scenario(name) {
  const resp = await fetch(`${BASE}/mock/scenario`, { method: "POST", body: JSON.stringify({ name }) });
  if (!resp.ok) throw new Error(`scenario ${name}: ${resp.status}`);
}

async function shot(page, name, extraPaths = []) {
  const file = join(OUT, `${name}.png`);
  await page.screenshot({ path: file });
  for (const extra of extraPaths) await page.screenshot({ path: extra });
  console.log(`wrote ${file}${extraPaths.length ? ` and ${extraPaths.join(", ")}` : ""}`);
}

const mock = startMock();
try {
  await waitForMock();
  const browser = await launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 1 });
  page.on("pageerror", (e) => console.error("page error:", e));
  page.on("console", (m) => {
    if (m.type() === "error") console.error("console:", m.text());
  });

  // 1. The start: documents attached, nothing asked yet.
  await scenario("attached");
  await page.goto(`${BASE}/?token=${TOKEN}`);
  await page.getByText("Less document work. More done.").waitFor();
  await page.getByText("Your documents · 3").waitFor();
  await page.waitForTimeout(400);
  await shot(page, "conversation-start", DOCS_DIR ? [join(DOCS_DIR, "conversation-start.png")] : []);

  // 2. A question with choices and chips, mid-conversation.
  const box = page.getByLabel("Describe a change or answer a question");
  await box.fill("I have a question about this: change every font to Arial 10 pt and update the footer to say Issued for Construction.");
  await box.press("Enter");
  await page.getByText("One thing to clarify", { exact: true }).waitFor({ timeout: 30000 });
  await page.getByRole("button", { name: /Just the body/ }).waitFor();
  await page.waitForTimeout(500);
  await shot(page, "conversation-chat", DOCS_DIR ? [join(DOCS_DIR, "conversation-chat.png")] : []);

  // 3. A plan with previews and the action button.
  await page.getByRole("button", { name: /^1\./ }).click();
  await page.getByRole("button", { name: "Create edited copies" }).waitFor({ timeout: 30000 });
  await page.locator("img[alt^='Before and after']").first().waitFor();
  await page.waitForFunction(() => Array.from(document.images).every((i) => i.complete));
  // The plan card's arrival tint settles over 0.6 s and the scroll is smooth.
  await page.waitForTimeout(1200);
  await shot(page, "conversation-preview", DOCS_DIR ? [join(DOCS_DIR, "conversation-preview.png")] : []);

  // 4. The zoomed pages.
  await page.locator("img[alt^='Before and after']").first().click();
  await page.getByRole("button", { name: "100%" }).waitFor();
  await page.waitForTimeout(300);
  await shot(page, "preview-zoom");
  await page.keyboard.press("Escape");

  // 5. Copies created.
  await page.getByRole("button", { name: "Create edited copies" }).click();
  await page.getByText("Your edited copies").waitFor({ timeout: 30000 });
  await page.waitForTimeout(500);
  await shot(page, "conversation-done");

  // 6. Two layouts, a pause, an update and no key: canned views.
  await scenario("layouts");
  await page.reload();
  await page.getByText("Your documents use 2 layouts").waitFor();
  await page.waitForTimeout(500);
  await shot(page, "plan-layouts");
  await scenario("paused");
  await page.reload();
  await page.getByText("Work paused; your progress is saved").waitFor();
  await page.waitForTimeout(300);
  await shot(page, "paused");
  await scenario("update-available");
  await page.reload();
  await page.getByText("is available").waitFor();
  await page.waitForTimeout(300);
  await shot(page, "update-available");
  await scenario("no-key");
  await page.reload();
  await page.getByText("Connect your API key").waitFor();
  await page.getByRole("button", { name: "Settings" }).click();
  await page.getByText("AI connection").waitFor();
  await page.waitForTimeout(300);
  await shot(page, "settings");
  await page.keyboard.press("Escape");

  // 7. The narrow window: 1000 px wide, with the plan shown and hidden.
  await scenario("preview");
  await page.setViewportSize({ width: 1000, height: 700 });
  await page.reload();
  await page.getByRole("button", { name: "Create edited copies" }).waitFor();
  await page.waitForFunction(() => Array.from(document.images).every((i) => i.complete));
  await page.waitForTimeout(500);
  await shot(page, "narrow-1000");
  await page.getByRole("button", { name: /Hide plan/ }).click();
  await page.waitForTimeout(300);
  await shot(page, "narrow-1000-collapsed");

  await browser.close();
} finally {
  mock.kill();
}
