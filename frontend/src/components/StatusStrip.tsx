// Adapted from Build-a-Spec's frontend/src/components/StatusStrip.tsx (copy-based reuse).
import type { Status } from "../types";
import { statusLabel } from "../lib/chat";

/**
 * The "app is not frozen" guarantee: a shimmering, pulsing-dots line that
 * names what the app is doing right now. Rendered inside the streaming
 * assistant bubble and replaced on each status frame; hidden the moment
 * real text starts flowing.
 */
export default function StatusStrip({ status }: { status: Status }) {
  return (
    <div className="status-strip" aria-live="polite">
      <span className="status-dots" aria-hidden="true">
        <span />
        <span />
        <span />
      </span>
      <span className="status-shimmer">{statusLabel(status)}</span>
    </div>
  );
}
