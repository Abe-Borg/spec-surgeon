import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { About } from "../types";
import { getAbout } from "../lib/api";
import { ModalShell } from "./ModalShell";

const REMARK_PLUGINS = [remarkGfm];

/** About Spec Surgeon: the version, the license and the release notes (GET /api/about). */
export default function AboutModal({ onClose, fallbackVersion }: { onClose: () => void; fallbackVersion: string }) {
  const [about, setAbout] = useState<About | null>(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<"notes" | "license">("notes");

  useEffect(() => {
    let alive = true;
    getAbout()
      .then((a) => alive && setAbout(a))
      .catch((failure: unknown) => alive && setError(failure instanceof Error ? failure.message : String(failure)));
    return () => {
      alive = false;
    };
  }, []);

  const version = about?.version || fallbackVersion;
  const tabBtn = (id: "notes" | "license", label: string) => (
    <button
      type="button"
      role="tab"
      aria-selected={tab === id}
      onClick={() => setTab(id)}
      className={`rounded-full px-3 py-1 text-xs transition-colors ${
        tab === id ? "bg-raised font-medium text-ink" : "text-ink-dim hover:text-ink"
      }`}
    >
      {label}
    </button>
  );

  return (
    <ModalShell title="About Spec Surgeon" onClose={onClose} wide>
      <div className="flex flex-col gap-3">
        <div>
          <p className="font-[family-name:var(--font-display)] text-xl text-ink">
            Spec Surgeon{version ? ` ${version}` : ""}
          </p>
          <p className="mt-0.5 text-sm text-ink-dim">
            Surgical edits to Word specifications with independent verification records.
          </p>
        </div>
        {error && (
          <p className="text-xs text-err" role="alert">
            {error}
          </p>
        )}
        <div role="tablist" className="flex items-center gap-1 border-b border-edge pb-2">
          {tabBtn("notes", "Release notes")}
          {tabBtn("license", "License")}
        </div>
        {tab === "notes" ? (
          <div className="md max-h-[50vh] overflow-y-auto pr-2 text-sm text-ink-dim">
            {about ? (
              about.release_notes ? (
                <ReactMarkdown remarkPlugins={REMARK_PLUGINS}>{about.release_notes}</ReactMarkdown>
              ) : (
                <p>No release notes for this version.</p>
              )
            ) : (
              !error && <p className="text-ink-faint">Loading…</p>
            )}
          </div>
        ) : (
          <div className="max-h-[50vh] overflow-y-auto pr-2">
            <pre className="whitespace-pre-wrap font-[family-name:var(--font-body)] text-xs leading-relaxed text-ink-dim">
              {about?.license || (error ? "" : "Loading…")}
            </pre>
            {about && about.credits?.length > 0 && (
              <div className="mt-3 border-t border-edge pt-3">
                <p className="text-[11px] font-medium tracking-wide text-ink-faint uppercase">
                  Open-source components
                </p>
                <ul className="mt-1 space-y-0.5 text-xs text-ink-dim">
                  {about.credits.map((credit) => (
                    <li key={credit}>{credit}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </div>
    </ModalShell>
  );
}
