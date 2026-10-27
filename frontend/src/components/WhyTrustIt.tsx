/**
 * "Why trust it?" — the short help topic for a reader in a hurry: one premise, eight claims each backed by the
 * mechanism that makes it true, and the way into the long answer (TrustDossier). Written for the specification writer
 * or engineer who puts their name on the documents that go to a client, not for a software engineer.
 *
 * Every claim here is in the claims ledger (docs/TRUST_CLAIMS.md) with the code that makes it true, and every number
 * comes from lib/trustFacts.ts. If the implementation changes, this changes with it. A trust document that has
 * drifted from the code is worse than none.
 */
import { MODEL_NAME, TRUST_FACTS as F } from "../lib/trustFacts";
import { Lead, Points, Tag } from "./helpParts";

export default function WhyTrustIt({ onDeepDive }: { onDeepDive: () => void }) {
  return (
    <div className="space-y-4">
      <Lead>
        The whole design assumes you check the result before it goes to a client, so the app works out every change
        before it makes it, proves each copy against that prediction, and never writes over your files.
      </Lead>
      <Points
        items={[
          {
            t: "Your originals are never written",
            d: (
              <>
                The window only creates copies, in its own scratch folder, and writes them where you choose only when
                you press Export. The code that delivers them refuses to write over a source file, and a file that
                changed since you approved it is refused, not guessed at.
              </>
            ),
          },
          {
            t: "Every change is predicted, then proved",
            d: "Before anything changes, the engine works out from your original exactly what will change in every file. After the edit, a separate checker that shares no code with the editing compares the original and the copy against that prediction — that change, and nothing else. A copy that fails is set aside, never delivered — except in Obliterate mode, hidden unless you show it in Settings, which delivers it marked UNVERIFIED with the checks it failed.",
          },
          {
            t: "Claude plans; the engine edits",
            d: (
              <>
                Claude turns your words into a plan of built-in operations, and the plan is checked before you see it.
                When no operation fits, Claude may write a small program instead — formatting only, unless it carries
                out an outcome plan you are shown — which runs in a sandbox on copies, predicts its own change, and must pass
                the same checker. Obliterate mode lifts the formatting-only limit and lets a failed check through,
                never the sandbox or the copies-only rule.
              </>
            ),
          },
          {
            t: "New words come from you",
            d: (
              <>
                Text placed in a document must trace to your request, your answers, a choice you picked, or a file you
                attached; a suggested reply you tap is never used as wording. When the app falls back to an outcome plan
                Claude proposed, that plan’s wording is shown beneath the choice you pick, and only your pick approves
                it. In Obliterate mode, chosen after its warning, Claude may write wording itself; every word it places
                must be in the reply you read, and the app checks that before the plan is previewed.
              </>
            ),
          },
          {
            t: "Nothing is delivered without your click",
            d: (
              <>
                <Tag>Create edited copies</Tag> approves exactly the plan and files on screen. If a document or the plan
                changed since the preview, the click is refused until you have looked again.
                {" "}In Safe and Default modes, a failed preview check also blocks Create; the reason appears beside it.
              </>
            ),
          },
          {
            t: "What runs without you",
            d: `Safe mode makes no AI call at all: its buttons build their plans in code. In Default mode every AI step runs inside work you started — a message, a choice or the button — though one action can chain several: the preview’s visual check, a custom method, recovery of a copy that failed. Without a click the app checks each file as it arrives (no AI, no network) and asks GitHub for a new version at most once every ${F.updateCheckHours} hours, carrying nothing about your documents; ${F.noUpdateCheckEnv} turns that off.`,
          },
          {
            t: "Your documents go to Anthropic, and nowhere else",
            d: `To plan, ${MODEL_NAME} (the default model) reads what it needs — text, structure, rendered pages — over HTTPS to Anthropic’s API, with your own key; only the ${F.apiAddressEnv} environment variable, which the window never sets, could send them elsewhere. There is no Spec Surgeon server, account or upload: copies, records and logs stay on this computer. One known gap: a picture link written into Claude’s reply would be loaded from the web — the long answer explains.`,
          },
          {
            t: "Your key and your spending stay in view",
            d: `The key stays in Windows Credential Manager (if you ask it to be remembered) and goes only to Anthropic. The top bar shows an estimate as it accrues, with Stop beside it to cut off the running step at once, and your spending check (Settings; $${F.spendingCheckDefaultUsd} a request until you change it or turn it off) pauses a request before it could spend more, until you continue. Fast responses, off until you turn it on in Settings, has the same model write up to ${F.fastSpeedup} times faster at ${F.fastPriceFactor} times the price, and the top bar then says Fast.`,
          },
        ]}
      />
      <div className="rounded-xl border border-edge bg-raised/40 p-4">
        <h3 className="text-sm font-medium text-ink">Not convinced?</h3>
        <p className="mt-1 text-xs leading-relaxed text-ink-dim">
          Fair. The points above are claims — here is the mechanism behind each one, action by action, plus how to
          audit any of it yourself.
        </p>
        <button
          type="button"
          onClick={onDeepDive}
          className="mt-3 rounded-lg border border-accent/60 bg-accent/10 px-3 py-1.5 text-xs font-medium text-accent transition-colors hover:bg-accent/20"
        >
          I’m not convinced — show me exactly what runs →
        </button>
      </div>
    </div>
  );
}
