/**
 * Where the page finds its server and token (lib/connection.ts): the shell's
 * injection wins, the query string is the dev fallback, the base is the
 * page's own origin otherwise, and images carry the token as a parameter.
 */
import assert from "node:assert/strict";
import test from "node:test";

import { TOKEN_HEADER, apiHeaders, apiUrl, imageSrc, normalizeApiBase, resolveConnection } from "../src/lib/connection.ts";

test("the shell's injection wins over the query string", () => {
  const setup = resolveConnection({ token: "shell-token", version: "1.6.0", api: "http://127.0.0.1:9001/" }, "?token=url-token&api=http://x");
  assert.equal(setup.token, "shell-token");
  assert.equal(setup.apiBase, "http://127.0.0.1:9001");
  assert.equal(setup.shellVersion, "1.6.0");
});

test("the dev page reads token and api from the query string", () => {
  const setup = resolveConnection(undefined, "?token=abc&api=http%3A%2F%2F127.0.0.1%3A8765");
  assert.equal(setup.token, "abc");
  assert.equal(setup.apiBase, "http://127.0.0.1:8765");
  assert.equal(setup.shellVersion, "");
});

test("without either, there is no token and the base is the page's own origin", () => {
  const setup = resolveConnection(null, "");
  assert.deepEqual(setup, { token: "", apiBase: "", shellVersion: "" });
  assert.equal(apiUrl(setup, "/api/state"), "/api/state");
});

test("a shell that injects only the token keeps a same-origin base", () => {
  const setup = resolveConnection({ token: "t" }, "?api=http://127.0.0.1:1");
  assert.equal(setup.token, "t");
  // The URL's api still applies: the shell did not say otherwise.
  assert.equal(setup.apiBase, "http://127.0.0.1:1");
});

test("non-string injections are ignored", () => {
  const setup = resolveConnection({ token: 42, api: ["x"] }, "?token=  spaced  ");
  assert.equal(setup.token, "spaced");
  assert.equal(setup.apiBase, "");
});

test("the base loses trailing slashes and routes get exactly one", () => {
  assert.equal(normalizeApiBase("http://h:1///"), "http://h:1");
  assert.equal(normalizeApiBase("   "), "");
  assert.equal(apiUrl({ apiBase: "http://h:1" }, "api/state"), "http://h:1/api/state");
});

test("every /api request carries the token header when there is one", () => {
  assert.deepEqual(apiHeaders({ token: "t" }, { "Content-Type": "application/json" }), {
    "Content-Type": "application/json",
    [TOKEN_HEADER]: "t",
  });
  assert.deepEqual(apiHeaders({ token: "" }), {});
  assert.equal(TOKEN_HEADER, "X-Spec-Surgeon-Token");
});

test("an image URL from the view is rebased and gets the token as a query parameter", () => {
  const setup = { token: "t", apiBase: "http://h:1", shellVersion: "" };
  assert.equal(imageSrc(setup, "/api/image?path=C%3A%2Fruns%2F1.png"), "http://h:1/api/image?path=C%3A%2Fruns%2F1.png&token=t");
  assert.equal(imageSrc(setup, "/api/image?path=x&token=already"), "http://h:1/api/image?path=x&token=already");
  assert.equal(imageSrc({ ...setup, apiBase: "" }, "/api/image?path=x"), "/api/image?path=x&token=t");
  assert.equal(imageSrc(setup, "https://elsewhere/p.png"), "https://elsewhere/p.png");
  assert.equal(imageSrc(setup, "data:image/png;base64,AA"), "data:image/png;base64,AA");
  assert.equal(imageSrc(setup, ""), "");
});
