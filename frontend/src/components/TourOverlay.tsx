// Adapted from Build-a-Spec's frontend/src/components/OnboardingOverlay.tsx (copy-based reuse): the spotlight on a
// real control, the step card beside it (draggable, never taller than the window, never blocking the app), the
// chapter break, the preparing and ending cards, and the End confirmation. Nothing here changes the practice
// workspace: the server builds each chapter's state (lib/useTour.ts asks for it) and the card only explains.
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { View } from "../types";
import { TOUR, anchorSelector, stepNeedsMet, type TourStep } from "../lib/tour";
import type { TourApi } from "../lib/useTour";
import { usePrefersReducedMotion } from "../lib/useReveal";
import { ModalShell, primaryBtn, quietBtn } from "./ModalShell";

interface Props {
  tour: TourApi;
  view: View;
  /** A job runs in the practice workspace (a button the user pressed): Continue waits for it. */
  busy: boolean;
}

interface Rect {
  top: number;
  left: number;
  width: number;
  height: number;
}

const CUTOUT_PAD = 6;
/** Stamps the tour's own dialogs, so the Escape guard below can tell them from the window's. */
export const TOUR_DIALOG = "tour";
/** Where a step's words go when its control is not on screen: the workbench, the real thing, never a pretend one. */
const FALLBACK_SELECTOR = '[data-tour="workbench"]';

/** A dialog stacked over the tour owns Escape: Settings, Help, the zoom. The step card is aria-modal="false". */
function anotherDialogOwnsEscape(): boolean {
  return Array.from(document.querySelectorAll('[role="dialog"][aria-modal="true"]')).some(
    (dialog) => dialog.getAttribute("data-dialog") !== TOUR_DIALOG,
  );
}

function useAnchorRect(
  selector: string | null,
  key: string,
  reducedMotion: boolean,
): { rect: Rect | null; missing: boolean } {
  const [rect, setRect] = useState<Rect | null>(null);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    setRect(null);
    setMissing(false);
    if (!selector) return;
    let cancelled = false;
    let timer = 0;
    let frame = 0;
    let observer: ResizeObserver | null = null;
    let untrack = () => {};

    const resolve = (attempt = 0) => {
      if (cancelled) return;
      let target: Element | null = null;
      try {
        target = document.querySelector(selector);
      } catch {
        target = null;
      }
      if (!target) {
        if (attempt < 13) {
          timer = window.setTimeout(() => resolve(attempt + 1), 150);
          return;
        }
        // The control is not on screen in this state: the words go beside the workbench and say so.
        target = document.querySelector(FALLBACK_SELECTOR);
        setMissing(true);
        if (!target) return;
      }
      target.scrollIntoView({ block: "center", behavior: reducedMotion ? "auto" : "smooth" });
      const commit = () => {
        const box = target!.getBoundingClientRect();
        setRect({ top: box.top, left: box.left, width: box.width, height: box.height });
      };
      const remeasure = () => {
        cancelAnimationFrame(frame);
        frame = requestAnimationFrame(commit);
      };
      frame = requestAnimationFrame(commit);
      window.addEventListener("resize", remeasure);
      window.addEventListener("scroll", remeasure, true);
      observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(remeasure);
      observer?.observe(target);
      untrack = () => {
        window.removeEventListener("resize", remeasure);
        window.removeEventListener("scroll", remeasure, true);
      };
    };

    resolve();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      cancelAnimationFrame(frame);
      observer?.disconnect();
      untrack();
    };
  }, [selector, key, reducedMotion]);

  return { rect, missing };
}

const clamp = (value: number, low: number, high: number) => Math.min(Math.max(value, low), Math.max(low, high));

function placeCard(rect: Rect, placement: TourStep["placement"], width: number, height: number) {
  const margin = 12;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const fits = {
    top: rect.top - height - margin >= 8,
    bottom: rect.top + rect.height + margin + height <= vh - 8,
    left: rect.left - width - margin >= 8,
    right: rect.left + rect.width + margin + width <= vw - 8,
  };
  let side = placement ?? "bottom";
  if (!fits[side]) {
    const opposite = { top: "bottom", bottom: "top", left: "right", right: "left" }[side] as NonNullable<
      TourStep["placement"]
    >;
    side = fits[opposite] ? opposite : fits.bottom ? "bottom" : fits.top ? "top" : fits.right ? "right" : "left";
  }
  const vertical = side === "top" || side === "bottom";
  const top = vertical
    ? side === "top"
      ? rect.top - height - margin
      : rect.top + rect.height + margin
    : rect.top + rect.height / 2 - height / 2;
  const left = vertical
    ? rect.left + rect.width / 2 - width / 2
    : side === "left"
      ? rect.left - width - margin
      : rect.left + rect.width + margin;
  return { top: clamp(top, 8, vh - height - 8), left: clamp(left, 8, vw - width - 8) };
}

function clampOffset(offset: { dx: number; dy: number }, base: { top: number; left: number }, element: HTMLElement) {
  const vh = window.innerHeight;
  const vw = window.innerWidth;
  const verticalHigh = vh - element.offsetHeight - 8;
  const horizontalHigh = vw - element.offsetWidth - 8;
  const top = clamp(base.top + offset.dy, Math.min(8, verticalHigh), Math.max(8, verticalHigh));
  const left = clamp(base.left + offset.dx, Math.min(8, horizontalHigh), Math.max(8, horizontalHigh));
  return { dx: left - base.left, dy: top - base.top };
}

function AnchoredCard({
  rect,
  pending,
  placement,
  resetKey,
  children,
}: {
  rect: Rect | null;
  pending: boolean;
  placement: TourStep["placement"];
  resetKey: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<{ top: number; left: number } | null>(null);
  const [offset, setOffset] = useState({ dx: 0, dy: 0 });
  const dragRef = useRef<{ pointerId: number; startX: number; startY: number; baseDx: number; baseDy: number } | null>(
    null,
  );

  useLayoutEffect(() => {
    const element = ref.current;
    if (!element || !rect) {
      setPosition(null);
      return;
    }
    const measure = () => {
      const next = placeCard(rect, placement, element.offsetWidth, element.offsetHeight);
      setPosition(next);
      setOffset((current) => clampOffset(current, next, element));
    };
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(element);
    return () => observer?.disconnect();
  }, [rect, placement]);

  useEffect(() => {
    setOffset({ dx: 0, dy: 0 });
    dragRef.current = null;
  }, [resetKey]);

  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || dragRef.current || !position) return;
    const target = event.target;
    if (!(target instanceof HTMLElement) || !target.closest("[data-drag-handle]")) return;
    const element = ref.current;
    if (!element) return;
    dragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      baseDx: offset.dx,
      baseDy: offset.dy,
    };
    element.setPointerCapture(event.pointerId);
  };
  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    const element = ref.current;
    if (!drag || !element || !position || drag.pointerId !== event.pointerId) return;
    setOffset(
      clampOffset(
        { dx: drag.baseDx + (event.clientX - drag.startX), dy: drag.baseDy + (event.clientY - drag.startY) },
        position,
        element,
      ),
    );
  };
  const endDrag = (event: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    dragRef.current = null;
    ref.current?.releasePointerCapture(event.pointerId);
  };

  const style = rect
    ? position
      ? { top: position.top + offset.dy, left: position.left + offset.dx }
      : { top: -9999, left: -9999 }
    : pending
      ? { top: -9999, left: -9999 }
      : { right: 16, bottom: 16 };
  return (
    <div
      ref={ref}
      role="dialog"
      aria-modal="false"
      aria-label="Guided tour"
      data-capability="tour.controls"
      className="pointer-events-auto fixed z-[65] flex max-h-[calc(100vh-16px)] w-[390px] max-w-[calc(100vw-16px)] flex-col rounded-xl border border-edge bg-surface p-4 shadow-2xl"
      style={style}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
    >
      {children}
    </div>
  );
}

function CardHeader({ kicker, title, onClose }: { kicker: string; title: string; onClose: () => void }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div data-drag-handle title="Drag to move" className="min-w-0 flex-1 cursor-grab touch-none select-none active:cursor-grabbing">
        <p className="text-[11px] tracking-wide text-ink-faint uppercase">{kicker}</p>
        <h3 className="mt-0.5 font-[family-name:var(--font-display)] text-base font-semibold text-ink">{title}</h3>
      </div>
      <button
        type="button"
        onClick={onClose}
        aria-label="End the tour"
        className="rounded-md px-1.5 py-0.5 text-ink-dim transition-colors hover:bg-raised hover:text-ink"
      >
        ✕
      </button>
    </div>
  );
}

function Cutout({ rect, reducedMotion }: { rect: Rect; reducedMotion: boolean }) {
  return (
    <div
      aria-hidden
      className="pointer-events-none fixed z-[61] rounded-[10px]"
      style={{
        top: rect.top - CUTOUT_PAD,
        left: rect.left - CUTOUT_PAD,
        width: rect.width + CUTOUT_PAD * 2,
        height: rect.height + CUTOUT_PAD * 2,
        boxShadow: "0 0 0 9999px color-mix(in srgb, var(--color-ink) 55%, transparent), 0 0 0 2px var(--color-accent)",
        transition: reducedMotion ? "none" : "top .2s ease,left .2s ease,width .2s ease,height .2s ease",
      }}
    />
  );
}

const END_TIP = "End the tour and return to your conversation exactly as you left it";

export default function TourOverlay({ tour, view, busy }: Props) {
  const { phase } = tour;
  const reducedMotion = usePrefersReducedMotion();
  const step = phase.kind === "touring" ? TOUR[phase.chapter].steps[phase.step] : null;
  const ready = step ? stepNeedsMet(step, view) : true;
  const selector = step ? (ready ? anchorSelector(step) : FALLBACK_SELECTOR) : null;
  const stepKey = phase.kind === "touring" ? `${phase.chapter}:${phase.step}:${ready}` : "";
  const { rect, missing } = useAnchorRect(selector, stepKey, reducedMotion);
  const placement = useMemo<TourStep["placement"]>(() => (ready && step ? step.placement : "left"), [ready, step]);

  // Escape asks to end, as every ✕ does; the confirmation keeps a stray keypress from throwing the tour away. It
  // yields while that confirmation, or any other dialog of the window, owns the keyboard.
  useEffect(() => {
    if (phase.kind === "idle") return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || tour.endConfirm || event.defaultPrevented) return;
      if (anotherDialogOwnsEscape()) return;
      if (phase.kind === "touring" || phase.kind === "break") tour.requestEnd();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [phase.kind, tour]);

  if (phase.kind === "idle") return null;

  const confirm = tour.endConfirm && (
    <ModalShell title="End the guided tour?" onClose={tour.cancelEnd} marker={TOUR_DIALOG}>
      <p className="text-sm leading-relaxed text-ink-dim">
        Your own conversation comes back exactly as it was before the tour, the same documents, messages and copies,
        and the practice workspace is discarded. Tour in the top bar starts it again at any time.
      </p>
      <div className="mt-4 flex justify-end gap-2">
        <button type="button" className={quietBtn} onClick={tour.cancelEnd}>
          Continue the tour
        </button>
        <button type="button" className={primaryBtn} onClick={tour.end} data-capability="tour.finish">
          End the tour
        </button>
      </div>
    </ModalShell>
  );

  if (phase.kind === "preparing") {
    const finishing = phase.stage === "finishing";
    const label =
      phase.stage === "starting"
        ? "Setting your conversation aside and opening the practice workspace…"
        : phase.stage === "chapter"
          ? `Preparing the chapter “${phase.label ?? "next"}”…`
          : "Returning your conversation…";
    return (
      <>
        <ModalShell
          title={phase.error ? (finishing ? "Your conversation could not be returned yet" : "The tour needs attention") : "One moment"}
          marker={TOUR_DIALOG}
          onClose={finishing ? (phase.error ? tour.stayInTour : () => {}) : tour.requestEnd}
        >
          {phase.error ? (
            <>
              <p className="text-sm leading-relaxed text-err">{phase.error}</p>
              {finishing && (
                <p className="mt-2 text-[11px] leading-snug text-ink-faint">
                  Your conversation is still set aside, untouched. If work is still running in the practice
                  workspace, stop it or let it finish, then retry.
                </p>
              )}
              <div className="mt-4 flex flex-wrap gap-2">
                <button type="button" onClick={tour.retry} className={primaryBtn}>
                  {finishing ? "Retry returning my conversation" : "Retry"}
                </button>
                <button type="button" onClick={finishing ? tour.stayInTour : tour.requestEnd} className={quietBtn}>
                  {finishing ? "Back to the tour" : "End the tour"}
                </button>
              </div>
            </>
          ) : (
            <>
              <p className="text-sm leading-relaxed text-ink-dim">{label}</p>
              <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-raised">
                <span className="block h-full w-1/2 animate-pulse rounded-full bg-accent" />
              </div>
              <p className="mt-2 text-[11px] text-ink-faint">
                The practice workspace is built from the bundled documents by the same engine that edits yours: real
                previews and real copies, no AI call, nothing of yours.
              </p>
              {!finishing && (
                <div className="mt-4">
                  <button type="button" onClick={tour.requestEnd} className={quietBtn} title={END_TIP} data-capability="tour.finish">
                    End the tour
                  </button>
                </div>
              )}
            </>
          )}
        </ModalShell>
        {confirm}
      </>
    );
  }

  if (phase.kind === "break") {
    const done = TOUR[phase.next - 1];
    const next = TOUR[phase.next];
    return (
      <>
        <ModalShell title={`Chapter ${phase.next} of ${TOUR.length} done — ${done.title}`} marker={TOUR_DIALOG} onClose={tour.requestEnd}>
          <div className="flex items-center gap-1.5" aria-hidden>
            {TOUR.map((chapter, index) => (
              <span key={chapter.id} className={`h-1.5 flex-1 rounded-full ${index < phase.next ? "bg-accent" : "bg-raised"}`} />
            ))}
          </div>
          <p className="mt-3 text-sm leading-relaxed text-ink-dim">
            Next: <b className="text-ink">{next.title}</b>. The step cards sit beside the real thing, so you can look at
            the documents and the pages as you go.
          </p>
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <button type="button" onClick={tour.continueChapter} className={primaryBtn}>
              Continue
            </button>
            <span className="flex-1" />
            <button type="button" onClick={tour.requestEnd} className={quietBtn} title={END_TIP} data-capability="tour.finish">
              End the tour
            </button>
          </div>
        </ModalShell>
        {confirm}
      </>
    );
  }

  const chapter = TOUR[phase.chapter];
  const current = chapter.steps[phase.step];
  const atFirst = phase.chapter === 0 && phase.step === 0;
  return (
    <>
      <div className="pointer-events-none fixed inset-0 z-[60]">
        {rect && <Cutout rect={rect} reducedMotion={reducedMotion} />}
        <AnchoredCard rect={rect} pending={!missing} placement={placement} resetKey={stepKey}>
          <CardHeader
            kicker={`Chapter ${phase.chapter + 1}/${TOUR.length} — ${chapter.title} · ${phase.step + 1}/${chapter.steps.length}`}
            title={current.title}
            onClose={tour.requestEnd}
          />
          <div key={stepKey} data-testid="tour-card-body" className="-mr-2 min-h-0 flex-1 overflow-y-auto overscroll-contain pr-2">
            <div className="mt-1 flex items-center gap-2">
              <span className="rounded-full border border-edge px-2 py-0.5 text-[10px] tracking-wide text-ink-faint uppercase">
                {current.mode}
              </span>
              {current.optionalReason && <span className="text-[10px] text-ink-faint">Optional: {current.optionalReason}</span>}
            </div>
            {ready && !missing ? (
              <p className="mt-2 text-sm leading-relaxed text-ink-dim">{current.body}</p>
            ) : (
              <div className="mt-2 rounded-lg border border-warn/40 bg-warn/10 p-3">
                <p className="text-sm leading-relaxed text-ink-dim">{current.body}</p>
                <p className="mt-2 text-sm leading-relaxed text-ink-dim">
                  {ready
                    ? "This control is not on screen in the window's current state, so the tour keeps its explanation beside the workbench rather than showing a pretend control. You can continue."
                    : "The practice workspace does not hold what this step points at right now (a click in it may have changed it). The explanation stands, nothing is invented to fill the gap, and you can continue."}
                </p>
              </div>
            )}
          </div>
          <div className="mt-4 flex shrink-0 flex-wrap items-center gap-2">
            {!atFirst && (
              <button type="button" onClick={tour.back} className={quietBtn}>
                ‹ Back
              </button>
            )}
            <button type="button" onClick={tour.advance} disabled={busy} className={primaryBtn} title={busy ? "Wait for the work in the practice workspace to finish" : undefined}>
              {current.continueLabel ?? "Continue"}
            </button>
            <span className="flex-1" />
            <button type="button" onClick={tour.requestEnd} className={quietBtn} title={END_TIP} data-capability="tour.finish">
              End
            </button>
          </div>
        </AnchoredCard>
      </div>
      {confirm}
    </>
  );
}
