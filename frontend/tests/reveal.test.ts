/**
 * How a reply's words come onto the page (lib/reveal.ts, lib/rehypeReveal.ts): the pace, a finished reply carrying
 * on from the words already shown, the blocks a reply renders as, and the pieces that fade in.
 */
import assert from "node:assert/strict";
import test from "node:test";

import type { Element, ElementContent, Root, RootContent } from "hast";
import remarkGfm from "remark-gfm";
import remarkParse from "remark-parse";
import remarkRehype from "remark-rehype";
import { unified } from "unified";

import {
  FADE_MS,
  alignShown,
  freshFrom,
  marksIn,
  revealStep,
  safeCut,
  sameMarks,
  splitBlocks,
} from "../src/lib/reveal.ts";
import rehypeReveal from "../src/lib/rehypeReveal.ts";

/** Frames at 60 fps until `length` characters are shown, as useReveal drains them (whole characters, the
 *  fraction carried into the next frame). */
function framesFor(length: number, dt = 1 / 60): number {
  let shown = 0;
  let carry = 0;
  let frames = 0;
  while (shown < length) {
    carry += revealStep(length - shown, dt);
    const take = Math.floor(carry);
    carry -= take;
    shown += take;
    frames += 1;
  }
  return frames;
}

test("a burst is shown at a reading pace: about a second for a long reply, a moment for a few words", () => {
  assert.ok(framesFor(800) / 60 < 1.4, `${framesFor(800) / 60}s`);
  assert.ok(framesFor(800) / 60 > 0.6, "a long reply still eases in");
  assert.ok(framesFor(40) / 60 < 0.35);
  // The pace is set by time, not frames: a 144 Hz screen takes as long as a 60 Hz one.
  for (const length of [40, 800]) {
    assert.ok(Math.abs(framesFor(length, 1 / 144) / 144 - framesFor(length) / 60) < 0.05, `${length}`);
  }
  assert.equal(revealStep(0, 1 / 60), 0);
  assert.equal(revealStep(3, 10), 3, "never more than the backlog, whatever the frame");
  assert.ok(revealStep(5, 1 / 60) >= 150 / 60 - 1e-9, "never slower than the slowest pace");
});

test("a cut never splits a surrogate pair", () => {
  const text = "ok 😀 done";
  assert.equal(safeCut(text, 4), 5);
  assert.equal(safeCut(text, 3), 3);
  assert.equal(safeCut(text, 99), text.length);
});

test("a finished reply carries on from the words already shown, whatever its spacing", () => {
  assert.deepEqual(alignShown("I read it", "I read it.\n\nThe headers…"), { at: 9, whole: true });
  // The stream wrote three newlines where the message has two; the words still line up.
  assert.deepEqual(alignShown("Done.\n\n\nThe ", "Done.\n\nThe headers"), { at: 10, whole: true });
  // Different words: the reply carries on from where they part.
  assert.deepEqual(alignShown("I'll bold the", "I'll italicize"), { at: 4, whole: false });
  assert.deepEqual(alignShown("anything", ""), { at: 0, whole: false });
  assert.deepEqual(alignShown("", "new"), { at: 0, whole: true });
});

test("a reply is cut into blocks at its blank lines, keeping fences and indented continuations whole", () => {
  const text = [
    "First paragraph\nstill first.",
    "",
    "- one",
    "- two",
    "",
    "  more of two",
    "",
    "```",
    "code",
    "",
    "more code",
    "```",
    "",
    "",
    "Last.",
  ].join("\n");
  const blocks = splitBlocks(text);
  assert.deepEqual(
    blocks.map((b) => b.text),
    ["First paragraph\nstill first.", "- one\n- two\n\n  more of two", "```\ncode\n\nmore code\n```", "Last."],
  );
  for (const block of blocks) assert.equal(text.slice(block.start, block.start + block.text.length), block.text);
  assert.deepEqual(splitBlocks(""), []);
  assert.deepEqual(splitBlocks("\n\n  \n"), []);
  // An open fence (the reply is still being written) holds everything after it.
  assert.deepEqual(splitBlocks("Intro\n\n```\nx\n\ny").map((b) => b.text), ["Intro", "```\nx\n\ny"]);
});

test("marks are found per block, and a piece fades while it is younger than the fade", () => {
  assert.deepEqual(marksIn([0, 4, 9, 12, 20], 4, 13), [0, 5, 8]);
  assert.deepEqual(marksIn([0, 4], 10, 20), []);
  const marks = [0, 5, 9];
  const times = [1000, 1100, 1200];
  assert.equal(freshFrom(marks, times, 1100 + FADE_MS - 1, 12), 5);
  assert.equal(freshFrom(marks, times, 1200 + FADE_MS, 12), 12, "every piece settled");
  assert.equal(freshFrom([], [], 0, 7), 7);
  assert.ok(sameMarks([1, 2, 3], [1, 2, 3]) && !sameMarks([1, 2], [1, 2, 3]) && !sameMarks([1, 3], [2, 3]));
});

// --- The rehype step ------------------------------------------------------

function hast(markdown: string, options?: Parameters<typeof rehypeReveal>[0]): Root {
  const processor = unified().use(remarkParse).use(remarkGfm).use(remarkRehype);
  const tree = processor.runSync(processor.parse(markdown)) as Root;
  if (options) rehypeReveal(options)(tree);
  return tree;
}

/** The tree as plain data: elements by name and class, text by value; positions and the line breaks between
 *  blocks dropped. */
function shape(nodes: (RootContent | ElementContent)[]): unknown[] {
  const out: unknown[] = [];
  for (const node of nodes) {
    if (node.type === "text") {
      if (node.value.trim() || !node.value.includes("\n")) out.push(node.value);
    } else if (node.type === "element") {
      const cls = (node.properties?.className as string[] | undefined)?.join(" ");
      out.push({ [cls ? `${node.tagName}.${cls}` : node.tagName]: shape(node.children) });
    }
  }
  return out;
}

const textOf = (nodes: (RootContent | ElementContent)[]): string =>
  nodes.map((n) => (n.type === "text" ? n.value : n.type === "element" ? textOf(n.children) : "")).join("");

test("each revealed piece is a span of its own, and only the young ones fade", () => {
  const tree = hast("Hello **world** today", { marks: [0, 3, 8, 16], fresh: 8, caret: false });
  assert.deepEqual(shape(tree.children), [
    { p: [{ span: ["Hel"] }, { span: ["lo "] }, { strong: [{ "span.reveal": ["world"] }] }, { "span.reveal": [" "] }, { "span.reveal": ["today"] }] },
  ]);
  assert.equal(textOf(tree.children).trim(), "Hello world today", "no word lost or added");
});

test("text shown before the reveal began stays plain", () => {
  const tree = hast("Already here, then more", { marks: [14], fresh: 14, caret: false });
  assert.deepEqual(shape(tree.children), [{ p: ["Already here, ", { "span.reveal": ["then more"] }] }]);
});

test("the caret goes after the last word: a paragraph, a list item, a code block, never inside a piece", () => {
  const caret = { "i.reveal-caret": [] };
  assert.deepEqual(shape(hast("Done.", { marks: [], fresh: 0, caret: true }).children), [{ p: ["Done.", caret] }]);
  assert.deepEqual(shape(hast("- a\n- b", { marks: [], fresh: 0, caret: true }).children), [
    { ul: [{ li: ["a"] }, { li: ["b", caret] }] },
  ]);
  assert.deepEqual(shape(hast("```\nx\n```", { marks: [], fresh: 0, caret: true }).children), [{ pre: [{ code: ["x\n", caret] }] }]);
  assert.deepEqual(shape(hast("Hi there", { marks: [0, 3], fresh: 0, caret: true }).children), [
    { p: [{ "span.reveal": ["Hi "] }, { "span.reveal": ["there"] }, caret] },
  ]);
  assert.deepEqual(shape(hast("", { marks: [], fresh: 0, caret: true }).children), [caret]);
});

test("a reply rendered block by block reads as the same markdown rendered whole", () => {
  const replies = [
    "I read the three documents.\n\nThe headers use **Times New Roman** at 9 pt, and the footer reads “Issued for Bid”.",
    "## Plan\n\nThree changes:\n\n1. The fonts\n2. The footer line\n3. The logo\n\nIf the pages look right, create the copies.",
    "Here is the table:\n\n| Part | Font |\n| --- | --- |\n| Body | Arial |\n\n> Your originals stay intact.",
    "Code:\n\n```\nline one\n\nline two\n```\n\nThat's all.",
  ];
  for (const reply of replies) {
    const whole = shape(hast(reply).children);
    const parts = splitBlocks(reply).flatMap((block) => shape(hast(block.text).children));
    assert.deepEqual(parts, whole, reply);
  }
});
