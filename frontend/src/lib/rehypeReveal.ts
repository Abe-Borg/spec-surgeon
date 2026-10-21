/**
 * The rehype step that lets a block of a reply fade in as it is revealed:
 * every revealed piece of its text becomes a span of its own, the pieces
 * still within their fade (from `fresh` on) carry the `reveal` class, and
 * the caret goes after the last word.
 *
 * Pieces are only ever appended, and React keys the spans by their order
 * among their siblings, so a piece keeps its element (and its fade) while
 * the block grows around it. A piece that has settled carries no class, so
 * an element made again later (markdown closing a `**`) shows it at once
 * instead of fading it in a second time.
 *
 * Pure over the tree, so `node --test` can pin it without React.
 */
import type { Element, ElementContent, Root, RootContent, Text } from "hast";

export interface RevealOptions {
  /** Where each revealed piece begins, ascending, as offsets into the block's markdown. */
  marks: readonly number[];
  /** Pieces that begin at or after this offset are still fading in. */
  fresh: number;
  /** Put the caret after the last word. */
  caret: boolean;
}

/** Elements the caret never goes inside. */
const LEAVES = new Set(["span", "img", "br", "hr", "input"]);

export default function rehypeReveal(options: RevealOptions) {
  return (tree: Root) => {
    if (options.marks.length > 0) splitPieces(tree, options);
    if (options.caret) addCaret(tree);
  };
}

function piece(value: string, fading: boolean): Element {
  return {
    type: "element",
    tagName: "span",
    properties: fading ? { className: ["reveal"] } : {},
    children: [{ type: "text", value }],
  };
}

/** The text node cut at the marks inside it; the text before the first mark (shown before any reveal) stays as it is. */
function cut(node: Text, { marks, fresh }: RevealOptions): (Text | Element)[] {
  const start = node.position?.start.offset;
  const end = node.position?.end.offset;
  if (start === undefined || end === undefined || end <= start || node.value === "") return [node];
  // A node whose value is not its source (an escape, an entity) maps offsets in proportion.
  const scale = node.value.length / (end - start);
  const at = (offset: number) => Math.max(0, Math.min(node.value.length, Math.round((offset - start) * scale)));
  // The node cut where a piece begins inside it; each part belongs to the last piece begun at or before it.
  const cuts = [start, ...marks.filter((m) => m > start && m < end), end];
  const out: (Text | Element)[] = [];
  let p = -1;
  for (let c = 0; c + 1 < cuts.length; c += 1) {
    while (p + 1 < marks.length && marks[p + 1] <= cuts[c]) p += 1;
    const value = node.value.slice(at(cuts[c]), at(cuts[c + 1]));
    if (!value) continue;
    out.push(p < 0 ? { type: "text", value } : piece(value, marks[p] >= fresh));
  }
  return out;
}

function splitPieces(parent: Root | Element, options: RevealOptions) {
  const children: (RootContent | ElementContent)[] = [];
  for (const child of parent.children) {
    if (child.type === "element") {
      splitPieces(child, options);
      children.push(child);
    } else if (child.type === "text") {
      children.push(...cut(child, options));
    } else {
      children.push(child);
    }
  }
  parent.children = children as typeof parent.children;
}

/** The caret, inside the deepest last element that holds words: after the last word of a paragraph, a list
 *  item, a table cell or a code block. An `<i>` of its own, so the pieces' spans never take its place. */
function addCaret(tree: Root) {
  let node: Root | Element = tree;
  for (;;) {
    let last: Element | null = null;
    for (let i: number = node.children.length - 1; i >= 0; i -= 1) {
      const child: RootContent | ElementContent = node.children[i];
      if (child.type === "element") {
        last = child;
        break;
      }
      if (child.type === "text" && child.value.trim() !== "") break;
    }
    if (!last || LEAVES.has(last.tagName)) break;
    node = last;
  }
  const caret: Element = {
    type: "element",
    tagName: "i",
    properties: { className: ["reveal-caret"], ariaHidden: "true" },
    children: [],
  };
  (node.children as ElementContent[]).push(caret);
}
