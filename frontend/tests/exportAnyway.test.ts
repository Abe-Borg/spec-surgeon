// Export anyway's menu rules (`lib/exportAnyway.ts`): what is offered is the server's decision; the page shows it in
// Obliterate mode alone and never invents a choice.
import assert from "node:assert/strict";
import { test } from "node:test";
import { ANYWAY_HEAD, ANYWAY_MEANS, anywayChoices, anywayCopies, anywayDetail } from "../src/lib/exportAnyway.ts";
import type { ExportAnyway, View } from "../src/types.ts";

const offer: ExportAnyway = {
  kind: "pdf",
  label: "PDF (Clean), made by Word",
  does: "Word deletes every comment and accepts every tracked change on a private copy, and the PDF is made from that.",
  copies: ["05 21 13.docx"],
  blocked: "",
};

function view(mode: View["mode"], anyway?: unknown): Pick<View, "export" | "mode"> {
  return { mode, export: { folder: "", unsaved: 0, copies: 1, ...(anyway === undefined ? {} : { anyway }) } as View["export"] };
}

test("the choices are the server's, in Obliterate mode alone", () => {
  assert.deepEqual(anywayChoices(view("obliterate", [offer])), [offer]);
  assert.deepEqual(anywayChoices(view("default", [offer])), []);
  assert.deepEqual(anywayChoices(view("safe", [offer])), []);
  assert.deepEqual(anywayChoices(view("obliterate")), []); // an older server, or nothing to offer
  assert.deepEqual(anywayChoices({ export: { folder: "", unsaved: 0, copies: 0, anyway: [offer] } }), []); // no mode
});

test("a malformed choice is never shown, and a kind is offered once", () => {
  const accepted = { ...offer, kind: "accepted", label: "Word (Tracked Changes OFF), made by Word" };
  const shown = anywayChoices(
    view("obliterate", [
      accepted,
      { ...offer, kind: "word" }, // Word (Tracked Changes ON) is the copy as it is: Word makes nothing
      { ...offer, copies: [] },
      { ...offer, label: "" },
      null,
      "pdf",
      offer,
      { ...offer, copies: ["again.docx"] }, // the same kind twice
    ]),
  );
  assert.deepEqual(
    shown.map((c) => [c.kind, c.copies]),
    [
      ["accepted", ["05 21 13.docx"]],
      ["pdf", ["05 21 13.docx"]],
    ],
  );
  assert.deepEqual(anywayChoices(view("obliterate", "pdf")), []);
  // what the server left out reads as empty, never as undefined on the page
  const [bare] = anywayChoices(view("obliterate", [{ kind: "pdf", label: "PDF (Clean), made by Word", copies: ["a.docx", 7] }]));
  assert.deepEqual(bare, { kind: "pdf", label: "PDF (Clean), made by Word", does: "", copies: ["a.docx"], blocked: "" });
});

test("a choice says which copies and what Word does", () => {
  assert.equal(anywayCopies(["a.docx"]), "a.docx");
  assert.equal(anywayCopies(["a.docx", "b.docx", "c.docx"]), "a.docx, b.docx, c.docx");
  assert.equal(anywayCopies(["a.docx", "b.docx", "c.docx", "d.docx", "e.docx"]), "a.docx, b.docx and 3 more");
  assert.equal(anywayDetail(offer), `For 05 21 13.docx. ${offer.does}`);
  assert.equal(anywayDetail({ ...offer, does: "" }), "For 05 21 13.docx.");
});

test("the group says it is Word's making and that nothing is proven", () => {
  assert.equal(ANYWAY_HEAD, "Made by Word, unchecked");
  assert.match(ANYWAY_MEANS, /marked copies/);
  assert.match(ANYWAY_MEANS, /No check proves what Word makes/);
});
