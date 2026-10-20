#!/usr/bin/env node
/**
 * Screenshots of the built page against the mock server, with Playwright.
 *
 *   npm run build
 *   node tools/screenshots.mjs                 # frontend/screenshots/*.png
 *   node tools/screenshots.mjs --docs ../docs  # also the three README pictures
 *
 * Browser and mock server: tools/browser.mjs.
 */
import { mkdirSync } from "node:fs";
import { join, resolve } from "node:path";
import { FRONTEND, launch, startMock } from "./browser.mjs";

const args = process.argv.slice(2);
const docsIndex = args.indexOf("--docs");
const DOCS_DIR = docsIndex !== -1 ? resolve(process.cwd(), args[docsIndex + 1] || "../docs") : null;
const OUT = resolve(FRONTEND, "screenshots");
const PORT = Number(process.env.PORT || 8799);
const TOKEN = "shot-token";

mkdirSync(OUT, { recursive: true });

async function shot(page, name, extraPaths = []) {
  const file = join(OUT, `${name}.png`);
  await page.screenshot({ path: file });
  for (const extra of extraPaths) await page.screenshot({ path: extra });
  console.log(`wrote ${file}${extraPaths.length ? ` and ${extraPaths.join(", ")}` : ""}`);
}

const mock = await startMock({ port: PORT, token: TOKEN });
const { base: BASE, scenario } = mock;
try {
  const browser = await launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 1 });
  page.on("pageerror", (e) => console.error("page error:", e));

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
  await page.waitForTimeout(900);
  await shot(page, "conversation-done");

  // 6. Two layouts, a pause, an update and no key: canned views.
  await scenario("layouts");
  await page.reload();
  await page.getByText("Your documents use 2 layouts").waitFor();
  await page.waitForTimeout(1000);
  await shot(page, "plan-layouts");
  await scenario("paused");
  await page.reload();
  await page.getByText("Work paused; your progress is saved").waitFor();
  await page.waitForTimeout(1000);
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
  await page.waitForTimeout(1000);
  await shot(page, "narrow-1000");
  await page.getByRole("button", { name: /Hide plan/ }).click();
  await page.waitForTimeout(300);
  await shot(page, "narrow-1000-collapsed");

  await browser.close();
} finally {
  mock.kill();
}
