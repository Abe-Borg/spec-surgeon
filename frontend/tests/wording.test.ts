// The edited wording of a choice (src/lib/wording.ts): what the box may send, the same rule the server applies.
import assert from "node:assert/strict";
import test from "node:test";

import { checkWording, WORDING_RULE } from "../src/lib/wording.ts";

const DRAFT = "Preserve the technical wording.";
const ch = (code: number) => String.fromCodePoint(code);

test("an edit is sent as the user's words; the draft as offered is an ordinary pick", () => {
  assert.deepEqual(checkWording(DRAFT, "Preserve the technical wording exactly."), {
    error: "",
    edited: "Preserve the technical wording exactly.",
  });
  assert.deepEqual(checkWording(DRAFT, `  ${DRAFT} `), { error: "", edited: null });
});

test("an empty, multi-line or invisible-character edit cannot be sent", () => {
  const bad = ["", "   ", `Two${ch(0x0a)}lines`, `zero${ch(0x200b)}width`, `line${ch(0x2028)}sep`, `bell${ch(0x07)}`];
  for (const text of bad) {
    assert.deepEqual(checkWording(DRAFT, text), { error: WORDING_RULE, edited: null }, JSON.stringify(text));
  }
  // a tab and a no-break space show as spaces: kept, as the server keeps them
  assert.equal(checkWording(DRAFT, `ISSUED${ch(0xa0)}FOR${ch(0x09)}BID`).error, "");
});
