/** Browser regressions for per-mode drafts and explicit conversation resets. Uses the local mock only. */
import assert from "node:assert/strict";
import { launch, startMock } from "./browser.mjs";

const TOKEN = "lifecycle-token";
const mock = await startMock({ port: Number(process.env.LIFECYCLE_PORT || 8812), token: TOKEN, delay: "5" });
let browser;
try {
  browser = await launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  const mode = (name) => page.getByRole("group", { name: "Mode" }).getByRole("button", { name, exact: true });
  const box = page.getByLabel("Describe a change or answer a question");
  const watermark = page.getByPlaceholder("NOT FOR CONSTRUCTION");
  const create = page.getByRole("button", { name: /^Create \d+ edited cop(?:y|ies)$/ });
  const load = async (scenario) => {
    await mock.scenario(scenario);
    await page.goto(`${mock.base}/?token=${TOKEN}`);
    await mode("Safe").waitFor();
  };

  await load("preview");
  await box.fill("Keep this unsent request");
  await mode("Safe").click();
  await page.getByRole("button", { name: "Add or remove a watermark", exact: true }).click();
  await watermark.fill("DRAFT2");
  await mode("Default").click();
  await box.waitFor();
  assert.equal(await box.inputValue(), "Keep this unsent request");
  assert.equal(await create.isDisabled(), true);
  await mode("Safe").click();
  await watermark.waitFor();
  assert.equal(await watermark.inputValue(), "DRAFT2");
  assert.equal(await create.isEnabled(), true, "a hidden Default draft must not hold Safe Create");
  console.log("PASS each mode preserves its input, and only the visible draft holds Create");

  const reset = page.getByRole("button", { name: "New conversation", exact: true });
  await page.route("**/api/new-conversation", (route) => route.fulfill({
    status: 503, contentType: "application/json", body: JSON.stringify({ error: "reset unavailable" }),
  }));
  await reset.click();
  await page.getByRole("status").filter({ hasText: "reset unavailable" }).waitFor();
  assert.equal(await watermark.inputValue(), "DRAFT2", "a failed reset must keep the user's input");
  await page.unroute("**/api/new-conversation");
  await reset.click();
  await page.getByRole("button", { name: "Find and replace", exact: true }).waitFor();
  assert.equal(await watermark.count(), 0);
  assert.equal(await create.count(), 0);
  assert.equal(await mode("Safe").getAttribute("aria-pressed"), "true");
  await mode("Default").click();
  await box.waitFor();
  assert.equal(await box.inputValue(), "");
  console.log("PASS New conversation clears both modes only after the server accepts the reset");

  // A saved recipe's run form open and its run refused: New conversation takes both away, as the owner saw they did not.
  await load("safe");
  await page.getByRole("region", { name: "My recipes" }).getByRole("button", { name: /Division 21/ }).click();
  const runForm = page.getByLabel("Division 21 · Issued for Construction", { exact: true });
  await runForm.waitFor();
  await runForm.getByRole("checkbox", { name: "Run" }).first().uncheck(); // its template's slots are not filled in
  const refused = "a file or the plan changed since the preview — check the documents again";
  await page.route("**/api/saved-recipes/run", (route) => route.fulfill({
    status: 409, contentType: "application/json", body: JSON.stringify({ error: refused }),
  }));
  await runForm.getByRole("button", { name: /^Preview / }).click();
  await page.getByText(refused).waitFor();
  await page.unroute("**/api/saved-recipes/run");
  await reset.click();
  await page.getByRole("button", { name: "Find and replace", exact: true }).waitFor();
  assert.equal(await runForm.count(), 0, "New conversation closes a saved recipe's run form");
  assert.equal(await page.getByText(refused).count(), 0, "New conversation clears the last job's failure");
  console.log("PASS New conversation closes a saved recipe's run form and clears the last failure");

  await load("safe-done");
  await page.getByRole("button", { name: "Add or remove a watermark", exact: true }).click();
  await watermark.fill("DRAFT3");
  await page.getByRole("button", { name: "Start over from the originals", exact: true }).click();
  await page.getByRole("button", { name: "Find and replace", exact: true }).waitFor();
  assert.equal(await watermark.count(), 0);
  assert.equal(await page.getByRole("heading", { name: "Your edited copies", exact: true }).count(), 0);
  await page.getByText("Starting over from the originals. Pick a button to start.", { exact: true }).waitFor();
  const state = await (await page.request.get(`${mock.base}/api/state`, {
    headers: { "X-Spec-Surgeon-Token": TOKEN },
  })).json();
  assert.equal(state.working.from_originals, true);
  assert.ok(state.attachments.some((a) => a.kind === "document"), "Start over keeps the attached originals");
  console.log("PASS Start over clears the staged form and results, keeps originals and uses Safe-mode copy");

  await load("safe-done");
  const done = page.getByRole("region", { name: "What you've done so far" }).getByRole("listitem");
  await done.first().waitFor();
  assert.equal(await done.count(), 1);
  await page.getByRole("button", { name: /^Remove all comments/ }).click();
  await page.getByRole("button", { name: "Preview", exact: true }).click();
  await create.click();
  await done.nth(1).waitFor({ timeout: 20000 });
  assert.match(await done.nth(1).innerText(), /Remove all comments[\s\S]*LATEST/);
  await page.getByRole("button", { name: "All buttons", exact: true }).click();
  await page.getByRole("img", { name: "You ran it once on these copies (What you've done so far)." }).first().waitFor();
  assert.equal(await page.getByRole("img", { name: /^You ran it once/ }).count(), 2, "both buttons carry their checkmark");
  await page.getByRole("button", { name: "Undo the last button", exact: true }).click();
  await page.getByText("Undid “Remove all comments”.", { exact: false }).waitFor();
  assert.equal(await done.count(), 1);
  assert.equal(await page.getByRole("img", { name: /^You ran it once/ }).count(), 1, "Undo takes the checkmark off");
  console.log("PASS What you've done so far lists each button, marks it, and Undo takes the newest off");

  // Saved recipes: the history saved under a name, run again as one preview, one entry in the list, one Undo.
  await load("safe-done");
  await page.getByRole("button", { name: /Save as a recipe/ }).click();
  const saveDialog = page.getByRole("dialog");
  await saveDialog.getByLabel("Name").fill("Clean copies");
  assert.equal(await saveDialog.getByRole("checkbox").count(), 1);
  await saveDialog.getByRole("button", { name: "Save recipe" }).click();
  const mine = page.getByRole("region", { name: "My recipes" });
  await mine.getByRole("button", { name: /Clean copies/ }).click();
  await page.getByText("RECIPE · 1 STEP", { exact: false }).waitFor();
  await page.getByRole("button", { name: "Preview 1 step" }).click();
  await create.click();
  await done.nth(1).waitFor({ timeout: 20000 });
  assert.match(await done.nth(1).innerText(), /Recipe[\s\S]*Clean copies[\s\S]*LATEST/);
  await page.getByRole("button", { name: "Undo the last button", exact: true }).click();
  await page.getByText("Undid", { exact: false }).last().waitFor();
  assert.equal(await page.getByRole("region", { name: "What you've done so far" }).getByText("Clean copies").count(), 0);
  console.log("PASS a saved recipe runs as one preview, lists as one entry, and Undo takes it off");

  await load("done");
  await page.getByRole("button", { name: /All recipes/ }).click();
  await page.getByRole("dialog", { name: "All recipes" }).getByRole("button", { name: /Riverside closeout/ }).click();
  await page.getByRole("button", { name: "Preview 1 step" }).click();
  await page.getByRole("button", { name: /^Create \d+ edited cop(?:y|ies)$/ }).waitFor({ timeout: 20000 });
  assert.ok(await page.getByText("Run the recipe “Riverside closeout: NFPA 13 2025 edition”").count());
  await page.getByRole("button", { name: /All recipes/ }).click();
  await page.getByRole("button", { name: "Manage recipes…" }).click();
  const manage = page.getByRole("dialog", { name: "Your recipes" });
  await manage.getByRole("button", { name: "Duplicate" }).first().click();
  await manage.getByText("(copy)", { exact: false }).waitFor();
  console.log("PASS Default mode runs a recipe from beside the message box and manages them");

  // Edit: a recipe's steps reordered and one removed; the list then shows the edit.
  await manage.getByRole("button", { name: "Edit" }).first().click();
  const editor = manage.getByRole("form", { name: /^Edit / });
  await editor.getByLabel("Name").fill("IFC, comments first");
  await editor.getByRole("button", { name: "Move step 3 up" }).click();
  await editor.getByRole("button", { name: "Remove" }).nth(2).click();
  await editor.getByText("Removed when you save").waitFor();
  if (process.env.SHOTS) await page.screenshot({ path: `${process.env.SHOTS}/recipe-editor.png` });
  await editor.getByRole("button", { name: "Save" }).click();
  await manage.getByRole("button", { name: /IFC, comments first/ }).click();
  const listed = await manage.locator("ol").first().innerText();
  assert.match(listed, /1\s*Match headers[\s\S]*2\s*Remove all comments/);
  assert.doesNotMatch(listed, /Accept all/);
  await manage.getByRole("button", { name: "Close" }).last().click();
  console.log("PASS Edit reorders and removes a recipe's steps");

  // Add to a saved recipe: the history's changes after a recipe's own steps.
  await page.getByRole("button", { name: /Save as a recipe/ }).click();
  const addDialog = page.getByRole("dialog", { name: "Save as a recipe" });
  await addDialog.getByLabel("Add to a saved recipe").check();
  await addDialog.getByLabel("The recipe to add them to").selectOption({ label: "IFC, comments first · 2 steps · SAFE" });
  if (process.env.SHOTS) await page.screenshot({ path: `${process.env.SHOTS}/recipe-add-to.png` });
  await addDialog.getByRole("button", { name: "Add to recipe" }).click();
  await page.getByText("to the recipe “IFC, comments first”", { exact: false }).waitFor();
  console.log("PASS Save as a recipe adds the changes to a saved recipe");

  assert.deepEqual(errors, []);
  console.log("PASS no lifecycle browser errors");
} finally {
  await browser?.close();
  mock.kill();
}
