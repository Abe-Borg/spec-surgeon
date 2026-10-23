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
  // The previews rise in and the scroll to them is smooth.
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

  // 6. A pause, an update and no key: canned views.
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

  // 7. The top bar's guide: How to use, and About.
  await scenario("attached");
  await page.reload();
  const topics = page.locator("header nav[aria-label='Help']");
  await topics.getByRole("button", { name: "How to use" }).click();
  await page.getByRole("dialog", { name: "How to use Spec Surgeon" }).waitFor();
  await page.waitForTimeout(300);
  await shot(page, "help-how-to-use");
  await page.getByRole("tab", { name: "About" }).click();
  await page.getByRole("dialog", { name: "About" }).getByText("Open-source components").waitFor();
  await page.waitForTimeout(300);
  await shot(page, "help-about");
  await page.keyboard.press("Escape");

  // 8. The to-do list: a document set aside with the red X, after the other copies were created.
  await scenario("todo");
  await page.reload();
  await page.getByText("Set aside for more work").first().waitFor();
  await page.waitForTimeout(800);
  await shot(page, "todo");

  // 9. Safe mode: the buttons in place of the message box, a button's form, and what a button left untouched.
  await scenario("safe");
  await page.reload();
  await page.getByRole("region", { name: "Safe mode" }).waitFor();
  await page.waitForTimeout(600);
  await shot(page, "safe-mode");
  await page.getByRole("button", { name: "Match headers and footers to template" }).click();
  await page.waitForTimeout(300);
  await shot(page, "safe-form");
  await page.getByRole("button", { name: "All buttons" }).click();
  await page.getByRole("button", { name: "Match body formatting to template" }).click();
  await page.waitForTimeout(300);
  await shot(page, "safe-body-form");
  await scenario("safe-done");
  await page.reload();
  await page.getByText("Left untouched by the last button").waitFor();
  await page.waitForTimeout(800);
  await shot(page, "safe-done");

  // 10. Obliterate mode (shown in Settings → Advanced): a copy delivered UNVERIFIED, then the warning it opens with.
  await scenario("obliterate-done");
  await page.reload();
  await page.getByText("Your edited copies").waitFor();
  await page.waitForTimeout(800);
  await shot(page, "obliterate-done");
  const modeSwitch = page.getByRole("group", { name: "Mode" });
  await modeSwitch.getByRole("button", { name: "Default" }).click();
  await modeSwitch.getByRole("button", { name: "Obliterate" }).click();
  await page.getByRole("dialog").filter({ hasText: "What it grants" }).waitFor();
  await page.waitForTimeout(300);
  await shot(page, "obliterate-warning");
  await page.keyboard.press("Escape");

  // 11. The narrow window: 1000 px wide, the documents folded to their title line beside the plan.
  await scenario("preview");
  await page.setViewportSize({ width: 1000, height: 700 });
  await page.reload();
  await page.getByRole("button", { name: "Create edited copies" }).waitFor();
  await page.waitForFunction(() => Array.from(document.images).every((i) => i.complete));
  await page.waitForTimeout(1000);
  await shot(page, "narrow-1000");

  await browser.close();
} finally {
  mock.kill();
}
