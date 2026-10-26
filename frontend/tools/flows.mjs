#!/usr/bin/env node
/**
 * Drives the built page through the mock's jobs and routes in a real
 * browser — the paths the screenshots do not show: a failed job, a pause
 * and its Continue, Stop now mid-job, typing ahead, a chip click, the
 * draft hold on the action button, the file-chooser fallback, a drop,
 * remove, no key → Settings, Settings' colour schemes, the top bar's help topics, About and its
 * update check and diagnostic bundle, the page's own report of its load,
 * the update banner and the pill it leaves, start over, the button's home
 * in the conversation, the folding documents tray, and the red X on a
 * preview → the to-do list → the pass over it; Safe mode's buttons, a button's form → preview → copies, and the
 * hand-off of what it left untouched to Default mode.
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

  // 4b. Claude's drafted wording is editable before it is picked; the edit is what the pick approves.
  await box.fill("a question");
  await box.press("Enter");
  await page.getByRole("button", { name: "Edit wording" }).waitFor({ timeout: 20000 });
  await page.getByRole("button", { name: "Edit wording" }).click();
  const wordingBox = page.getByLabel("Your wording");
  check("the wording box starts from Claude's draft",
    (await wordingBox.inputValue()) === "Riverside Medical Center — Issued for Construction, 20 October 2026");
  await wordingBox.fill("Riverside Medical Center — Issued for Permit");
  check("the choice's own button cannot pick the hidden draft while it is being edited",
    await page.getByRole("button", { name: /reword the footer/ }).isDisabled());
  await wordingBox.press("Shift+Enter");
  await wordingBox.type("second line");
  check("a line break cannot be sent", await page.getByRole("button", { name: "Use this wording" }).isDisabled());
  await wordingBox.fill("Riverside Medical Center — Issued for Permit");
  await page.getByRole("button", { name: "Use this wording" }).click();
  await page.getByRole("button", { name: "Create edited copies" }).waitFor({ timeout: 20000 });
  check("the edited wording is the user's pick",
    (await userBubbles().last().innerText()) === "Everything, and reword the footer — “Riverside Medical Center — Issued for Permit”");

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
  await page.waitForFunction(() => !!document.querySelector('[role="radiogroup"][aria-label="Colour scheme"] [aria-checked="true"]'));
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

  // 9c. Settings → Fast responses: off, both sides shown, a second click that names the price turns it on, the pill
  // says Fast, one click turns it off.
  const fastSwitch = page.getByRole("switch", { name: "Fast responses" });
  check("Fast responses starts off", (await fastSwitch.getAttribute("aria-checked")) === "false");
  check(
    "the benefit and the price are both on screen",
    (await page.getByText(/writes its answers up to 2\.5× faster/).isVisible()) &&
      (await page.getByText(/\$8 instead of \$4 per million input tokens/).isVisible()),
  );
  await fastSwitch.click();
  const confirmFast = page.getByRole("button", { name: "Turn on — twice the price" });
  check("the switch asks first, with the price on the button", await confirmFast.isVisible());
  check("…and is not on yet", (await fastSwitch.getAttribute("aria-checked")) === "false");
  await Promise.all([savedTheme(), confirmFast.click()]);
  check("the second click turns it on", (await fastSwitch.getAttribute("aria-checked")) === "true");
  const fastKept = await (await fetch(`${BASE}/api/settings`, { headers: { "X-Spec-Surgeon-Token": TOKEN } })).json();
  check("the server keeps it", fastKept.fast_mode === true, String(fastKept.fast_mode));
  await page.keyboard.press("Escape");
  await page.reload();
  await page.getByText("Connected", { exact: true }).waitFor();
  check("the spending pill says Fast", await page.locator("header").getByText("Fast", { exact: true }).isVisible());
  await page.getByRole("button", { name: "Settings" }).click();
  await Promise.all([savedTheme(), fastSwitch.click()]);
  check("one click turns it off", (await fastSwitch.getAttribute("aria-checked")) === "false");
  await page.keyboard.press("Escape");

  // 10. The top bar's help topics: one dialog, a tab strip, Escape closes it.
  const topBar = page.locator("header nav[aria-label='Help']");
  check(
    "the top bar names the help topics, Why trust it before About",
    JSON.stringify(await topBar.getByRole("button").allInnerTexts()) === JSON.stringify(["How to use", "Workflows", "How it works", "Why trust it", "About"]),
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

  // 10b. Why trust it → the "I'm not convinced" dossier: it stacks on help, one Escape closes one dialog, focus returns.
  await topBar.getByRole("button", { name: "Why trust it" }).click();
  const trust = page.getByRole("dialog", { name: "Why trust it?" });
  await trust.waitFor();
  check("Why trust it opens with its claims", await trust.getByText("Your originals are never written").isVisible());
  const notConvinced = trust.getByRole("button", { name: "I’m not convinced — show me exactly what runs →" });
  await notConvinced.click();
  const dossier = page.getByRole("dialog", { name: "How Spec Surgeon actually works" });
  await dossier.waitFor();
  check("the button opens the dossier on top of Why trust it", (await page.getByRole("dialog").count()) === 2);
  check("focus moves into the dossier", await dossier.evaluate((el) => el.contains(document.activeElement)));
  const cards = await dossier.locator("article").count();
  const rows = await dossier.locator("article dt").allInnerTexts();
  check("every runtime card answers the five rows", cards >= 12 && rows.length === cards * 5, `${cards} cards, ${rows.length} rows`);
  check("the dossier's diagram says itself in words", ((await dossier.getByRole("img").first().getAttribute("aria-label")) ?? "").startsWith("Data flow."));
  for (let i = 0; i < 4; i++) await page.keyboard.press("Shift+Tab");
  check("Tab stays inside the dossier", await dossier.evaluate((el) => el.contains(document.activeElement)));
  await page.keyboard.press("Escape");
  await page.waitForTimeout(200);
  check("one Escape closes only the dossier", (await page.getByRole("dialog").count()) === 1 && (await trust.isVisible()));
  check("focus returns to the button that opened it", await notConvinced.evaluate((el) => el === document.activeElement));
  await page.keyboard.press("Escape");
  await page.waitForTimeout(200);
  check("a second Escape closes Why trust it", (await page.getByRole("dialog").count()) === 0);

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
  await about.getByRole("button", { name: "Save a diagnostic bundle" }).click();
  check(
    "Save a diagnostic bundle says where it went",
    await about
      .getByText("Saved spec-surgeon-diagnostics-mock.zip (12 files); its folder is open.")
      .waitFor({ timeout: 5000 })
      .then(() => true)
      .catch(() => false),
  );
  const sentReports = (await (await fetch(`${BASE}/mock/page-reports`)).json()).reports;
  check(
    "the page reported its load to the app",
    sentReports.some((r) => r.kind === "loaded" && typeof r.user_agent === "string" && r.visible === true),
    JSON.stringify(sentReports.map((r) => r.kind).slice(0, 6)),
  );
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

  // 13. The one button lives in the conversation pane; the workbench carries none, and no plan card; there is no Hide plan button.
  await scenario("preview");
  await page.reload();
  const createBtn = page.getByRole("button", { name: "Create edited copies" });
  await createBtn.waitFor();
  check("no Hide plan button", (await page.getByRole("button", { name: /Hide plan|Show plan/ }).count()) === 0);
  check("the action button is in the conversation pane", (await page.locator("section[aria-label='Conversation']").getByRole("button", { name: "Create edited copies" }).count()) === 1);
  check("the workbench has no button", (await page.locator("section[aria-label='Your documents and the pages']").getByRole("button", { name: "Create edited copies" }).count()) === 0);
  check("no plan card", (await page.getByText("Here’s what will change").count()) === 0);

  // 14. Markdown renders in both panes (the chat's reply and the findings on the pages).
  check("the chat renders markdown", (await page.locator("section[aria-label='Conversation'] .md").count()) > 0);
  check("the findings render markdown", (await page.locator(".paper .md").count()) > 0);

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
  check("the to-do card says why it waits", (await todoCard.getByText("You marked its preview as needing more work.", { exact: true }).count()) === 1);
  check("…and so does the card under the copies", (await page.locator("section[aria-label='Set aside for more work']").getByText("You marked its preview as needing more work.", { exact: true }).count()) === 1);
  check("each waiting document can be put back", await page.getByRole("button", { name: /Put back 21 05 00/ }).isVisible());
  check("the copy that looked right is listed", (await page.locator("section[aria-label='Your edited copies']").getByText("21 13 13", { exact: false }).count()) === 1);
  check("the set-aside document got no copy", (await page.locator("section[aria-label='Your edited copies']").getByText("21 05 00", { exact: false }).count()) === 0);
  if ((await trayToggle.getAttribute("aria-expanded")) === "false") await trayToggle.click();
  check("the tray tags it", await page.getByText("To do", { exact: true }).first().isVisible());
  await page.getByRole("button", { name: "Work on it now" }).click();
  await page.getByText(/Working on 21 05 00/).waitFor({ timeout: 20000 });
  await page.waitForTimeout(300);
  check("the pass over the set-aside document begins with Continue", await page.getByRole("button", { name: "Continue" }).first().isVisible());
  check("the to-do card is gone during the pass", (await page.getByRole("button", { name: "Work on it now" }).count()) === 0);

  // 16. Put back takes a document off the to-do list without a pass; it is in scope again.
  await scenario("todo");
  await page.reload();
  const putBack = page.getByRole("button", { name: /Put back / });
  await putBack.waitFor({ timeout: 20000 });
  await putBack.click();
  await page.getByText(/is off your to-do list and back among the documents/).waitFor({ timeout: 20000 });
  check("Put back empties the to-do list", (await page.locator("section[aria-label='Set aside for more work']").count()) === 0);
  check("…and the chat's to-do card with it", (await page.getByRole("button", { name: "Work on it now" }).count()) === 0);

  // 17. Safe mode: the switch, the buttons in place of the message box, a button's form → preview → copies, and the
  // hand-off of what it left untouched to Default mode.
  await scenario("attached");
  await page.reload();
  const modes = page.getByRole("group", { name: "Mode" });
  await modes.getByRole("button", { name: "Safe" }).click();
  await page.getByRole("region", { name: "Safe mode" }).waitFor({ timeout: 5000 });
  check("Safe mode puts the buttons where the message box was", (await page.getByLabel("Describe a change or answer a question").count()) === 0);
  check("…and says no AI is used", await page.getByText("No AI in Safe mode").isVisible());
  await page.getByRole("button", { name: "Match body formatting to template" }).click();
  check("the body button's form starts from the template's roles",
    (await page.getByRole("combobox", { name: "PART", exact: true }).inputValue()) === "PRT" &&
    (await page.getByRole("combobox", { name: "Section title" }).inputValue()) === "TTL");
  check("…says what it read from the template", await page.getByText("From your template: pages 8.5 × 11 in portrait").isVisible());
  check("…and can be previewed as it is", await page.getByRole("button", { name: "Preview", exact: true }).isEnabled());
  await page.getByRole("button", { name: "All buttons" }).click();
  await page.getByRole("button", { name: "Find and replace" }).click();
  const previewBtn = page.getByRole("button", { name: "Preview", exact: true });
  check("an empty form cannot be previewed", await previewBtn.isDisabled());
  await page.getByLabel("Find, row 1").fill("ACME");
  await page.getByLabel("Replace with, row 1").fill("Contoso");
  check("a filled form can", await previewBtn.isEnabled());
  await previewBtn.click();
  await page.getByRole("button", { name: "Create edited copies" }).waitFor({ timeout: 20000 });
  check("a button previews with no conversation", (await page.locator("section[aria-label='Left untouched']").count()) === 1);
  await page.getByRole("button", { name: "Create edited copies" }).click();
  const handOff = page.getByRole("button", { name: "Try these in Default mode" });
  await handOff.waitFor({ timeout: 20000 });
  check("what the button left untouched waits beside the copies", await page.getByText("Your edited copies").isVisible());
  await handOff.click();
  await page.getByLabel("Describe a change or answer a question").waitFor({ timeout: 5000 });
  check("the hand-off switches to Default mode", (await modes.getByRole("button", { name: "Default" }).getAttribute("aria-pressed")) === "true");
  check("…with the document on the to-do list", await page.getByRole("button", { name: "Work on it now" }).isVisible());

  // 17. Obliterate mode: hidden until Settings → Advanced shows it (a second click), its warning every time it is
  // chosen (the box ticked before the button works), the top bar and the message box say so, Back to Default leaves
  // it, and a copy delivered with a failed check carries the UNVERIFIED mark.
  check("Obliterate is hidden at first", (await modes.getByRole("button", { name: "Obliterate" }).count()) === 0);
  await page.getByRole("button", { name: "Settings" }).click();
  const showSwitch = page.getByRole("switch", { name: "Show Obliterate mode" });
  check("Settings → Advanced has the switch, off", (await showSwitch.getAttribute("aria-checked")) === "false");
  await showSwitch.click();
  const showIt = page.getByRole("button", { name: "Show it", exact: true });
  check("showing it asks first", (await showIt.isVisible()) && (await showSwitch.getAttribute("aria-checked")) === "false");
  await showIt.click();
  await page.waitForFunction(() => document.querySelector("[role=switch][aria-label='Show Obliterate mode']")?.getAttribute("aria-checked") === "true");
  await page.keyboard.press("Escape");
  const obliterate = modes.getByRole("button", { name: "Obliterate" });
  await obliterate.waitFor({ timeout: 5000 });
  check("the top bar offers it once shown", await obliterate.isVisible());
  await obliterate.click();
  const warning = page.getByRole("dialog").filter({ hasText: "What it grants" });
  await warning.waitFor({ timeout: 5000 });
  const turnOn = warning.getByRole("button", { name: "Turn on Obliterate" });
  check("its warning names what it grants, costs and keeps",
    (await warning.getByText("What it costs").isVisible()) && (await warning.getByText("What still holds").isVisible()));
  check("…and the button waits for the box", await turnOn.isDisabled());
  await warning.getByRole("button", { name: "Stay in this mode" }).click();
  check("Stay keeps the mode", (await modes.getByRole("button", { name: "Default" }).getAttribute("aria-pressed")) === "true");
  await obliterate.click();
  await warning.waitFor({ timeout: 5000 });
  check("the warning shows again, unticked", (await turnOn.isDisabled()) && !(await warning.getByRole("checkbox").isChecked()));
  await warning.getByRole("checkbox").check();
  await turnOn.click();
  const strip = page.getByRole("note", { name: "Obliterate mode" });
  await strip.waitFor({ timeout: 5000 });
  check("Obliterate is on: the switch and the message box say so",
    (await obliterate.getAttribute("aria-pressed")) === "true" && (await page.getByText(/^Obliterate mode is on\./).isVisible()));
  await strip.getByRole("button", { name: "Back to Default" }).click();
  await page.getByText(/^Obliterate mode is off/).waitFor({ timeout: 5000 });
  check("Back to Default leaves it", (await modes.getByRole("button", { name: "Default" }).getAttribute("aria-pressed")) === "true");
  await scenario("obliterate-done");
  await page.reload();
  await page.getByText("Your edited copies").waitFor({ timeout: 5000 });
  check("a copy delivered with a failed check is marked UNVERIFIED",
    (await page.getByText("UNVERIFIED", { exact: true }).count()) === 1 &&
      (await page.getByText("UNVERIFIED — V2 (Text preservation) failed. Check it in Word before you use it.").isVisible()));
  await page.getByRole("button", { name: "Settings" }).click();
  await showSwitch.click();
  await page.waitForFunction(() => document.querySelector("[role=switch][aria-label='Show Obliterate mode']")?.getAttribute("aria-checked") === "false");
  await page.keyboard.press("Escape");
  await page.getByLabel("Describe a change or answer a question").waitFor({ timeout: 5000 });
  check("hiding it again leaves the mode", (await modes.getByRole("button", { name: "Obliterate" }).count()) === 0);

  check("no page errors", errors.length === 0, errors.join(" | ").slice(0, 300));
  await browser.close();
} catch (failure) {
  results.push(`CRASH ${failure}`);
} finally {
  mock.kill();
}
console.log(results.join("\n"));
process.exit(results.some((line) => !line.startsWith("PASS")) ? 1 : 0);
