// The reduced-motion hook is copied from Build-a-Spec's frontend/src/lib/useSmoothText.ts (copy-based reuse); the
// reveal replaces that file's typewriter: time-based pacing, pieces that fade in, a finished reply that carries on.
import { useEffect, useRef, useState } from "react";
import { alignShown, revealStep, safeCut } from "./reveal";

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

export interface Revealed {
  /** The part of the text on the page now. */
  shown: string;
  /** Where each revealed piece begins in `shown`, ascending. Read during the render; never kept. */
  marks: readonly number[];
  /** When each piece was revealed (`performance.now()`), beside `marks`. */
  times: readonly number[];
  /** Text is still waiting to be shown. */
  flowing: boolean;
}

/**
 * The text as it comes onto the page. A text the hook mounts with shows at
 * once (a reply from history); whatever it grows by afterwards is revealed
 * over the next frames at `revealStep`'s pace, each frame's piece marked so
 * it can fade in. A text that changes under the words already shown (the
 * finished reply joins its parts with its own spacing) carries on from where
 * those words end in it (`alignShown`); one that shrinks shows at once.
 * The frame loop runs only while text is waiting, and reduced motion shows
 * everything at once.
 */
export function useReveal(target: string): Revealed {
  const reduced = usePrefersReducedMotion();
  const state = useRef({ shown: target, marks: [] as number[], times: [] as number[], carry: 0 });
  const targetRef = useRef(target);
  const raf = useRef<number | null>(null);
  const [, setFrame] = useState(0);
  targetRef.current = target;

  const settle = (text: string) => {
    const s = state.current;
    if (text.startsWith(s.shown)) return;
    const { at } = alignShown(s.shown, text);
    s.shown = text.slice(0, at);
    let keep = s.marks.length;
    while (keep > 0 && s.marks[keep - 1] >= at) keep -= 1;
    s.marks.length = keep;
    s.times.length = keep;
  };

  // Settled in the render itself (idempotent), so no frame ever shows words the text no longer has.
  if (reduced) {
    state.current = { shown: target, marks: [], times: [], carry: 0 };
  } else {
    settle(target);
  }

  useEffect(() => {
    if (reduced || raf.current !== null || state.current.shown.length >= target.length) return;
    let last = 0;
    const tick = (now: number) => {
      const text = targetRef.current;
      settle(text);
      const s = state.current;
      const backlog = text.length - s.shown.length;
      if (backlog <= 0) {
        raf.current = null;
        return;
      }
      // The first frame after a pause counts as one frame, not as the whole pause.
      const dt = last ? Math.min((now - last) / 1000, 0.25) : 1 / 60;
      last = now;
      s.carry += revealStep(backlog, dt);
      const take = Math.floor(s.carry);
      if (take > 0) {
        s.carry -= take;
        s.marks.push(s.shown.length);
        s.times.push(now);
        s.shown = text.slice(0, safeCut(text, s.shown.length + take));
        setFrame((n) => n + 1);
      }
      raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
  }, [target, reduced]);

  useEffect(
    () => () => {
      if (raf.current !== null) cancelAnimationFrame(raf.current);
      raf.current = null;
    },
    [],
  );

  const s = state.current;
  return { shown: s.shown, marks: s.marks, times: s.times, flowing: s.shown.length < target.length };
}
