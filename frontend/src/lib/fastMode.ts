/**
 * The words Settings says about Claude's fast mode ("Fast responses"), built from what the server reports
 * (GET /api/settings → `fast`: its own price table, never a number typed here). The user turns it on knowing both
 * sides: answers written up to `speedup` times faster by the same model, and every AI step priced at `price_factor`
 * times the standard rate. Pure, so tests/fastMode.test.ts can read them.
 */
import type { FastOffer } from "../types.ts";
import { modelLabel } from "./help.ts";

/** 8 → "$8", 0.4 → "$0.40". */
export function usd(n: number): string {
  return Number.isInteger(n) ? `$${n}` : `$${n.toFixed(2)}`;
}

/** 2 → "twice", 3 → "3 times". */
export function times(n: number): string {
  return n === 2 ? "twice" : `${n} times`;
}

export interface FastWords {
  /** What it buys. */
  benefit: string;
  /** What it costs: the prices, side by side. */
  cost: string;
  /** What it does not change, and how it behaves. */
  notes: string[];
  /** Why the setting does nothing right now (the model is not served fast, or the API refused it), or "". */
  unavailable: string;
  /** The confirming button's words. */
  confirm: string;
}

/** The line under the switch while it is on: green when the next AI step will be fast, a warning when the setting is
 *  on but cannot take effect (`unavailable` says why, above it). Nothing while it is off. */
export function fastStatus(on: boolean, words: FastWords): { tone: "ok" | "warn"; text: string } | null {
  if (!on) return null;
  if (words.unavailable) {
    return { tone: "warn", text: "On, but not in effect: AI steps run at standard speed and standard price. Kept for the next start." };
  }
  return { tone: "ok", text: "On: AI steps from now on are fast, at the fast price. Kept for the next start." };
}

export function fastWords(offer: FastOffer): FastWords {
  const model = modelLabel(offer.model);
  const factor = times(offer.price_factor);
  const std = offer.standard_usd_per_mtok;
  const fast = offer.fast_usd_per_mtok;
  const prices =
    std && fast
      ? ` ${usd(fast[0])} instead of ${usd(std[0])} per million input tokens, and ${usd(fast[1])} instead of ` +
        `${usd(std[1])} per million output tokens; reading and writing Anthropic's cache costs ${factor} as much too.`
      : "";
  let unavailable = "";
  if (!offer.available) {
    unavailable = `${model} is not served fast by Anthropic, so this setting has no effect with it.`;
  } else if (offer.refused) {
    unavailable =
      `Anthropic refused fast mode for this API key, so AI steps run at standard speed and price. ` +
      `Turn the setting off and on again to retry. Anthropic said: ${offer.refused}`;
  }
  return {
    benefit:
      `${model} writes its answers up to ${offer.speedup}× faster. It is the same model at the same effort, and ` +
      `every plan is checked, previewed and verified exactly as before: only the wait changes.`,
    cost: `Every AI step costs ${factor} as much:${prices} A conversation that would cost about ${usd(1)} costs about ${usd(offer.price_factor)}.`,
    notes: [
      "It speeds up Claude's writing and thinking, not the moment before it starts, and not the work done on your computer (rendering, checking, creating copies).",
      "When Anthropic's fast capacity is busy, a step runs at standard speed and standard price instead of waiting.",
      "Switching it during a conversation makes the next step re-read the conversation once without the cache discount: the two speeds are cached separately.",
      "The spending estimate in the top bar uses the fast prices while it is in effect, and says “Fast”.",
    ],
    unavailable,
    confirm: `Turn on — ${factor} the price`,
  };
}
