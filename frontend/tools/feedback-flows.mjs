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
  await mock.scenario("failed-preview");
  await page.goto(`${mock.base}/?token=${TOKEN}`);
  const failures = page.getByRole("list", { name: /^Failed checks for / });
  await failures.waitFor();
  assert.equal(await failures.count(), 1);
  assert.match(await failures.innerText(), /wording does not match.*\(V2\)/s);
  assert.equal(await page.getByRole("button", { name: "Create edited copies", exact: true }).isDisabled(), true);
  assert.ok(await page.getByText("Checked", { exact: true }).count() > 0);
  assert.deepEqual(errors, []);
  console.log("PASS the failed document names its check and meaning; passing documents keep their checked state");
  console.log("PASS Create stays blocked and there are no feedback browser errors");
} finally {
  await browser?.close();
  mock.kill();
}
