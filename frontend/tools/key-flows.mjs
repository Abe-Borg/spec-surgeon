/** Key status/removal regressions against a local fake store; no provider request or real credential is used. */
import assert from "node:assert/strict";
import { launch, startMock } from "./browser.mjs";

const TOKEN = "key-flow-token";
const mock = await startMock({ port: Number(process.env.KEY_FLOW_PORT || 8813), token: TOKEN, delay: "5" });
let browser;
try {
  browser = await launch();
  const page = await browser.newPage();
  const external = [], errors = [];
  page.on("request", (r) => { if (!r.url().startsWith(mock.base) && !r.url().startsWith("data:")) external.push(r.url()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  await mock.scenario("no-key");
  await page.goto(`${mock.base}/?token=${TOKEN}`);
  await page.getByRole("button", { name: "Add your API key", exact: true }).click();
  await page.getByLabel("Anthropic API key").fill("fake-unvalidated-key");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await page.getByText(/The key is saved.*It has not been tested with Anthropic\./).waitFor();
  assert.equal(await page.getByText("Connected", { exact: true }).count(), 0);
  assert.equal(await page.getByRole("button", { name: "Key available", exact: true }).count(), 1);
  const remove = page.getByRole("button", { name: "Remove app key", exact: true });
  await remove.waitFor();
  console.log("PASS saving an unvalidated key reports availability without claiming a tested connection");

  await page.route("**/api/settings/remove-key", (route) => route.fulfill({
    status: 400, contentType: "application/json", body: JSON.stringify({ error: "The saved key could not be removed securely." }),
  }));
  await remove.click();
  await page.getByRole("alert").filter({ hasText: "could not be removed" }).waitFor();
  assert.equal(await remove.isEnabled(), true);
  assert.equal(await page.getByRole("button", { name: "Key available", exact: true }).count(), 1);
  await page.unroute("**/api/settings/remove-key");
  await remove.click();
  await page.getByText("The app's key was removed.", { exact: true }).waitFor();
  assert.equal(await remove.count(), 0);
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Add your API key", exact: true }).waitFor();
  console.log("PASS failed deletion remains available; successful deletion updates Settings and the header");

  await mock.scenario("environment-key");
  await page.reload();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByText("Credentials from the environment or SDK profile are managed outside this app.").waitFor();
  assert.equal(await remove.count(), 0);
  assert.deepEqual(external, []);
  assert.deepEqual(errors, []);
  console.log("PASS external credentials have no removal control; no provider requests or page errors");
} finally {
  await browser?.close();
  mock.kill();
}
