#!/usr/bin/env node
/**
 * Drives the built page through the mock's jobs and routes in a real
 * browser — the paths the screenshots do not show: a failed job, a pause
 * and its Continue, Stop now mid-job, typing ahead, a chip click, the
 * draft hold on the action button, the file-chooser fallback, a drop,
 * remove, no key → Settings, About, the update banner, start over.
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

  // 6. Add Word documents → 501 → the browser's file input → upload; the plan is void.
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

  // 10. Help → About.
  await page.getByRole("button", { name: "Help ▾" }).click();
  await page.getByRole("menuitem", { name: "About Spec Surgeon" }).click();
  await page.getByText("Release notes").waitFor();
  check("About shows the release notes", await page.getByText("The window is a web page").isVisible({ timeout: 3000 }).catch(() => false));
  await page.getByRole("tab", { name: "License" }).click();
  check("About shows the license", await page.getByText("MIT License").first().isVisible());
  await page.keyboard.press("Escape");

  // 11. Updates: check → available → download → ready.
  await scenario("attached");
  await page.reload();
  await page.getByRole("button", { name: "Help ▾" }).click();
  await page.getByRole("menuitem", { name: "Check for updates" }).click();
  await page.getByText("is available").waitFor({ timeout: 15000 });
  await page.getByRole("button", { name: "Download" }).click();
  await page.getByText("Downloading Spec Surgeon").waitFor({ timeout: 5000 });
  await page.getByRole("button", { name: "Restart to update" }).waitFor({ timeout: 15000 });
  check("the update banner walks available → downloading → ready", true);

  // 12. Copies and Start over.
  await scenario("done");
  await page.reload();
  await page.getByText("Your edited copies").waitFor();
  check("Start over from the originals is offered after a run", await page.getByRole("button", { name: "Start over from the originals" }).isVisible());
  await page.getByRole("button", { name: "Start over from the originals" }).click();
  await page.waitForTimeout(400);
  check("Start over clears the copies", (await page.getByText("Your edited copies").count()) === 0);

  check("no page errors", errors.length === 0, errors.join(" | ").slice(0, 300));
  await browser.close();
} catch (failure) {
  results.push(`CRASH ${failure}`);
} finally {
  mock.kill();
}
console.log(results.join("\n"));
process.exit(results.some((line) => !line.startsWith("PASS")) ? 1 : 0);
