#!/usr/bin/env node
/**
 * Drives the built page through the mock's jobs and routes in a real
 * browser — the paths the screenshots do not show: a failed job, a pause
 * and its Continue, Stop now mid-job, typing ahead, a chip click, the
 * draft hold on the action button, the file-chooser fallback, a drop,
 * remove, no key → Settings, Settings' colour schemes, the top bar's help topics, About and its
 * update check, the update banner and the pill it leaves, start over, the
 * button's home in the conversation, the folding documents tray, and the
 * red X on a preview → the to-do list → the pass over it.
 *
 *   npm run build
 *   node tools/flows.mjs
 *
 * Prints one PASS/FAIL line per check and exits non-zero on any FAIL.
 * Browser and mock server: tools/browser.mjs.
 */
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { launch, startMock } from "./browser.mjs";

const PORT = Number(process.env.PORT || 8811);
const TOKEN = "flow-token";
const results = [];
const check = (name, ok, extra = "") => results.push(`${ok ? "PASS" : "FAIL"} ${name}${extra ? ` — ${extra}` : ""}`);

const mock = await startMock({ port: PORT, token: TOKEN, delay: "40" });
const { base: BASE, scenario } = mock;
const scratch = mkdtempSync(join(tmpdir(), "spec-surgeon-flows-"));
try {
  const browser = await launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  const userBubbles = () => page.locator("main section[aria-label='Conversation'] .rounded-br-md");

  // 1. A failed job: the error bubble, the page idle, the user bubble rolled back with the view.
  await scenario("attached");
  await page.goto(`${BASE}/?token=${TOKEN}`);
  const box = page.getByLabel("Describe a change or answer a question");
  await box.fill("please fail");
  await box.press("Enter");
  await page.getByRole("alert").waitFor({ timeout: 20000 });
  const alertText = await page.getByRole("alert").innerText();
  check("a failed job shows the error bubble", alertText.includes("could not be reached"), alertText.slice(0, 80));
  await page.waitForTimeout(400);
  check("the failed job's user bubble gave way to the server's view", (await userBubbles().count()) === 0);
  check("Send is back after the failure", await page.getByRole("button", { name: "Send" }).isVisible());
  check("the refused request is back in the composer", (await box.inputValue()) === "please fail", await box.inputValue());
  await box.fill("");

  // 2. A pause: the card with Continue; Continue runs the primary job.
  await box.fill("pause please");
  await box.press("Enter");
  await page.getByText("Work paused; your progress is saved").waitFor({ timeout: 20000 });
  const cont = page.getByRole("button", { name: "Continue" });
  check("the pause card offers Continue", await cont.isEnabled());
  check("exactly one Continue while paused", (await cont.count()) === 1);
  await cont.click();
  await page.getByText("Continuing where the conversation stopped.").waitFor({ timeout: 20000 });
  await page.waitForTimeout(800);
  check("after Continue the pause card is gone", (await page.getByText("Work paused; your progress is saved").count()) === 0);

  // 3. Stop now mid-job, with typing ahead.
  await box.fill("plan it");
  await box.press("Enter");
  await page.getByRole("button", { name: "Stop now" }).first().waitFor({ timeout: 10000 });
  check("Stop now shows while busy", true);
  await box.fill("typing ahead while busy");
  check("typing ahead is allowed while busy", (await box.inputValue()) === "typing ahead while busy");
  await page.getByLabel("Stop now").click();
  await page.getByText("Stopped. No AI step runs until you continue").waitFor({ timeout: 20000 });
  await page.waitForTimeout(500);
  check("Stop ends the job and the page is idle", await page.getByRole("button", { name: "Send" }).isVisible());
  await box.fill("");

  // 4. A chip click sends its text and clears the chips.
  await box.fill("a question");
  await box.press("Enter");
  await page.getByRole("button", { name: "Just the body" }).waitFor({ timeout: 20000 });
  await page.getByRole("button", { name: "Just the body" }).click();
  await page.waitForTimeout(300);
  check("chips are cleared on send", (await page.getByRole("button", { name: "Just the body" }).count()) === 0);
  await page.getByRole("button", { name: "Create edited copies" }).waitFor({ timeout: 20000 });
  check("the chip's text became the user bubble", (await userBubbles().last().innerText()) === "Just the body");

  // 5. A draft holds the action button and says why.
  await box.fill("hmm");
  await page.waitForTimeout(100);
  const create = page.getByRole("button", { name: "Create edited copies" });
  check("an unsent draft disables the action button", !(await create.isEnabled()));
  const tip = await create.locator("xpath=..").getAttribute("title");
  check("…and says why", tip === "Send your new message before approving a plan.", tip ?? "");
  await box.fill("");
  await page.waitForTimeout(100);
  check("clearing the draft re-enables it", await create.isEnabled());

  // 6. Add Word documents → 501 → the browser's file input → upload; the plan is void. The tray folded itself
  //    when the plan arrived; open it first.
  const trayToggle = page.getByRole("button", { name: /^Your documents/ });
  check("the documents tray folds to its title once a plan exists", (await trayToggle.getAttribute("aria-expanded")) === "false");
  await trayToggle.click();
  check("…and opens on a click", (await trayToggle.getAttribute("aria-expanded")) === "true");
  const [chooser] = await Promise.all([page.waitForEvent("filechooser"), page.getByRole("button", { name: "Add Word documents" }).click()]);
  check("a 501 from /api/pick falls back to a file chooser", chooser.isMultiple());
  const extra = join(scratch, "Extra spec.docx");
  writeFileSync(extra, "x");
  await chooser.setFiles(extra);
  await page.getByText("Extra spec.docx").first().waitFor({ timeout: 10000 });
  check("the uploaded file appears in the tray", true);
  check("attaching after a plan voids the action (Continue)", await page.getByRole("button", { name: "Continue" }).first().isVisible());

  // 7. The drop ring and a dropped file.
  const dt = await page.evaluateHandle(() => {
    const transfer = new DataTransfer();
    transfer.items.add(new File(["x"], "Dropped spec.docx"));
    return transfer;
  });
  await page.dispatchEvent("main", "dragenter", { dataTransfer: dt });
  await page.waitForTimeout(100);
  check("the drop ring shows on dragenter", (await page.locator(".drop-ring").count()) === 1);
  await page.dispatchEvent("main", "drop", { dataTransfer: dt });
  await page.getByText("Dropped spec.docx").first().waitFor({ timeout: 10000 });
  check("a dropped file is uploaded and attached", true);
  check("the drop ring is gone after the drop", (await page.locator(".drop-ring").count()) === 0);

  // 8. Remove.
  await page.getByRole("button", { name: "Remove Dropped spec.docx" }).click();
  await page.waitForTimeout(300);
  check("remove works", (await page.getByText("Dropped spec.docx").count()) === 0);

  // 9. No key: the composer says why; Settings saves and reconnects.
  await scenario("no-key");
  await page.reload();
  await page.getByText("Connect your API key").waitFor();
  const sendBtn = page.getByRole("button", { name: "Send" });
  check("no key: Send is disabled", !(await sendBtn.isEnabled()));
  const reasonTip = await sendBtn.locator("xpath=..").getAttribute("title");
  check("no key: the reason on hover", reasonTip === "Connect your Anthropic API key in Settings to start.", reasonTip ?? "");
  await page.getByRole("button", { name: "Connect your API key" }).click();
  await page.getByLabel("Anthropic API key").fill("sk-ant-test");
  await page.getByRole("button", { name: "Save" }).click();
  await page.getByText("The key is connected.").waitFor({ timeout: 5000 });
  await page.keyboard.press("Escape");
  await page.waitForTimeout(500);
  check("the key pill reads Connected after saving", await page.getByText("Connected", { exact: true }).isVisible());

  // 9b. Settings → Appearance: a scheme recolours the window at once, the server keeps it, a reload opens in it.
  const bodyBg = () => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  const savedTheme = () => page.waitForResponse((r) => r.url().endsWith("/api/settings") && r.request().method() === "POST");
  await page.getByRole("button", { name: "Settings" }).click();
  const schemes = page.getByRole("radiogroup", { name: "Colour scheme" });
  await schemes.getByRole("radio", { name: "Warm Clay" }).waitFor();
  check(
    "Appearance offers five schemes, Warm Clay chosen",
    (await schemes.getByRole("radio").count()) === 5 &&
      (await schemes.getByRole("radio", { name: "Warm Clay" }).getAttribute("aria-checked")) === "true",
  );
  await Promise.all([savedTheme(), schemes.getByRole("radio", { name: "Linen" }).click()]);
  check("picking Linen recolours the window at once", (await bodyBg()) === "rgb(245, 241, 234)", await bodyBg());
  check("…and marks it chosen", (await schemes.getByRole("radio", { name: "Linen" }).getAttribute("aria-checked")) === "true");
  const kept = await (await fetch(`${BASE}/api/settings`, { headers: { "X-Spec-Surgeon-Token": TOKEN } })).json();
  check("the server keeps the scheme", kept.theme === "linen", String(kept.theme));
  await page.keyboard.press("Escape");
  await page.reload();
  await page.getByText("Connected", { exact: true }).waitFor();
  check(
    "a reload opens in Linen",
    (await page.evaluate(() => document.documentElement.dataset.theme)) === "linen" && (await bodyBg()) === "rgb(245, 241, 234)",
    await bodyBg(),
  );
  await page.getByRole("button", { name: "Settings" }).click();
  await Promise.all([savedTheme(), schemes.getByRole("radio", { name: "Warm Clay" }).click()]);
  check("…and back to Warm Clay", (await bodyBg()) === "rgb(38, 38, 36)", await bodyBg());
  await page.keyboard.press("Escape");

  // 10. The top bar's help topics: one dialog, a tab strip, Escape closes it.
  const topBar = page.locator("header nav[aria-label='Help']");
  check(
    "the top bar names the help topics, without Why trust it yet",
    JSON.stringify(await topBar.getByRole("button").allInnerTexts()) === JSON.stringify(["How to use", "Workflows", "How it works", "About"]),
    JSON.stringify(await topBar.getByRole("button").allInnerTexts()),
  );
  check("the old Help menu is gone", (await page.getByRole("button", { name: "Help ▾" }).count()) === 0);
  await topBar.getByRole("button", { name: "How to use" }).click();
  const help = page.getByRole("dialog", { name: "How to use Spec Surgeon" });
  await help.waitFor();
  check("How to use opens with its steps", await help.getByText("Connect your Anthropic API key").isVisible());
  await help.getByRole("tab", { name: "Workflows" }).click();
  await page.getByRole("dialog", { name: "Typical workflows" }).getByText("Swap a logo").waitFor();
  check("the tab strip moves to Workflows in the same dialog", true);
  await page.getByRole("tab", { name: "How it works" }).click();
  check("…and to How it works", await page.getByRole("dialog", { name: "How it works" }).getByText("Claude plans, the engine edits").isVisible());
  check("the active tab is marked", (await page.getByRole("tab", { name: "How it works" }).getAttribute("aria-selected")) === "true");
  await page.keyboard.press("Escape");
  await page.waitForTimeout(200);
  check("Escape closes the help dialog", (await page.getByRole("dialog").count()) === 0);

  // 11. About: the version, the model, the release notes, the license; a check that finds nothing says so.
  await scenario("up-to-date");
  await page.reload();
  await page.locator("header nav[aria-label='Help']").getByRole("button", { name: "About" }).click();
  const about = page.getByRole("dialog", { name: "About" });
  await about.getByText("What’s new in 1.6.0").waitFor();
  check("About shows the release notes", await about.getByText("The window is a web page").isVisible({ timeout: 3000 }).catch(() => false));
  check("About names the model", await about.getByText("Claude Opus 5.5").isVisible());
  await about.getByRole("button", { name: "Read the full license" }).click();
  check("About shows the full license", await about.getByText("Permission is hereby granted").isVisible());
  await about.getByRole("button", { name: "Check for updates" }).click();
  check("a check says it is checking", await about.getByText("Checking for updates…").isVisible({ timeout: 1000 }).catch(() => false));
  await about.getByText("You're up to date").waitFor({ timeout: 15000 });
  check("…then that you are up to date", true);
  // Closed while its check runs and reopened after it answered, About still shows that answer.
  await about.getByRole("button", { name: "Check for updates" }).click();
  await page.keyboard.press("Escape");
  await page.waitForTimeout(3500);
  await page.locator("header nav[aria-label='Help']").getByRole("button", { name: "About" }).click();
  check(
    "a check's answer survives closing and reopening About",
    await page.getByRole("dialog", { name: "About" }).getByText("You're up to date").isVisible({ timeout: 3000 }).catch(() => false),
  );
  await about.getByRole("button", { name: "Open diagnostic logs" }).click();
  check("Open diagnostic logs is in About", true);
  await page.keyboard.press("Escape");

  // 12. Updates: About's check finds one → the banner → Not now leaves a pill → the pill downloads → ready.
  await scenario("attached");
  await page.reload();
  await page.locator("header nav[aria-label='Help']").getByRole("button", { name: "About" }).click();
  await page.getByRole("dialog", { name: "About" }).getByRole("button", { name: "Check for updates" }).click();
  await page.getByRole("dialog", { name: "About" }).getByText("Version 1.7.0 is available.").waitFor({ timeout: 15000 });
  check("About offers the update it found", await page.getByRole("dialog", { name: "About" }).getByRole("button", { name: "Download and install" }).isVisible());
  await page.keyboard.press("Escape");
  await page.getByText("is available").first().waitFor({ timeout: 5000 });
  check("no update pill while the banner shows it", (await page.getByRole("button", { name: /available — install/ }).count()) === 0);
  await page.getByRole("button", { name: "Not now" }).click();
  const pill = page.getByRole("button", { name: "v1.7.0 available — install" });
  check("Not now leaves the update one click away in the top bar", await pill.isVisible({ timeout: 2000 }).catch(() => false));
  await pill.click();
  await page.getByText("Downloading Spec Surgeon").waitFor({ timeout: 5000 });
  check("the pill downloads, and the banner shows the progress", (await pill.count()) === 0);
  await page.getByRole("button", { name: "Restart to update" }).waitFor({ timeout: 15000 });
  check("the update banner walks available → downloading → ready", true);

  // 13. Copies and Start over.
  await scenario("done");
  await page.reload();
  await page.getByText("Your edited copies").waitFor();
  check("Start over from the originals is offered after a run", await page.getByRole("button", { name: "Start over from the originals" }).isVisible());
  await page.getByRole("button", { name: "Start over from the originals" }).click();
  await page.waitForTimeout(400);
  check("Start over clears the copies", (await page.getByText("Your edited copies").count()) === 0);

  // 13. The one button lives in the conversation pane; the plan card carries none; there is no Hide plan button.
  await scenario("preview");
  await page.reload();
  const createBtn = page.getByRole("button", { name: "Create edited copies" });
  await createBtn.waitFor();
  check("no Hide plan button", (await page.getByRole("button", { name: /Hide plan|Show plan/ }).count()) === 0);
  check("the action button is in the conversation pane", (await page.locator("section[aria-label='Conversation']").getByRole("button", { name: "Create edited copies" }).count()) === 1);
  check("the plan card has no button", (await page.locator("section[aria-label='Your documents, the plan and the pages']").getByRole("button", { name: "Create edited copies" }).count()) === 0);

  // 14. Markdown renders in both panes (the chat's reply and the plan's sentences).
  check("the chat renders markdown", (await page.locator("section[aria-label='Conversation'] .md").count()) > 0);
  check("the plan renders markdown", (await page.locator(".paper .md").count()) > 0);

  // 15. The red X on a preview counts on the button; approving sets that document aside; the to-do card offers the pass.
  const holdBtn = page.getByRole("button", { name: /Needs more work · 21 05 00/ });
  await holdBtn.click();
  check("the X marks the preview", (await holdBtn.getAttribute("aria-pressed")) === "true");
  check("…and the button counts it", await page.getByRole("button", { name: "Create edited copies (1 set aside)" }).isVisible());
  await page.getByRole("button", { name: /Looks good · 21 13 13/ }).click();
  check("the checkmark marks the other", (await page.getByRole("button", { name: /Looks good · 21 13 13/ }).getAttribute("aria-pressed")) === "true");
  await page.getByRole("button", { name: "Create edited copies (1 set aside)" }).click();
  await page.getByText("Your edited copies").waitFor({ timeout: 20000 });
  await page.getByRole("button", { name: "Work on it now" }).waitFor({ timeout: 20000 });   // the job's final view
  const todoCard = page.locator("[aria-label='Your to-do list']");
  check("the set-aside document is on the to-do card", (await todoCard.getByText("21 05 00 Common Work Results for Fire Suppression.docx", { exact: true }).count()) === 1);
  check("…and under the copies", (await page.locator("section[aria-label='Set aside for more work']").getByText("21 05 00", { exact: false }).count()) === 1);
  check("the copy that looked right is listed", (await page.locator("section[aria-label='Your edited copies']").getByText("21 13 13", { exact: false }).count()) === 1);
  check("the set-aside document got no copy", (await page.locator("section[aria-label='Your edited copies']").getByText("21 05 00", { exact: false }).count()) === 0);
  if ((await trayToggle.getAttribute("aria-expanded")) === "false") await trayToggle.click();
  check("the tray tags it", await page.getByText("To do", { exact: true }).first().isVisible());
  await page.getByRole("button", { name: "Work on it now" }).click();
  await page.getByText(/Working on 21 05 00/).waitFor({ timeout: 20000 });
  await page.waitForTimeout(300);
  check("the pass over the set-aside document begins with Continue", await page.getByRole("button", { name: "Continue" }).first().isVisible());
  check("the to-do card is gone during the pass", (await page.getByRole("button", { name: "Work on it now" }).count()) === 0);

  check("no page errors", errors.length === 0, errors.join(" | ").slice(0, 300));
  await browser.close();
} catch (failure) {
  results.push(`CRASH ${failure}`);
} finally {
  mock.kill();
}
console.log(results.join("\n"));
process.exit(results.some((line) => !line.startsWith("PASS")) ? 1 : 0);
