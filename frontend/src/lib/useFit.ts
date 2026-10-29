import { useLayoutEffect, useState, type RefObject } from "react";
import { flushSync } from "react-dom";

/**
 * How many steps a one-line bar has given way (0 for none) so that it holds what it shows without spilling over its
 * width: measured on the page, never guessed from the window's width, because what the bar shows changes and so does
 * its font (Segoe UI in the app). `content` names what the bar shows, never what it has given way: when it changes, or
 * the bar's width does, the bar starts again with nothing given way and takes one step at a time until it fits or has
 * no step left. Each step is taken before the browser paints, so a frame with overlapping words is never shown.
 */
export function useFit(ref: RefObject<HTMLElement | null>, content: string, steps: number): number {
  const [fit, setFit] = useState({ content, step: 0 });
  let step = fit.step;
  if (fit.content !== content) {
    // New words in the bar: start again (React's "state from the previous render", re-rendered before any commit).
    step = 0;
    setFit({ content, step });
  }
  // After every render: one more step while the bar spills over.
  useLayoutEffect(() => {
    const bar = ref.current;
    if (bar && step < steps && spills(bar)) setFit((f) => ({ ...f, step: f.step + 1 }));
  });
  // A wider or narrower window: start again, within the same frame.
  useLayoutEffect(() => {
    const bar = ref.current;
    if (!bar || typeof ResizeObserver === "undefined") return;
    let width = bar.clientWidth;
    const observer = new ResizeObserver(() => {
      if (bar.clientWidth === width) return;
      width = bar.clientWidth;
      flushSync(() => setFit((f) => ({ ...f, step: 0 })));
    });
    observer.observe(bar);
    return () => observer.disconnect();
  }, [ref]);
  return step;
}

/** Whether something in the bar reaches past its content box: into its padding (which `scrollWidth` counts as room) or beyond. */
function spills(bar: HTMLElement): boolean {
  const end = bar.getBoundingClientRect().right - parseFloat(getComputedStyle(bar).paddingRight);
  return Array.from(bar.children).some((child) => child.getBoundingClientRect().right > end + 0.5);
}
