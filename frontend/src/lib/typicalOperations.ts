/**
 * Typical operations, by mode: what most users will ever need, each with whether the app can do it and what to
 * know (Help → Workflows). Not every operation the app has — a list of the usual jobs, in the words a spec writer
 * uses. A Safe row names the buttons that do it, pinned to the catalog (`tests/typicalOperations.test.ts` reads
 * `mock/recipes.json`, which `tests/test_recipes.py` pins to `recipes.catalog()`), so a renamed button fails a test
 * here. Pure data, so `node --test` can pin it.
 */

export interface TypicalOperation {
  /** The job, as a spec writer says it. */
  task: string;
  /** Safe mode only: the buttons that do it, by their labels. */
  buttons?: string[];
  /** The verdict: starts with "Yes", "Partly" or "No". */
  can: string;
  /** What to know before pressing or asking. */
  note: string;
}

/** Said once above the tables: what the lists are, and what they are not. */
export const TYPICAL_INTRO =
  "What most users will ever need, by mode. This is not an exhaustive list of what the app can do: Default and " +
  "Obliterate have many more operations than these tables name, and the conversation says when a request is " +
  "outside them.";

export const SAFE_OPERATIONS: readonly TypicalOperation[] = [
  {
    task: "Clean a set before issue: accept tracked changes, strip comments, highlighting, hidden text, blank lines",
    buttons: [
      "Accept all tracked changes",
      "Remove all comments",
      "Remove highlighting",
      "Remove hidden text",
      "Remove empty paragraphs",
    ],
    can: "Yes",
    note:
      "One button per pass, each building on the last. A change Word must settle itself sets a file aside: an " +
      "inserted or merged table cell, a change inside an equation. Hidden text means Word's Hidden attribute, not " +
      "white or tiny text.",
  },
  {
    task:
      "Rebrand sections reused from an old job: firm headers and footers with project values, the logo, the Title " +
      "property, file names",
    buttons: [
      "Match headers and footers to template",
      "Swap the logo",
      "Set the document title property",
      "Name files by section number and title",
    ],
    can: "Yes, with one real weakness",
    note:
      "Each document's section number and title are read from its own SECTION line by fixed rules. Some outside " +
      "firms' layouts write that line in a way the rules do not read. Those files are left untouched by these " +
      "buttons; Default mode handles them.",
  },
  {
    task: "Project-wide find and replace: issue stamp, owner, product names, code editions, a CSV of pairs",
    buttons: ["Find and replace"],
    can: "Yes",
    note:
      "Body, headers and footers. Whole words and exact case by default. A file with none of the words is left as " +
      "it is. An occurrence inside a text box sets the file aside unless you untick it. Tracked or clean.",
  },
  {
    task: "Standardize on the office font",
    buttons: ["Match font to template"],
    can: "Yes, narrowly",
    note:
      "Needs a template with one dominant body font. Only text in the documents' body size changes size; headings " +
      "and notes keep theirs. You cannot type “Arial 10” here; that is a Default request.",
  },
  {
    task:
      "Make reused specs look like the office master: outline styles and numbering, page, font, paragraph spacing",
    buttons: ["Match body formatting to template"],
    can: "Partly",
    note:
      "Each document is planned from its own structure. All-automatic numbering converts to the template's list, " +
      "all-typed converts by value, a mix is refused. A document whose own style ids collide with the template's in " +
      "a table, header or note is refused. This is the button that leaves files behind on a motley batch, by " +
      "design: it reasons with code only.",
  },
  {
    task: "Progress sets: a DRAFT watermark, removed at issue",
    buttons: ["Add or remove a watermark"],
    can: "Yes",
    note: "Needs a header in every section; a file with none is set aside. An existing watermark is replaced.",
  },
  {
    task: "Strip headers and footers before re-templating",
    buttons: ["Remove headers and footers"],
    can: "Yes",
    note: "First-page and even-page variants included. Margins stay.",
  },
  {
    task: "Deliver copies that open clean",
    buttons: ["Turn off Track Changes"],
    can: "Yes",
    note: "The setting only. Pending changes are neither accepted nor rejected.",
  },
  {
    task: "Issue clean PDFs of the set: no comments, every tracked change accepted",
    buttons: ["Save clean PDFs"],
    can: "Yes",
    note:
      "Needs Microsoft Word or LibreOffice on this computer; Word's pages are the ones to trust. Each PDF is made " +
      "from a checked clean copy, and your Word documents keep their comments and tracked changes. A change Word " +
      "must settle itself leaves that file without a PDF.",
  },
];

export const SAFE_LIMITS =
  "Safe mode cannot change wording beyond literal replacement, delete or insert articles, fix numbering, change " +
  "page setup, touch tables or fields, or do anything that needs judgment per document. Those are Default requests.";

export const DEFAULT_OPERATIONS: readonly TypicalOperation[] = [
  {
    task: "Replace terms across the set, tracked or clean: code editions, Engineer to Architect, owner names",
    can: "Yes",
    note:
      "Whole words, exact case. Text inside fields is never edited; words inside a text box are changed too. A " +
      "phrase split by a tab or line break sets the file aside rather than being skipped.",
  },
  { task: "Change the issue stamp wherever it sits", can: "Yes", note: "Cover, header or footer, found by its current wording." },
  {
    task: "Insert a standard article or clause after an anchor in every section",
    can: "Yes",
    note: "Your exact words only. The new paragraph copies its neighbour's formatting. Tracked by default.",
  },
  {
    task: "Delete paragraphs, articles, PARTs, specifier notes, or the whole body",
    can: "Yes",
    note:
      "Size is never a reason to refuse. A text box, a field crossing the edge, or a chart in an untracked " +
      "deletion sets the file aside.",
  },
  {
    task: "Keep or remove system-tagged paragraphs in a master, such as WET or DRY tags",
    can: "Yes",
    note: "The tag comes off the paragraphs that stay.",
  },
  { task: "Resolve bracketed options", can: "Yes", note: "Picks keep the option's own words; nothing is retyped." },
  {
    task: "Fill placeholders from typed values or an attached sheet",
    can: "Yes",
    note: "Values come from your sheet, never from the model.",
  },
  {
    task: "Make section number and title agree across title line, header, footer, file name and properties",
    can: "Yes",
    note: "The place you name is the truth.",
  },
  { task: "Add or fix the END OF SECTION line", can: "Yes", note: "The number is each file's own." },
  {
    task: "Move an article or paragraphs",
    can: "Yes",
    note: "Tracked shows a deletion plus an insertion. Tables and pictures need an untracked move.",
  },
  { task: "Capitalize defined terms", can: "Yes, slowly", note: "One pick per occurrence; a broad request authorizes none." },
  {
    task: "Whitespace and punctuation hygiene, non-breaking spaces before units",
    can: "Yes",
    note: "No words are added.",
  },
  { task: "Review comments: add, reply, resolve, remove", can: "Yes", note: "Your text and author name." },
  {
    task: "Rewrite or draft prose in its own words",
    can: "No, by design",
    note: "Claude may offer a draft as a choice. Nothing is placed until you pick it, or edit it and pick it.",
  },
  {
    task: "Fonts and sizes typed outright",
    can: "Yes",
    note: "Three operations the engine merges. Symbol fonts and the East Asian slot stay.",
  },
  {
    task: "Bold, caps, colour, keep-with-next, each PART on a new page, editor's blue text to black",
    can: "Yes",
    note: "Caps is a display property; letters are never retyped.",
  },
  {
    task:
      "Paragraph spacing, indents, alignment, borders and shading, on paragraphs or in styles; tab stops on " +
      "paragraphs",
    can: "Yes",
    note: "Style changes are never tracked. Tab stops are set on the paragraphs themselves, never in a style.",
  },
  { task: "Clear direct formatting", can: "Yes", note: "Hidden text, list numbers and styles are never touched." },
  {
    task: "Apply or map styles from the office template, restyle bold-caps paragraphs as headings, remove unused styles",
    can: "Yes",
    note: "Map then remove-unused runs as two steps automatically.",
  },
  {
    task:
      "Page setup: margins, orientation, landscape schedule sections, columns, different first page; insert or " +
      "remove section breaks",
    can: "Yes",
    note: "Never tracked.",
  },
  { task: "Proofing language", can: "Yes", note: "Never tracked; nothing is retyped." },
  {
    task:
      "Numbering: the architect's outline to the office scheme, typed numbers to real numbering, 1.1 to 1.01, " +
      "restart per PART, audit gaps and duplicates",
    can: "Yes, strict",
    note:
      "A list it cannot predict converts whole only. A typo or a renumbering sets the file aside. Mixed typed and " +
      "automatic refuses.",
  },
  {
    task: "Headers and footers from a prepared template or another firm's spec",
    can: "Yes",
    note:
      "Each document's own section number and title are read by fixed rules; a document whose number they cannot " +
      "read is left untouched with the reason.",
  },
  {
    task: "Fields: page numbers, DOCPROPERTY, STYLEREF at a placeholder; unlink fields; remove hyperlinks",
    can: "Yes",
    note: "Fields are inserted empty and Word fills them.",
  },
  {
    task: "Watermarks and pictures: swap the logo, insert, remove, resize",
    can: "Yes",
    note:
      "A VML picture, or one in a shape, sets the file aside. The logo swap also sets it aside for a linked, SVG, " +
      "cropped or rotated picture; the resize for a rotated one only (a cropped picture is resized, its crop kept). " +
      "An SVG file cannot be inserted.",
  },
  {
    task: "Schedules: table style, borders, repeating header rows, widths, cell shading",
    can: "Yes",
    note: "Shading every other row is not built in; it is offered as a custom method.",
  },
  {
    task:
      "Delete tables, rows or columns; insert rows with typed cells; insert a table from a CSV or sheet; merge cells",
    can: "Yes",
    note: "Merges or marks crossing the edge set the file aside.",
  },
  { task: "Accept or reject tracked changes, all or by author or date", can: "Yes", note: "Request-only; never done to tidy." },
  {
    task: "Remove comments, footnotes, endnotes; set Title or Author; Mark as Final",
    can: "Yes",
    note: "Request-only.",
  },
  {
    task: "A mixed batch from several projects",
    can: "Yes",
    note: "One plan per layout, one approval. Documents no layout covers wait under Left untouched.",
  },
];

export const DEFAULT_LIMITS =
  "Out of reach by decision in Default: text boxes as objects (adding, moving or sizing one, or its formatting), " +
  "shapes, SmartArt, charts, equations, embedded objects, data-bound content controls and the theme. Each refuses " +
  "with a code and “change this one in Word”. The words inside a text box are another matter: replacing a term in " +
  "Default changes them too, where Safe mode's Find and replace button (and a table of pairs in Default) sets " +
  "such a file aside. A custom method in Default is formatting only: no text, no new parts, no tracked changes.";

/** Said above the Obliterate table: it does everything the other two modes do, and more. */
export const OBLITERATE_INTRO =
  "Obliterate mode can do everything Safe and Default modes can do: every button's change and every operation " +
  "above are available to it by asking. It adds what is below.";

export const OBLITERATE_OPERATIONS: readonly TypicalOperation[] = [
  {
    task:
      "Claude writes the wording itself: rewrite boilerplate to a new owner's standard, consolidate articles, " +
      "modernize phrasing",
    can: "Yes",
    note:
      "Every placed word must appear in the reply you read. The words are the model's, so you read them before " +
      "approving.",
  },
  {
    task: "Edit what built-ins refuse: headers built as text boxes, cover-page text boxes, shapes",
    can: "Yes, via a custom method",
    note:
      "The sandbox, the import whitelist, and the time and memory limits still hold. Your attachments are " +
      "available to the method. Before you approve, the exact words its sample copies add and remove are listed " +
      "in the chat and under each sample, as the independent checker read them.",
  },
  {
    task: "Custom methods beyond formatting: text changes, new parts, tracked revisions in your name",
    can: "Yes",
    note:
      "Failed checks go back to the writer up to three times. The exact words added and removed are listed before " +
      "you approve — in the chat and under each sample — from the independent checker's own reading of the sample " +
      "copies, never from the method's or the model's account; a method that changes no words is said to change none.",
  },
  {
    task: "Deliver a copy that fails a check anyway",
    can: "Yes, marked UNVERIFIED",
    note:
      "The writer may keep a failed check with a reason you read beside the copy. Five package-integrity checks " +
      "can never be kept.",
  },
];

export const EVERY_MODE =
  "In every mode: copies only, the source guard, the independent checks on every copy, and built-in operations " +
  "refusing what they cannot predict.";

/** The verdict's colour: Yes is fine, Partly is a caution, No is a stop. */
export function verdictTone(can: string): "ok" | "warn" | "err" {
  if (can.startsWith("No")) return "err";
  if (can.startsWith("Partly")) return "warn";
  return "ok";
}
