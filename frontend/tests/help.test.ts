/**
 * The top bar's small rules (lib/help.ts): the topics, the line beside the
 * title, the model's name, the About page's update line and the update pill.
 */
import assert from "node:assert/strict";
import test from "node:test";

import type { Attachment, Update, Working } from "../src/types.ts";
import {
  HELP_TITLES,
  HELP_TOPICS,
  checkSettled,
  modelLabel,
  releaseNotesBody,
  sessionHeading,
  updatePill,
  updateStatus,
} from "../src/lib/help.ts";

const file = (name: string, kind: Attachment["kind"] = "document", state: Attachment["state"] = "ok"): Attachment => ({
  path: `C:/specs/${name}`,
  name,
  kind,
  state,
  notes: [],
  reason: "",
  current: "",
});
const originals: Working = { generation: 0, from_originals: true, can_start_over: false };
const update = (overrides: Partial<Update> = {}): Update => ({
  state: "none",
  version: "",
  notes: "",
  progress: 0,
  error: "",
  ...overrides,
});

test("the top bar names four topics, Why trust it not among them yet, each with a title", () => {
  assert.deepEqual(
    HELP_TOPICS.map((t) => t.label),
    ["How to use", "Workflows", "How it works", "About"],
  );
  for (const t of HELP_TOPICS) assert.ok(HELP_TITLES[t.id], t.id);
  assert.equal(new Set(HELP_TOPICS.map((t) => t.id)).size, HELP_TOPICS.length);
});

test("the line beside the title counts what the conversation works on", () => {
  assert.equal(sessionHeading({ attachments: [], working: originals }), "");
  assert.equal(sessionHeading({ attachments: [file("a.docx")], working: originals }), "1 document");
  assert.equal(
    sessionHeading({
      attachments: [
        file("a.docx"),
        file("b.docx"),
        file("c.docx"),
        file("Office master.docx", "template"),
        file("logo.png", "reference"),
        file("values.xlsx", "reference"),
        file("more.csv", "reference"),
      ],
      working: originals,
    }),
    "3 documents · 1 template · 1 picture · 2 tables",
  );
});

test("files triage refused are not counted, and a template alone says nothing", () => {
  assert.equal(
    sessionHeading({ attachments: [file("a.docx"), file("old.docx", "document", "refused")], working: originals }),
    "1 document",
  );
  assert.equal(sessionHeading({ attachments: [file("t.docx", "template")], working: originals }), "");
  assert.equal(
    sessionHeading({ attachments: [file("a.docx"), file("t.docx", "template", "refused")], working: originals }),
    "1 document",
  );
});

test("after a run the line says the next request builds on the copies", () => {
  assert.equal(
    sessionHeading({
      attachments: [file("a.docx"), file("b.docx")],
      working: { generation: 1, from_originals: false, can_start_over: true },
    }),
    "2 documents · building on your copies",
  );
  // Started over: back on the originals, whatever the generation.
  assert.equal(
    sessionHeading({ attachments: [file("a.docx")], working: { generation: 2, from_originals: true, can_start_over: true } }),
    "1 document",
  );
});

test("a Claude model id reads as its name; anything else as given", () => {
  assert.equal(modelLabel("claude-opus-5-5"), "Claude Opus 5.5");
  assert.equal(modelLabel("claude-sonnet-5-5-20261001"), "Claude Sonnet 5.5");
  assert.equal(modelLabel("claude-haiku-5-5@latest"), "Claude Haiku 5.5");
  assert.equal(modelLabel("some-other-model"), "some-other-model");
  assert.equal(modelLabel("claude-opus-5-55x"), "claude-opus-5-55x");
  assert.equal(modelLabel(""), "");
  assert.equal(modelLabel(undefined), "");
});

test("a check settles on an answer or a found update, never on an earlier outcome", () => {
  assert.equal(checkSettled(null), false);
  assert.equal(checkSettled(update()), false);
  assert.equal(checkSettled(update({ manual_result: "You're up to date — Spec Surgeon 1.6.0 is the newest version." })), true);
  for (const state of ["available", "downloading", "ready", "installing"] as const) {
    assert.equal(checkSettled(update({ state, version: "1.7.0" })), true, state);
  }
  for (const state of ["updated", "failed", "error"] as const) {
    assert.equal(checkSettled(update({ state, version: "1.6.0" })), false, state);
  }
});

test("About shows a found update with the banner's own button", () => {
  assert.deepEqual(updateStatus(update({ state: "available", version: "1.7.0" }), false, false), {
    text: "Version 1.7.0 is available.",
    tone: "accent",
    action: "download",
    progress: null,
  });
  assert.deepEqual(updateStatus(update({ state: "downloading", version: "1.7.0", progress: 41.6 }), true, false), {
    text: "Downloading version 1.7.0… 42%",
    tone: "accent",
    action: null,
    progress: 42,
  });
  assert.equal(updateStatus(update({ state: "ready", version: "1.7.0" }), false, false).action, "install");
  assert.equal(updateStatus(update({ state: "installing", version: "1.7.0" }), false, false).text, "Closing to install version 1.7.0…");
});

test("About says 'checking' until its own check answers, then the answer", () => {
  const answer = "You're up to date — Spec Surgeon 1.6.0 is the newest version.";
  assert.equal(updateStatus(update(), true, true).text, "Checking for updates…");
  assert.deepEqual(updateStatus(update({ manual_result: answer }), true, false), {
    text: answer,
    tone: "quiet",
    action: null,
    progress: null,
  });
  const failed = updateStatus(update({ manual_result: "Couldn't check for updates: offline" }), true, false);
  assert.equal(failed.tone, "err");
  // An answer to a check asked for before this page opened is not passed off as this one's.
  assert.equal(updateStatus(update({ manual_result: answer }), false, false).text, "");
  // A check that never answered says so instead of spinning on.
  assert.match(updateStatus(update(), true, false).text, /not answered yet/);
  assert.equal(updateStatus(update(), false, false).text, "");
});

test("an earlier outcome shows until a check this page asked for answers", () => {
  const updated = update({ state: "updated", version: "1.6.0" });
  assert.deepEqual(updateStatus(updated, false, false), { text: "Updated to version 1.6.0.", tone: "ok", action: null, progress: null });
  assert.equal(updateStatus(updated, true, true).text, "Checking for updates…");
  assert.equal(updateStatus({ ...updated, manual_result: "You're up to date." }, true, false).text, "You're up to date.");
  assert.equal(updateStatus(update({ state: "failed", version: "1.7.0" }), false, false).tone, "err");
  assert.equal(updateStatus(update({ state: "error", error: "checksum mismatch" }), false, false).text, "The update was not installed: checksum mismatch");
});

test("the update pill keeps a set-aside update one click away, and only that", () => {
  const available = update({ state: "available", version: "1.7.0" });
  assert.equal(updatePill(available, true), "");
  assert.equal(updatePill(available, false), "v1.7.0 available");
  assert.equal(updatePill(update({ state: "available" }), false), "Update available");
  for (const state of ["none", "downloading", "ready", "installing", "updated", "failed", "error"] as const) {
    assert.equal(updatePill(update({ state, version: "1.7.0" }), false), "", state);
  }
  assert.equal(updatePill(null, false), "");
});

test("the release notes lose their own leading title, and nothing else", () => {
  assert.equal(
    releaseNotesBody("# Spec Surgeon 1.6.0\n\nIncludes PR #46.\n\n## The window\n\n- One.\n"),
    "Includes PR #46.\n\n## The window\n\n- One.",
  );
  assert.equal(releaseNotesBody("## Spec Surgeon 1.6.0\n\n- The window is a web page."), "- The window is a web page.");
  assert.equal(releaseNotesBody("Plain notes.\n\n# Later heading"), "Plain notes.\n\n# Later heading");
  assert.equal(releaseNotesBody("#hashtag first"), "#hashtag first");
  assert.equal(releaseNotesBody("# Only a title"), "");
  assert.equal(releaseNotesBody(undefined), "");
});
