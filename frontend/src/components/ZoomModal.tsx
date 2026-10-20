import { useEffect, useRef, useState } from "react";
import { ModalShell, quietBtn } from "./ModalShell";

interface Props {
  src: string;
  title: string;
  onClose: () => void;
}

const MIN = 0.2;
const MAX = 5;

/**
 * The enlarged before/after pages: Fit (the whole picture in the window),
 * 100% (one image pixel per screen pixel), and Ctrl+wheel to zoom freely.
 */
export default function ZoomModal({ src, title, onClose }: Props) {
  const frameRef = useRef<HTMLDivElement>(null);
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null);
  const [mode, setMode] = useState<"fit" | number>("fit");
  const [frameWidth, setFrameWidth] = useState(0);

  const fitScale = natural && frameWidth > 0 ? Math.min(1, (frameWidth - 2) / natural.w) : 1;
  const scale = mode === "fit" ? fitScale : mode;
  const fitRef = useRef(fitScale);
  fitRef.current = fitScale;

  useEffect(() => {
    const el = frameRef.current;
    if (!el) return;
    const measure = () => setFrameWidth(el.clientWidth);
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(el);
    // React registers wheel listeners as passive, so the browser's own
    // Ctrl+wheel zoom could not be stopped from an onWheel prop; a native
    // non-passive listener can.
    const onWheel = (e: globalThis.WheelEvent) => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      const factor = Math.pow(1.1, -e.deltaY / 100);
      setMode((current) => {
        const base = current === "fit" ? fitRef.current : current;
        return Math.max(MIN, Math.min(MAX, base * factor));
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      observer?.disconnect();
      el.removeEventListener("wheel", onWheel);
    };
  }, []);

  const percent = Math.round(scale * 100);
  const width = natural ? Math.round(natural.w * scale) : undefined;

  return (
    <ModalShell title={title} onClose={onClose} xwide>
      <div className="flex items-center gap-2 pb-3">
        <button
          type="button"
          onClick={() => setMode("fit")}
          aria-pressed={mode === "fit"}
          className={`${quietBtn} ${mode === "fit" ? "border-accent text-accent" : ""}`}
          title="Fit the whole picture in the window"
        >
          Fit
        </button>
        <button
          type="button"
          onClick={() => setMode(1)}
          aria-pressed={mode === 1}
          className={`${quietBtn} ${mode === 1 ? "border-accent text-accent" : ""}`}
          title="One image pixel per screen pixel"
        >
          100%
        </button>
        <span className="ml-2 text-xs text-ink-dim tabular-nums">{percent}%</span>
        <span className="ml-auto text-[11px] text-ink-faint">Ctrl + mouse wheel to zoom · Esc to close</span>
      </div>
      <div ref={frameRef} className="max-h-[calc(100vh-15rem)] overflow-auto rounded-lg border border-edge bg-white">
        <img
          src={src}
          alt={title}
          onLoad={(e) => setNatural({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })}
          style={width ? { width, maxWidth: "none" } : undefined}
          className={width ? "block" : "block w-full"}
          draggable={false}
        />
      </div>
    </ModalShell>
  );
}
