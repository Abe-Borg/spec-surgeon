// Copied from Build-a-Spec's frontend/src/lib/useSmoothText.ts (copy-based reuse); changed here so the loop
// runs only while there is text to catch up on — the live bubble stays mounted through minutes of rendering.
import { useEffect, useRef, useState } from "react";

/** True when the user asked for reduced motion (live-updates on change). */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () =>
      typeof window !== "undefined" &&
      !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches,
  );
  useEffect(() => {
    const mq = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    if (!mq) return;
    const on = () => setReduced(mq.matches);
    mq.addEventListener?.("change", on);
    return () => mq.removeEventListener?.("change", on);
  }, []);
  return reduced;
}

/**
 * Typewriter smoothing for streamed text. Deltas arrive in network bursts;
 * this drains toward the latest `target` a few characters per animation
 * frame so the text flows instead of jumping in chunks. The catch-up rate
 * scales with the backlog (`ceil(backlog / 30)`, min 2) so it never lags the
 * stream by more than ~half a second. Flushes instantly when `active` goes
 * false (turn end / unmount) and when `prefers-reduced-motion` is set. The
 * frame loop stops once the text has caught up and starts again with the
 * next delta, so a bubble waiting on a long step asks for no frames at all.
 */
export function useSmoothText(target: string, active: boolean): string {
  const reduced = usePrefersReducedMotion();
  const [displayed, setDisplayed] = useState(target);
  const targetRef = useRef(target);
  const displayedRef = useRef(target);
  const rafRef = useRef<number | null>(null);
  targetRef.current = target;

  // Snap (no animation) when reduced-motion is on, the stream has ended, or
  // the target shrank out from under us (a fresh message reusing the hook).
  const snap = reduced || !active || target.length < displayedRef.current.length;

  const stopLoop = () => {
    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
  };

  useEffect(() => {
    if (!snap) return;
    stopLoop();
    displayedRef.current = targetRef.current;
    setDisplayed(targetRef.current);
  }, [snap]);

  // Start draining when text is behind; a running loop reads the ref each
  // frame, so later deltas join it instead of starting another.
  useEffect(() => {
    if (snap || rafRef.current !== null || displayedRef.current.length >= target.length) return;
    rafRef.current = requestAnimationFrame(function tick() {
      const t = targetRef.current;
      const cur = displayedRef.current;
      if (cur.length >= t.length) {
        rafRef.current = null;
        return;
      }
      const step = Math.max(2, Math.ceil((t.length - cur.length) / 30));
      const next = t.slice(0, cur.length + step);
      displayedRef.current = next;
      setDisplayed(next);
      rafRef.current = requestAnimationFrame(tick);
    });
  }, [snap, target]);

  useEffect(() => stopLoop, []);

  // While snapping — reduced motion, stream end, or a shrunk target — the
  // effect only re-runs when `snap` flips, so `displayed` can lag a growing
  // target. Return the live target directly in that case so a
  // reduced-motion stream never sits blank; the animated path still uses
  // the smoothed `displayed`.
  return snap ? target : displayed;
}

/** Split streamed markdown at the last paragraph break: `[stablePrefix, liveTail]`. */
export function splitStableTail(text: string): [string, string] {
  const idx = text.lastIndexOf("\n\n");
  if (idx === -1) return ["", text];
  return [text.slice(0, idx + 2), text.slice(idx + 2)];
}
