/**
 * The "I'm not convinced" dossier — the long answer for the specification writer or engineer who puts their name on
 * the documents Spec Surgeon edits and needs to know exactly where every change came from and exactly what ran on
 * their behalf. Opened from the bottom of Help → Why trust it, stacked on top of it.
 *
 * Written for a working AEC professional, not a software engineer: every mechanism is named, every claim is in the
 * claims ledger (docs/TRUST_CLAIMS.md) with the code that makes it true, every number comes from lib/trustFacts.ts,
 * and the closing section says how to check all of it.
 *
 * If the implementation changes, this changes with it. A trust document that has drifted from the code is worse than
 * none.
 */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useDialogFocus } from "../lib/dialogFocus";
import { MODEL_NAME, TRUST_FACTS as F, grouped } from "../lib/trustFacts";
import { NONE, RUNTIME_CARDS, type RuntimeCard } from "../lib/trustRuntime";
import { OBLITERATE_GRANTS, OBLITERATE_KEPT } from "../lib/modes";
import TrustDiagram from "./TrustDiagram";

interface Props {
  open: boolean;
  onClose: () => void;
}

/** The contents rail. Ids double as scroll anchors. */
export const SECTIONS: { id: string; label: string }[] = [
  { id: "summary", label: "The short answer" },
  { id: "origins", label: "Where the edits come from" },
  { id: "engine", label: "What the engine is" },
  { id: "boundary", label: "What leaves your computer" },
  { id: "runtime", label: "What runs when you click" },
  { id: "authority", label: "What the AI may touch" },
  { id: "terms", label: "What the words mean" },
  { id: "deterministic", label: "The parts with no AI" },
  { id: "security", label: "Security & privacy" },
  { id: "money", label: "Money" },
  { id: "limits", label: "What this does not do" },
  { id: "verify", label: "Check it yourself" },
];

/** The further-reading links: the only external references in the trust surfaces. */
export const FURTHER_READING: { href: string; label: string; what: string }[] = [
  {
    href: "https://www.anthropic.com/legal/privacy",
    label: "Anthropic privacy policy",
    what: "how the model provider handles the data your requests carry.",
  },
  {
    href: "https://www.anthropic.com/legal/commercial-terms",
    label: "Anthropic commercial terms",
    what: "the terms your API key is used under, including how API data may be used.",
  },
  {
    href: "https://trust.anthropic.com",
    label: "Anthropic Trust Center",
    what: "security posture, compliance reports and subprocessors.",
  },
  {
    href: "https://docs.claude.com",
    label: "Claude API documentation",
    what: "the model, effort, prompt caching and streaming described above.",
  },
  {
    href: `https://${F.githubHost}/${F.repo}/releases`,
    label: "Spec Surgeon releases",
    what: "versions, release notes, and the SHA256SUMS the updater checks downloads against.",
  },
  {
    href: `https://${F.githubHost}/${F.repo}`,
    label: "Spec Surgeon source code",
    what: "everything this document describes. Its own code is under the MIT License; with PyMuPDF the app as a whole is under the GNU Affero General Public License (AGPL), and each release page carries its complete source. docs/TRUST_CLAIMS.md is the ledger behind it.",
  },
];

/* --------------------------------------------------------------------- */
/* Presentational primitives — the same palette and idiom as HelpModal     */
/* --------------------------------------------------------------------- */

function Section({ id, title, kicker, children }: { id: string; title: string; kicker: string; children: ReactNode }) {
  return (
    <section id={id} aria-labelledby={`${id}-title`} className="scroll-mt-4 border-t border-edge pt-7 first:border-0 first:pt-0">
      <p className="text-[11px] font-medium tracking-wider text-accent uppercase">{kicker}</p>
      <h3 id={`${id}-title`} className="mt-1 font-[family-name:var(--font-display)] text-lg font-semibold text-ink">
        {title}
      </h3>
      <div className="mt-3 space-y-4">{children}</div>
    </section>
  );
}

function P({ children }: { children: ReactNode }) {
  return <p className="text-sm leading-relaxed text-ink-dim">{children}</p>;
}

function Mono({ children }: { children: ReactNode }) {
  return <span className="font-[family-name:var(--font-mono)] text-[12px] text-ink">{children}</span>;
}

function Bullets({ items }: { items: ReactNode[] }) {
  return (
    <ul className="space-y-2">
      {items.map((item, i) => (
        <li key={i} className="flex gap-2.5">
          <span className="mt-[7px] h-1.5 w-1.5 flex-none rounded-full bg-accent/70" />
          <div className="text-sm leading-relaxed text-ink-dim">{item}</div>
        </li>
      ))}
    </ul>
  );
}

/** A short aside; `tone` shifts the rule colour, nothing else. */
function Note({ title, tone = "accent", children }: { title?: string; tone?: "accent" | "warn" | "ok"; children: ReactNode }) {
  const rule = tone === "warn" ? "border-l-warn" : tone === "ok" ? "border-l-ok" : "border-l-accent";
  return (
    <div className={`rounded-r-lg border-l-2 bg-raised/40 px-4 py-3 ${rule}`}>
      {title && <p className="text-sm font-medium text-ink">{title}</p>}
      <div className="text-sm leading-relaxed text-ink-dim">{children}</div>
    </div>
  );
}

/** Two columns: what does, what does not. */
function Contrast({ leftTitle, left, rightTitle, right }: { leftTitle: string; left: ReactNode[]; rightTitle: string; right: ReactNode[] }) {
  const col = (title: string, items: ReactNode[], ok: boolean) => (
    <div className="rounded-xl border border-edge bg-raised/30 p-4">
      <h4 className={`text-sm font-medium ${ok ? "text-warn" : "text-ok"}`}>{title}</h4>
      <ul className="mt-2.5 space-y-2">
        {items.map((item, i) => (
          <li key={i} className="flex gap-2 text-sm leading-relaxed text-ink-dim">
            <span className={`flex-none ${ok ? "text-warn" : "text-ok"}`} aria-hidden>
              {ok ? "↗" : "●"}
            </span>
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
  return (
    <div className="grid gap-3 md:grid-cols-2">
      {col(leftTitle, left, true)}
      {col(rightTitle, right, false)}
    </div>
  );
}

function Matrix({ head, rows }: { head: string[]; rows: ReactNode[][] }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-edge">
      <table className="w-full min-w-[34rem] border-collapse text-sm">
        <thead>
          <tr className="bg-raised/60 text-left">
            {head.map((h) => (
              <th key={h} scope="col" className="border-b border-edge px-3 py-2 text-xs font-medium tracking-wide text-ink-faint uppercase">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="align-top">
              {row.map((cell, j) => (
                <td key={j} className={`border-b border-edge/60 px-3 py-2.5 leading-relaxed ${j === 0 ? "text-ink" : "text-ink-dim"}`}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** One action, always answered in the same five rows so the cards can be compared rather than merely read. */
function Runtime({ n, card }: { n: number; card: RuntimeCard }) {
  const row = (label: string, value: ReactNode, strong = false) => (
    <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-3">
      <dt className="w-32 flex-none pt-px text-[11px] font-medium tracking-wide text-ink-faint uppercase">{label}</dt>
      <dd className={`text-sm leading-relaxed ${strong ? "font-medium text-ok" : "text-ink-dim"}`}>{value}</dd>
    </div>
  );
  return (
    <article className="rounded-xl border border-edge bg-raised/30 p-4" aria-labelledby={`card-${n}`}>
      <div className="flex items-baseline gap-2.5">
        <span className="flex h-5 w-5 flex-none items-center justify-center rounded-full bg-accent/15 text-[11px] font-semibold text-accent tabular-nums">
          {n}
        </span>
        <h4 id={`card-${n}`} className="font-[family-name:var(--font-display)] text-[15px] font-semibold text-ink">
          {card.title}
        </h4>
        {card.automatic && <span className="rounded bg-raised px-1.5 py-0.5 text-[11px] text-ink-faint">Automatic</span>}
      </div>
      <dl className="mt-3 space-y-2.5">
        {row("You do", card.you)}
        {row("What runs", card.runs)}
        {row("What is sent", card.sent)}
        {row("AI involved", card.ai, card.ai === NONE)}
        {row("Bounded by", card.bounds)}
      </dl>
    </article>
  );
}

/** The exact claim a loaded word makes, and what it does not. */
function Term({ word, claim, not }: { word: string; claim: ReactNode; not: ReactNode[] }) {
  return (
    <div className="rounded-xl border border-edge bg-raised/30 p-4">
      <h4 className="font-[family-name:var(--font-display)] text-[15px] font-semibold text-ink">“{word}”</h4>
      <div className="mt-2 rounded-lg border border-accent/40 bg-accent/10 px-3 py-2">
        <p className="text-[11px] font-medium tracking-wide text-accent uppercase">The exact claim</p>
        <p className="mt-0.5 text-sm leading-relaxed text-ink">{claim}</p>
      </div>
      <p className="mt-3 text-[11px] font-medium tracking-wide text-ink-faint uppercase">It does not claim</p>
      <ul className="mt-1 space-y-1">
        {not.map((item, i) => (
          <li key={i} className="flex gap-2 text-sm leading-relaxed text-ink-dim">
            <span className="flex-none text-ink-faint" aria-hidden>
              –
            </span>
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Link({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className="text-accent underline underline-offset-2 hover:text-accent-hover">
      {children}
    </a>
  );
}

/* --------------------------------------------------------------------- */
/* Content                                                                */
/* --------------------------------------------------------------------- */

function Dossier() {
  return (
    <div className="space-y-7 pb-4">
      <Section id="summary" kicker="Start here" title="The short answer">
        <P>
          Spec Surgeon is an editing instrument, not an author. It makes the change you describe to copies of your Word
          specifications and shows you the evidence for each copy. Three commitments hold the rest of this document
          together:
        </P>
        <Bullets
          items={[
            <>
              <b className="text-ink">Every change has a declared origin.</b> Your words, your files, a built-in
              operation the plan names, or — clearly marked — something Claude proposed. The plan you approve is
              described by the operations’ own code, not by Claude, and new wording must trace to you (one exception is
              named under “Where the edits come from”).
            </>,
            <>
              <b className="text-ink">Nothing runs that you did not start, and every exception is named.</b> Safe
              mode makes no AI call at all. In Default mode every AI
              step happens inside a message, a choice or a button press of yours, though one press can chain several
              steps (they are all listed under “What runs when you click”). Without a click the app only checks files as
              they arrive and asks GitHub for a new version at most once every {F.updateCheckHours} hours.
            </>,
            <>
              <b className="text-ink">Every automated judgment can be re-derived.</b> Each copy carries a verification
              record from a checker that shares no code with the editing; the conversation’s records keep every request
              sent to Claude and every answer; the logs say what ran and what it is estimated to have cost. All of it is
              in plain files on your computer.
            </>,
          ]}
        />
      </Section>

      <Section id="origins" kicker="Provenance" title="Where the edits come from">
        <P>
          A delivered copy is your original plus the changes in the plan you approved. Those changes come from a short
          list of places, and each is labelled differently:
        </P>
        <Matrix
          head={["Origin", "What it actually is", "How you can tell"]}
          rows={[
            [
              "You",
              "Your request, your typed answers, and the exact wording of a choice you picked — as offered, or as you edited it first. Only these count as approved wording.",
              "Your bubbles in the chat. The conversation’s record keeps them as approved words, apart from everything Claude wrote.",
            ],
            [
              "A Safe-mode button",
              "A fixed plan the app builds in code from the form you filled in: the button's own operations and settings, and only the words you typed.",
              "Your line in the chat names the button and what you typed; the plan's sentences are the operations' own.",
            ],
            [
              "Your files",
              "An attached template, picture or table, and a document’s own words (a template filled with each file’s own section number and title, or a File Properties title built from them around words you gave).",
              "The plan’s sentence names the file; a value from your sheet or template is never retyped by Claude.",
            ],
            [
              "The engine’s operations",
              "Built-in, deterministic edits: fonts, spacing, styles, numbering, headers and footers, tables, pictures, page setup and more. Formatting and structural operations add no characters at all.",
              "Each change in the plan is a sentence the operation writes about itself from the plan’s settings — the same settings the engine runs.",
            ],
            [
              "Claude’s plan",
              "Which operations to run, on which paragraphs, with which settings. Never wording of its own: a plan that would place a word you did not give is refused before you see it.",
              "The plan’s sentences, and the assumptions Claude made, listed as assumptions under the changes.",
            ],
            [
              "Claude’s custom method",
              "When no operation fits and a formatting-only change would, a small program Claude wrote. It may not change a word, add a part or make a tracked change. Under an outcome plan (next row) it may make that plan’s changes instead, wording included, and is checked against the plan.",
              "The plan shows Claude’s own one-line description of it; the conversation says a custom method was prepared.",
            ],
            [
              "Claude’s outcome plan",
              "When the ordinary plan cannot be made, the outcomes Claude proposes (what should change, where, with what wording). The app carries it out on copies without waiting for a pick, so its wording is used without one.",
              "The plan lists the outcomes as its changes. Read their wording before you approve.",
            ],
            [
              "Claude’s visual check",
              "Advice about the sample pages. It changes nothing in a file.",
              "Kept in the preview’s records by file and page, not listed in the window; a blocking finding is listed beside the pages and changes the button’s label.",
            ],
            [
              "Remembered repairs",
              `Up to ${F.pastRepairCasesSent} past repair cases from earlier work on this computer, given to recovery as untrusted context.`,
              "A remembered repair is tried again from scratch and must pass the same checks; recovery’s records name it.",
            ],
          ]}
        />
        <Note title="Things that are not happening">
          <Bullets
            items={[
              "No Spec Surgeon server, account or cloud storage. The app talks to Anthropic’s API with your key and to GitHub for updates, and to nothing else of its own.",
              "No library of other people’s specifications. Claude sees your request, your files and what its tools read from them — nothing from other users.",
              "No telemetry, analytics or crash reporting. Logs and diagnostic bundles are written on your computer and never uploaded.",
              "No folder scanning. Only the files you attach are read; nothing beside them is looked for.",
              "No training by Spec Surgeon. What Anthropic may do with API data is set by its commercial terms and privacy policy, linked at the end.",
              "No web search. Claude has no tool that reaches the internet, so nothing in a plan comes from the web.",
            ]}
          />
        </Note>
      </Section>

      <Section id="engine" kicker="The engine" title="What the engine actually is">
        <Matrix
          head={["Job", "Model or component", "Why"]}
          rows={[
            [
              "Understanding your request, asking, planning",
              <>
                {MODEL_NAME} (<Mono>{F.model}</Mono>) on Anthropic’s servers: {F.conversationEffort} effort, up to{" "}
                {grouped(F.maxOutputTokens)} output tokens per call
              </>,
              "Turning plain English into a plan of operations takes judgment; the plan is then checked by code.",
            ],
            [
              "The visual check",
              <>
                {MODEL_NAME}, at the conversation’s {F.conversationEffort} effort (it runs inside the turn that started
                it)
              </>,
              "Looking at pages.",
            ],
            [
              "Writing a custom method",
              <>
                {MODEL_NAME}, {F.codegenEffort} effort while it writes the method, here or in recovery; the calls
                after it go back to their own effort
              </>,
              "The one place Claude writes the code that edits your documents: it gets the most thought.",
            ],
            [
              "An outcome plan, and recovery of copies that failed",
              <>
                {MODEL_NAME}, {F.recoveryEffort} effort (no step runs at the model’s highest setting)
              </>,
              "The harder path keeps more depth.",
            ],
            [
              "Editing the document",
              <>
                The operation library, on your computer: a batch of {F.processMinFiles} or more files is shared over the
                app’s own worker processes (one per core, at most {F.maxProcessWorkers}), each file edited and checked
                whole by one of them
              </>,
              "Code working directly on the document’s XML, with no model involved: it does what the plan says, or refuses with a reason. A file’s result does not depend on which worker had it.",
            ],
            [
              "Checking every copy",
              "The independent checker (checks V1–V10), on your computer",
              "Shares no code with the editing; a test fails the build if it ever does.",
            ],
            [
              "Rendering pages",
              <>
                Microsoft Word (new, invisible instances, up to {F.wordInstances} side by side, never the Word you have
                open) or LibreOffice, on your computer
              </>,
              "So you and the visual check can see the result.",
            ],
            [
              "Running Claude’s code",
              "Two sandboxes: separate Python processes on your computer",
              "Custom methods and read-only analyses run there, never inside the app.",
            ],
          ]}
        />
        <P>
          In plain words: Claude thinks before it answers (its thinking summary is shown while it works), at the
          reasoning depth called “effort”. It may write at most {grouped(F.maxOutputTokens)} tokens of output per call (a
          token is about three-quarters of a word). Claude’s message to you in a question or a plan reaches the window
          as it is written; the app checks that what arrived is complete before acting on it, and asks again when it
          is not. The app sends no temperature (randomness) setting, so the model’s defaults apply. The instructions and the operation
          catalog are cached by Anthropic for an hour, and the growing conversation for the default five minutes, so a
          follow-up reads them instead of paying full price again. A pill in the top bar counts the context the
          conversation holds, as Anthropic counted it at its last step, against the model’s{" "}
          {grouped(F.contextWindowTokens)}-token window: a measurement from what each answer reports, never an extra
          call. Past about {grouped(F.contextRolloverTokens)} tokens by the app’s own estimate, the conversation is
          sealed on your computer and goes on in a fresh one that carries your request, your decisions and the approved
          words; Claude can still read the sealed part, and nothing in it approves anything.
        </P>
        <Note title="Four things about the model you should know" tone="warn">
          <Bullets
            items={[
              <>
                {MODEL_NAME} is the default. The environment variable <Mono>{F.modelEnv}</Mono> replaces it for every
                call, and the About page and the conversation’s record then name that model. Nothing in the window
                sets it.
              </>,
              "If Anthropic’s safety filters decline a request, Anthropic may answer it with another model inside the same call. The app does not choose that model; the conversation’s record names the model that answered, and the spending pill says when the price table cannot estimate it.",
              <>
                The Anthropic SDK sends to <Mono>{F.anthropicHost}</Mono> unless the environment variable{" "}
                <Mono>{F.apiAddressEnv}</Mono> names another address; then your requests, documents and key go there
                instead. Nothing in the window sets it.
              </>,
              `Fast responses (Settings) is off until you turn it on, with a second click that names the price. On, each call to a model Anthropic serves fast asks for its fast mode: the same model, effort and thinking, writing up to ${F.fastSpeedup} times faster, at ${F.fastPriceFactor} times the price on every token. When Anthropic’s fast capacity is busy, the call runs at once at standard speed and price instead of waiting; if Anthropic refuses fast mode for your key, every call runs at standard speed and Settings says why.`,
            ]}
          />
        </Note>
      </Section>

      <Section id="boundary" kicker="The boundary" title="What leaves your computer, and where it goes">
        <TrustDiagram />
        <Contrast
          leftTitle="Leaves your machine"
          left={[
            <>
              <b className="text-ink">To Anthropic’s API</b> (<Mono>{F.anthropicHost}</Mono>, or the address in{" "}
              <Mono>{F.apiAddressEnv}</Mono> if you set one; over HTTPS, with your key),
              only while work you started runs: your request and answers; file names (and, in recovery, full paths); the
              full path of an attached template, picture or table; a profile of each kind of document (styles, headings,
              header and footer text, sample paragraphs); an overview of every document (its section number and title,
              its header and footer lines, its File Properties title); whatever Claude’s read tools return (paragraph text, a part’s
              XML, table rows); page images rendered on your computer; failure reasons; and in recovery up to{" "}
              {F.pastRepairCasesSent} remembered repair cases from earlier work.
            </>,
            <>
              <b className="text-ink">To GitHub</b> (<Mono>{F.githubApiHost}</Mono>, <Mono>{F.githubHost}</Mono>): the
              update check, on its own at most once every {F.updateCheckHours} hours and when you click Check for updates,
              and the installer download when you click it. They carry the app’s version and nothing about your work.{" "}
              <Mono>{F.noUpdateCheckEnv}</Mono> turns the automatic check off.
            </>,
            "To any web address, through your own browser, when you click a link (in the chat, in release notes, on About).",
            <>
              <b className="text-ink">A known gap.</b> The chat, the plan and the release notes are drawn from Markdown.
              A picture written as Markdown in that text — <Mono>![](https://…)</Mono> — would be fetched by the window
              from wherever it points, without a click. Claude has no reason to write one, but text inside a document
              could ask it to. The app does not block it yet.
            </>,
          ]}
          rightTitle="Stays on your machine"
          right={[
            "Your originals: read, never written.",
            "Edited copies, failed copies, previews, page pictures and PDFs (the clean versions Export makes among them), in the window’s scratch folder while it is open; the Word copies and PDFs you save with Export, where you chose.",
            "The verification records, the conversation’s records (every request sent and every answer), logs and event journals.",
            "Your API key, in Windows Credential Manager when you ask it to be remembered.",
            "The repair memory, the render cache and settings.json; the conversation’s journal and copies of files you dropped on the window, in the scratch folder while it is open.",
            <>
              The window’s own traffic: the page talks to the app’s server on <Mono>{F.loopback}</Mono>, which no other
              computer can reach, and every request needs this session’s token.
            </>,
          ]}
        />
      </Section>

      <Section id="runtime" kicker="Runtime" title="What runs when you click">
        <P>
          One card per thing you can do, and one per thing that happens on its own. Every card answers the same five
          questions in the same order, so you can compare them. “AI involved: None.” means no model call at all.
        </P>
        <div className="space-y-3">
          {RUNTIME_CARDS.map((card, i) => (
            <Runtime key={card.title} n={i + 1} card={card} />
          ))}
        </div>
      </Section>

      <Section id="authority" kicker="Blast radius" title="What the AI may touch, and what it cannot">
        <P>
          Claude acts only through tools the app defines and answers. Every tool below names files by the label the app
          gave them, never by a path of Claude’s choosing, and every read works on a copy held in memory.
        </P>
        <Matrix
          head={["Tool or capability", "What it can do", "Constraint"]}
          rows={[
            [
              "Read tools (paragraphs, XML of a part, styles, headers and footers, the template, an attached table)",
              "Read the attached documents, the attached template and attached pictures or tables.",
              "Only those files, each re-checked against the content it had when attached; read-only; each answer is cut to a fixed length.",
            ],
            [
              "View pages",
              "Ask for pages of an attached document or the template.",
              `The app renders them on your computer, at most ${F.viewPagesPerCall} pages per call, ${F.pageImageLongEdgePx} pixels on the long side.`,
            ],
            [
              "Run an analysis",
              "Write a short read-only Python program that inspects copies of the documents’ XML.",
              `A separate process with no file, network or process access, at most ${F.analysisSeconds} s and ${F.analysisMemoryMB} MB; only whitelisted modules. Its result is just data for Claude.`,
            ],
            [
              "Ask you",
              "Ask questions, offer choices and suggested replies.",
              "Choices about the result only, never a choice of method; wording with invisible characters is refused; a tapped suggested reply is never wording.",
            ],
            [
              "Set documents aside",
              "Put documents you named on your to-do list, out of this request and every later one.",
              "Only at your word, by the labels the app gave the documents; nothing in them changes; they come back only when you work on them or press Put back.",
            ],
            [
              "Submit a plan",
              "Propose the plan of operations.",
              "Checked before you see it: valid operations and settings, attached files only, every placed word traced to you, a dry run on representative files.",
            ],
            [
              "Write a custom method",
              "Write one small Python operation when nothing built-in fits.",
              `Formatting only, except under an outcome plan, where it may make exactly that plan’s changes, wording included (in Obliterate mode, below: any change Word stores, its failed checks reported, not blocking). Checked as text (whitelisted modules, no file, process or network calls), run on copies in a separate process (${F.customMethodSeconds} s, ${F.customMethodMemoryMB} MB, writes only in its own folder), and every result must pass the independent checker (and, under an outcome plan, its check of the outcomes).`,
            ],
            [
              "Propose outcomes, try repairs (recovery)",
              "Propose what should change and try plans or custom methods on copies of failed files.",
              `Copies only, checked like everything else; at most ${F.recoveryAttemptsPerGroup} attempts per group of files.`,
            ],
            [
              "Visual check, critic",
              "Report findings about pages, or object to a repair.",
              "Advice only: neither can pass anything.",
            ],
          ]}
        />
        <Note title="What Claude categorically cannot do">
          <Bullets
            items={[
              "Open, write, move or delete any file on your computer. Its programs write only inside their own temporary folder.",
              "Reach the internet. It has no web search or fetch tool, and its programs’ network calls are blocked.",
              "Deliver a copy, or write over an original. Only your click delivers, and only copies.",
              "Approve its own wording — an outcome plan’s wording is approved by your pick of it; in Obliterate mode (below) Claude may write wording, but only wording its reply shows you, and your click approves it.",
              "Change your settings, your key or the app.",
            ]}
          />
        </Note>
        <Note title="Obliterate mode: what it lifts, and what it does not" tone="warn">
          <P>
            Hidden unless Settings → Advanced → Show Obliterate mode is on, and chosen only after its warning, every
            time; the app never opens in it. Everything above holds in Safe and Default modes. In Obliterate mode:
          </P>
          <Bullets items={[...OBLITERATE_GRANTS]} />
          <P>What still holds, in every mode:</P>
          <Bullets items={[...OBLITERATE_KEPT]} />
        </Note>
      </Section>

      <Section id="terms" kicker="Definitions" title="What the key terms mean, exactly">
        <Term
          word="Verified (PASS)"
          claim="A checker that shares no code with the editing read the original and the copy and found exactly the predicted change: the predicted text and no other text changed, no other part of the package changed, the structure and tracked changes are as predicted, the XML is no less valid than the original, accepting and rejecting the tracked changes give the predicted results, and every predicted property holds (checks V1–V10, each recorded)."
          not={[
            "That the plan was what you meant: a PASS proves the planned change, not the request.",
            "That the result meets a standard, a code or your office’s practice.",
            "That Word will show it as you expect: look at the pages.",
          ]}
        />
        <Term
          word="Approved"
          claim="You pressed Create edited copies (or Save ready copies) on a plan whose fingerprint covers the plan and the exact content of each file. A changed file or plan voids the approval. In Safe and Default modes, a document whose preview failed a check is not part of it (it waits under Left untouched), and Create refuses a preview none of whose samples passed. Save ready copies exports only the recovery's ready files. Export’s Word (Tracked Changes OFF) and PDF (Clean) are approved by your choice in the menu: each runs one plan fixed in the app over your edited copies and changes none of them."
          not={[
            "That you read every sentence of the plan.",
            "That Claude understood you — only that the engine will do exactly what is on screen.",
          ]}
        />
        <Term
          word="Independent"
          claim="The checker’s code imports nothing from the editing code; it has its own readers, text extraction and definitions, and a test fails if that ever changes."
          not={["That it was written by a different person.", "That it can tell a wrong request from a right one."]}
        />
        <Term
          word="Copies only"
          claim="The window never writes to a file you attached. Every run delivers into the window’s scratch folder, and Export writes the copies where you choose, never over an input."
          not={[
            "That copies are kept: the scratch folder is deleted when the window closes, after one question if a copy is not saved as it is now (never saved with Export → Word (Tracked Changes ON), or the copy or its saved file changed since).",
            "That the folder is private: the scratch folder is under the app’s local data, and the saved copies are wherever you chose.",
          ]}
        />
        <Term
          word="Sandbox"
          claim="Claude’s programs run in a separate Python process with a stripped environment, a whitelist of modules, a guard that blocks starting processes, opening network connections and loading native code, writes only in its own folder, a time limit and a memory limit."
          not={[
            "An operating-system security boundary. On Windows the analysis sandbox has no system-call filter; the custom-method sandbox has none anywhere. The code says plainly it is not a complete boundary against hostile code.",
          ]}
        />
        <Term
          word="Estimated cost"
          claim="Tokens reported by Anthropic, priced from a table built into the app."
          not={["A bill: Anthropic bills your key, at its current prices.", "Exact for a stopped call, a failed call or a model missing from the table."]}
        />
        <Term
          word="Visual check"
          claim="Claude looked at sample pages, before and after, and said what it noticed."
          not={["A check in the verification record.", "Every page of every file.", "Reliable: it can miss a problem and flag a non-problem."]}
        />
      </Section>

      <Section id="deterministic" kicker="No AI here" title="The parts with no AI in them">
        <P>The list is longer than most people expect. None of these call a model, and none use the network:</P>
        <Bullets
          items={[
            "Checking each file when it arrives (opens, is a .docx, no document type declaration, has a paragraph; notes on typed numbers, tracked changes and more).",
            "Safe mode, every button from its form to the copies.",
            "The guided tour, every chapter from the bundled practice documents to the copies it makes of them; its conversation chapters are a recording and say so.",
            "Every operation: planning the change from the original, applying it, and writing the package back with every untouched part byte-identical.",
            "Putting overlapping changes into ordered steps, in the order you asked for them.",
            "Grouping a mixed batch by layout.",
            "The independent checker, V1–V10.",
            "Rendering pages (Word or LibreOffice on your computer) and making the page pictures.",
            "The sentences that describe the plan.",
            "Approval fingerprints and the delivery guard.",
            "The spending estimate, the logs, the diagnostic bundle.",
            "The update check and the installer’s integrity checks (they use the network, but no AI).",
          ]}
        />
        <Note title="With the network disconnected" tone="ok">
          You can still attach files and see their checks, use Safe mode's buttons from the preview to the copies, take the guided tour, open
          earlier copies and their records, read the logs and save a bundle. You cannot ask for a change in Default
          mode: every request there goes through Claude.
        </Note>
      </Section>

      <Section id="security" kicker="Security" title="Security and privacy, mechanism by mechanism">
        <Matrix
          head={["Concern", "How it is handled"]}
          rows={[
            [
              "Your API key",
              <>
                Remembered only if you tick the box, in Windows Credential Manager under{" "}
                <Mono>{F.credentialTarget}</Mono>; otherwise kept for the session. Never written to settings, records or
                conversations; scrubbed from logs by value and by pattern; sent only to Anthropic. Settings → Remove
                app key removes the app's session and saved key, without revoking it at Anthropic or changing external
                credentials. Saving reports availability; it does not test authentication.
              </>,
            ],
            [
              "The window’s local server",
              <>
                Listens on <Mono>{F.loopback}</Mono> only, on a free port picked at start. Every request must carry the
                token created for this launch (<Mono>{F.tokenHeader}</Mono>). Pictures are served only as PNG files from
                the app’s own folders. If the app cannot open its own window (for example, without WebView2), it opens the page in
                your default browser instead, at the same local address, with that launch’s token in the address, which
                the browser keeps in its history.
              </>,
            ],
            [
              "Hostile documents",
              "A part with an XML document type declaration is refused; XML is read without resolving entities; files are opened within size limits. Document text is treated as untrusted: whatever it says, Claude’s plan still has to pass the same checks.",
            ],
            [
              "Claude’s code",
              "Runs only in the sandboxes described above, on copies. Its results count only if the independent checker passes them.",
            ],
            [
              "Links and pictures in Claude’s text",
              "A link opens in your browser, never inside the window. A picture would be fetched — the known gap under “What leaves your computer”.",
            ],
            [
              "Updates",
              `HTTPS only, redirects included. Only the installer named for the release, at most ${F.maxInstallerMB} MiB, whose SHA-256 matches the release’s SHA256SUMS and GitHub’s digest, and again just before it runs. The installer is unsigned: this proves the file is the one the release lists, not who published it.`,
            ],
            [
              "Logs",
              `On your computer only: the app log keeps ${F.appLogMB} MiB plus ${F.appLogBackups} older files, the event journals ${F.eventJournalMB} MiB per file for the ${F.eventJournalProcesses} most recent past sessions, the newest ${F.crashLogsKept} crash logs. They record what ran, never your prompts — but an error or a refusal reason can quote a line of a document.`,
            ],
            [
              "What is kept, and for how long",
              `The window’s scratch folder (copies, previews, page pictures, the conversation’s journal and records with page images, copies of files you dropped on the window) is deleted when the window closes — after one question if a copy is not saved as it is now — and swept at the next start; the folder of a window that never closed is restored first. What you save with Export stays where you put it, and saved bundles are never deleted. The repair memory keeps up to ${grouped(F.repairMemoryCases)} cases, your requests and wording included, with no button to clear it; the render cache ${F.renderCacheGB} GiB. All of it but the saved copies is in the app’s folders under your Windows profile.`,
            ],
            [
              "Diagnostic bundles",
              "Made only when you ask, saved on your computer, never sent. They leave out folders of copies and pictures, but the conversation’s saved history they include can hold page images and document text.",
            ],
          ]}
        />
      </Section>

      <Section id="money" kicker="Money" title="Money">
        <Bullets
          items={[
            "You pay Anthropic for the API calls, through your own key. Spec Surgeon has no account, subscription or bill of its own.",
            "The pill in the top bar is an estimate: the tokens Anthropic reports for each call, priced from a table built into the app. The table can be out of date; a model it does not list shows “an amount the price table cannot estimate” instead of a guess. Anthropic’s console is the exact figure.",
            `The spending check is yours: Settings → Spending check is the most one request (a message, a pick, an approval) may spend, $${F.spendingCheckDefaultUsd} until you change it or turn it off. An AI step that could take the request past it is not started (counting the most that step could cost); the work waits for Continue, which allows the amount again. It checks estimates, not Anthropic's bill. Stop, beside the spending in the top bar, cuts off the running step at once.`,
            `Fast responses, if you turn it on in Settings, costs ${F.fastPriceFactor} times as much on every token (the cache discounts apply on top), for answers written up to ${F.fastSpeedup} times faster. The pill prices those calls at the fast rates and says Fast while fast mode is in effect.`,
            `Stopped work: the stopped call is estimated from what Anthropic reported plus at most ${F.stoppedOutputTokensPerSecond} output tokens per second it ran (${F.fastSpeedup} times that for a fast call). Anthropic may bill what was produced before the stop.`,
            "Failed work: a failed call keeps its estimate, because it may have been billed. A call that never connected (no network, a name that does not resolve, a proxy or firewall that refused it) reached nobody and keeps none, and neither does one Anthropic refused for its key or its permission before running any model. A call that failed for a passing reason is sent again, and each attempt that reached Anthropic costs.",
            "The preview, the checker, rendering and creating copies cost nothing: they run on your computer. Recovery after a failed copy and the visual check are AI steps and are counted.",
          ]}
        />
      </Section>

      <Section id="limits" kicker="Honesty" title="What this does not do">
        <Bullets
          items={[
            <>
              <b className="text-ink">It can be confidently wrong about what you meant.</b> Claude may read “every
              heading” differently from you, and the engine will then do exactly the wrong thing, perfectly. The guard is
              the plan’s sentences and the before-and-after pages: read them before you approve.
            </>,
            <>
              <b className="text-ink">A PASS is not a review.</b> It proves the planned change and nothing else. It says
              nothing about whether the specification is right.
            </>,
            <>
              <b className="text-ink">Only samples are rendered before you approve</b> — {F.sampleFiles} files (or every file
              in a smaller batch), and at least one per layout — so a problem only another file shows may first appear in the copies. Every copy is
              still checked.
            </>,
            <>
              <b className="text-ink">The visual check is advice</b> and can be wrong both ways.
            </>,
            <>
              <b className="text-ink">LibreOffice is not Word.</b> Without Word installed, the pages are LibreOffice’s,
              which can lay out differently; open the copies in Word.
            </>,
            <>
              <b className="text-ink">Some content is out of reach by design:</b> shapes, SmartArt, charts, equations,
              embedded objects, data-bound content controls and the theme. A change that would touch one is refused with
              a reason, to be made in Word.
            </>,
            <>
              <b className="text-ink">Some steps run before you see anything:</b> a custom method is written and tried on
              copies without a separate click. Nothing is delivered until you approve, but the AI work — and its cost —
              happens. An outcome plan is proposed on its own, but carried out only once you pick it.
            </>,
            <>
              <b className="text-ink">An outcome plan’s wording is not traced to you</b> the way an ordinary plan’s is:
              it is Claude’s, shown beneath the choice, and your pick approves it. Read it before you pick.
            </>,
            <>
              <b className="text-ink">A custom method writes its own prediction.</b> For a built-in operation the
              prediction the checker holds a copy to comes from the engine’s code; for a custom method it comes from
              Claude’s program. The app bounds what that program may predict (no word changed, no part added, no tracked
              change, outside an outcome plan), and anything it did not predict must be unchanged, but its description
              is Claude’s own. Look at the pages.
            </>,
            <>
              <b className="text-ink">The sandboxes are not a security boundary,</b> and they are weaker on Windows than
              on Linux.
            </>,
            <>
              <b className="text-ink">The repair memory crosses projects.</b> Wording from an earlier job can be sent to
              Claude with a later one, as context for a repair.
            </>,
            <>
              <b className="text-ink">Data piles up.</b> Copies, page pictures, transcripts and dropped-file copies are
              never cleaned up for you.
            </>,
            <>
              <b className="text-ink">It is young.</b> The engine and the window are tested on synthetic documents and
              in automated checks; real batches are still finding problems. Treat every delivered copy as a draft until
              you have opened it.
            </>,
          ]}
        />
      </Section>

      <Section id="verify" kicker="The point" title="Check it yourself">
        <P>From least effort to most:</P>
        <ol className="space-y-3">
          {[
            [
              "Read the plan and look at the pages.",
              "Each change is a sentence written by the operation itself; the previews show before and after, side by side. Click a page to enlarge it.",
            ],
            [
              "Open a copy and compare it in Word.",
              "Review → Compare, original against copy, shows every difference Word sees. It should be the change you approved and nothing else.",
            ],
            [
              "Read a copy’s verification record.",
              "In the run’s folder (the folder button beside Your edited copies), records holds one .record.md per file: each check, PASS or FAIL, with what was predicted and what was found. DIAGNOSTICS.md says why a file did not pass. The run’s folder is in the window’s scratch folder, so read it before the window closes, or save a diagnostic bundle, which carries the records.",
            ],
            [
              "Read what was sent to Claude.",
              "The session folder’s conversation.json (in the window’s scratch folder) keeps every request as sent and every answer, with the model that answered, its tokens and its request id. visual_review.json does the same for the visual check (without the images).",
            ],
            [
              "Compare the spending.",
              "Each job’s line in the app log (About → Open diagnostic logs, spec-surgeon.log) gives its model calls and estimated cost. Compare the total with the usage page of Anthropic’s console for your key.",
            ],
            [
              "Watch the network.",
              <>
                With a firewall log or a tool such as Resource Monitor, run a request. You should see{" "}
                <Mono>{F.anthropicHost}</Mono> while work runs, GitHub at start (at most once every{" "}
                {F.updateCheckHours} hours, or never with <Mono>{F.noUpdateCheckEnv}</Mono> set), and nothing else from Spec
                Surgeon. Word and LibreOffice are separate programs and may make connections of their own (Word’s licensing,
                for example). A picture link in Claude’s text would show up as a connection to wherever it points — the known gap.
              </>,
            ],
            [
              "Disconnect the network.",
              "Files still attach and are checked; asking for a change says the request failed. That is the boundary: planning needs Claude, nothing else does.",
            ],
            [
              "Read the code.",
              "It is open source: its own code under the MIT License, the app as a whole, with PyMuPDF, under the GNU Affero General Public License (AGPL). docs/TRUST_CLAIMS.md lists every claim in this document with the code that makes it true and the test that checks it.",
            ],
          ].map(([title, body], i) => (
            <li key={title as string} className="flex gap-3">
              <span className="mt-0.5 flex h-5 w-5 flex-none items-center justify-center rounded-full bg-accent/15 text-[11px] font-semibold text-accent tabular-nums">
                {i + 1}
              </span>
              <div className="text-sm leading-relaxed text-ink-dim">
                <span className="font-medium text-ink">{title}</span> — {body}
              </div>
            </li>
          ))}
        </ol>
        <div className="rounded-xl border border-edge bg-raised/30 p-4">
          <h4 className="text-sm font-medium text-ink">Further reading</h4>
          <ul className="mt-2 space-y-1.5 text-sm leading-relaxed text-ink-dim">
            {FURTHER_READING.map((link) => (
              <li key={link.href}>
                <Link href={link.href}>{link.label}</Link> — {link.what}
              </li>
            ))}
          </ul>
        </div>
        <Note title="Still not convinced?" tone="ok">
          Good. Use it the way it is built to be used: describe one change, read the plan’s sentences, look at the pages,
          approve, then open the copy in Word and compare. Keep your originals as they are. The app’s whole argument is
          that it makes its own work cheap to check — not that you should stop checking.
        </Note>
      </Section>
    </div>
  );
}

/* --------------------------------------------------------------------- */
/* The dialog                                                             */
/* --------------------------------------------------------------------- */

export default function TrustDossier({ open, onClose }: Props) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const [active, setActive] = useState<string>(SECTIONS[0].id);

  // Initial focus, Escape, Tab containment and focus restoration, through the one hook every dialog uses. The
  // dossier stacks on the help dialog; the shared dialog stack makes one Escape close only this one, and containment
  // keeps Shift+Tab from reaching the help dialog's tabs underneath.
  useDialogFocus(open, dialogRef, closeButtonRef, onClose);

  // The contents rail follows the scroll. Rebuilt on every open: the sections exist only while the dialog does.
  useEffect(() => {
    if (!open) return;
    const root = scrollRef.current;
    if (!root || typeof IntersectionObserver === "undefined") return;
    setActive(SECTIONS[0].id);
    const seen = new Map<string, number>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) seen.set(entry.target.id, entry.intersectionRatio);
        let best = "";
        let bestRatio = 0;
        for (const section of SECTIONS) {
          const ratio = seen.get(section.id) ?? 0;
          if (ratio > bestRatio) {
            best = section.id;
            bestRatio = ratio;
          }
        }
        if (best) setActive(best);
      },
      { root, threshold: [0, 0.25, 0.5, 1] },
    );
    for (const section of SECTIONS) {
      const el = root.querySelector(`#${section.id}`);
      if (el) observer.observe(el);
    }
    return () => observer.disconnect();
  }, [open]);

  if (!open) return null;

  const jump = (id: string) => {
    scrollRef.current?.querySelector(`#${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
    setActive(id);
  };

  return (
    // A sibling of the help dialog, not a child: a click on this backdrop closes only the dossier.
    <div
      className="fixed inset-0 z-[80] flex items-start justify-center bg-black/60 p-4 pt-10"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-labelledby="trust-dossier-title"
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        className="flex max-h-[88vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl border border-edge bg-surface shadow-2xl outline-none"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4 border-b border-edge px-6 py-3.5">
          <div>
            <h2 id="trust-dossier-title" className="font-[family-name:var(--font-display)] text-lg font-semibold">
              How Spec Surgeon actually works
            </h2>
            <p className="mt-0.5 text-xs text-ink-faint">
              The long answer: where every change comes from, what runs when you press each button, and how to check all
              of it yourself.
            </p>
          </div>
          <button
            ref={closeButtonRef}
            type="button"
            onClick={onClose}
            className="rounded-lg px-2 py-1 text-ink-dim transition-colors hover:text-ink"
            title="Close"
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        <div className="flex min-h-0 flex-1">
          <nav aria-label="Contents" className="hidden w-60 flex-none overflow-y-auto border-r border-edge px-3 py-4 lg:block">
            <p className="px-2 pb-2 text-[11px] font-medium tracking-wider text-ink-faint uppercase">Contents</p>
            <ul className="space-y-0.5">
              {SECTIONS.map((s, i) => (
                <li key={s.id}>
                  <button
                    type="button"
                    onClick={() => jump(s.id)}
                    aria-current={active === s.id ? "true" : undefined}
                    className={`flex w-full gap-2 rounded-md px-2 py-1.5 text-left text-xs leading-snug transition-colors ${
                      active === s.id ? "bg-accent/15 text-accent" : "text-ink-dim hover:bg-raised hover:text-ink"
                    }`}
                  >
                    <span className="tabular-nums opacity-60">{i + 1}</span>
                    <span>{s.label}</span>
                  </button>
                </li>
              ))}
            </ul>
          </nav>

          <div ref={scrollRef} className="min-w-0 flex-1 overflow-y-auto px-6 py-5">
            <Dossier />
          </div>
        </div>
      </div>
    </div>
  );
}
