import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

/** A little longer than `.fold`'s transition in index.css, so the content leaves after it has folded away. */
const CLOSE_MS = 420;

interface Props {
  open: boolean;
  children: ReactNode;
  /** Animate the opening too. Off for content that is already on screen elsewhere and only moves in here
   *  (words set aside as a step), which would otherwise fold shut in one place and unfold in the other. */
  enter?: boolean;
  className?: string;
}

/**
 * Content that opens and closes by animating its height and opacity (grid rows 0fr ↔ 1fr), so nothing beside it
 * jumps. It mounts open when it is open from the start; while it closes it keeps showing what it held when it was
 * last open, and it leaves the page once closed.
 */
export default function Collapse({ open, children, enter = true, className = "" }: Props) {
  const [present, setPresent] = useState(open);
  const [expanded, setExpanded] = useState(open);
  const ref = useRef<HTMLDivElement>(null);
  const kept = useRef<ReactNode>(children);
  if (open) kept.current = children;

  useLayoutEffect(() => {
    if (!open) {
      if (expanded) setExpanded(false);
      return;
    }
    if (!present) {
      // Mounted closed first (or, without the entrance, open at once).
      setPresent(true);
      if (!enter) setExpanded(true);
      return;
    }
    if (!expanded) {
      // The closed size is laid out before it changes, so the change animates.
      void ref.current?.offsetHeight;
      setExpanded(true);
    }
  }, [open, present, expanded, enter]);

  useEffect(() => {
    if (open || !present) return;
    const timer = window.setTimeout(() => setPresent(false), CLOSE_MS);
    return () => window.clearTimeout(timer);
  }, [open, present]);

  if (!present) return null;
  return (
    <div ref={ref} className={`fold ${expanded ? "is-open" : ""} ${className}`} aria-hidden={open ? undefined : true}>
      <div className="fold-inner">{open ? children : kept.current}</div>
    </div>
  );
}
