import { useState } from "react";
import { OBLITERATE_ACKNOWLEDGE, OBLITERATE_COSTS, OBLITERATE_GRANTS, OBLITERATE_KEPT } from "../lib/modes";
import { ModalShell, primaryBtn, quietBtn } from "./ModalShell";

interface Props {
  /** The conversation needs an API key; without one the warning says so and cannot be confirmed. */
  keyReady: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

/**
 * Obliterate's warning, shown every time the mode is chosen (CLAUDE.md, Modes): what it grants, what
 * it costs and what still holds, then a box to tick before the button turns it on. Nothing is remembered: the next
 * time it asks again, and a launch never opens in Obliterate.
 */
export default function ObliterateWarning({ keyReady, onCancel, onConfirm }: Props) {
  const [understood, setUnderstood] = useState(false);
  return (
    <ModalShell title="Obliterate mode" onClose={onCancel} wide>
      <div className="flex flex-col gap-3 text-sm">
        <p className="text-ink-dim">
          The conversation without the guardrails that protect your documents’ content. Use it only for a change the
          other modes refuse, and only when you will check every copy yourself.
        </p>
        <WarnList title="What it grants" items={OBLITERATE_GRANTS} tone="warn" />
        <WarnList title="What it costs" items={OBLITERATE_COSTS} tone="err" />
        <WarnList title="What still holds" items={OBLITERATE_KEPT} tone="ok" />
        {!keyReady && (
          <p className="text-xs text-warn" role="status">
            Obliterate mode uses the AI: connect your API key in Settings first.
          </p>
        )}
        <label className="flex cursor-pointer items-start gap-2 rounded-lg border border-err/40 bg-err/5 px-3 py-2 text-xs text-ink">
          <input
            type="checkbox"
            className="mt-0.5 h-3.5 w-3.5 flex-none accent-[var(--color-err)]"
            checked={understood}
            onChange={(e) => setUnderstood(e.target.checked)}
          />
          {OBLITERATE_ACKNOWLEDGE}
        </label>
        <div className="flex justify-end gap-2">
          <button type="button" className={quietBtn} onClick={onCancel}>
            Stay in this mode
          </button>
          <button
            type="button"
            className={primaryBtn}
            disabled={!understood || !keyReady}
            onClick={onConfirm}
            data-capability="obliterate.warning"
          >
            Turn on Obliterate
          </button>
        </div>
      </div>
    </ModalShell>
  );
}

function WarnList({ title, items, tone }: { title: string; items: readonly string[]; tone: "warn" | "err" | "ok" }) {
  const border = tone === "err" ? "border-err" : tone === "warn" ? "border-warn" : "border-ok";
  return (
    <section className={`rounded-lg border-l-2 ${border} bg-raised px-3 py-2`}>
      <h3 className="text-xs font-semibold text-ink">{title}</h3>
      <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs leading-relaxed text-ink-dim">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </section>
  );
}
