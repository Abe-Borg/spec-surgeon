/** Failed preview explanations stay with the document whose check failed. Local mock; no provider calls. */
import assert from "node:assert/strict";
import { launch, startMock } from "./browser.mjs";

const TOKEN = "feedback-token";
const mock = await startMock({ port: Number(process.env.FEEDBACK_PORT || 8814), token: TOKEN, delay: "5" });
let browser;
try {
  browser = await launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  // One preview of two did not pass: it is left untouched and Create makes the other's copy.
  await mock.scenario("failed-preview");
  await page.goto(`${mock.base}/?token=${TOKEN}`);
  const failures = page.getByRole("list", { name: /^Failed checks for / });
  await failures.waitFor();
  assert.equal(await failures.count(), 1);
  assert.match(await failures.innerText(), /wording does not match.*\(V2\)/s);
  assert.ok(await page.getByText("Checked", { exact: true }).count() > 0);
  const create = page.getByRole("button", { name: "Create edited copies", exact: true });
  assert.equal(await create.isDisabled(), false);
  assert.equal(await page.getByRole("group", { name: /^Review / }).count(), 1);   // the failed preview carries no marks
  await page.getByText("Left untouched: it did not pass its checks", { exact: false }).waitFor();
  await page.getByText("One document whose preview did not pass gets no copy", { exact: false }).waitFor();
  assert.match(await page.getByRole("region", { name: "Your documents and the pages" }).innerText(),
               /Left untouched[\s\S]*Its preview did not pass its checks/);
  console.log("PASS the failed document names its check and meaning; passing documents keep their checked state");
  console.log("PASS one failed preview of two is left untouched and Create stays available for the other");

  // Every preview did not pass: Create is unavailable and says why.
  await mock.scenario("failed-every-preview");
  await page.reload();
  await page.getByRole("list", { name: /^Failed checks for / }).first().waitFor();
  assert.equal(await page.getByRole("list", { name: /^Failed checks for / }).count(), 2);
  assert.equal(await page.getByRole("button", { name: "Create edited copies", exact: true }).isDisabled(), true);
  await page.getByText("Create is unavailable:", { exact: false }).first().waitFor();
  assert.deepEqual(errors, []);
  console.log("PASS Create stays blocked when no preview passed, and there are no feedback browser errors");
} finally {
  await browser?.close();
  mock.kill();
}
