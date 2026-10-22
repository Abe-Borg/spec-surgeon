/**
 * The dossier's data-flow diagram: hand-authored inline SVG on the theme's colour tokens (readable in every scheme),
 * no external asset. Solid lines are what runs routinely while work you started runs; dashed lines are automatic,
 * optional or only-when-you-click connections. The aria-label restates the whole picture in words.
 *
 * If the implementation changes, this changes with it (docs/TRUST_CLAIMS.md §1c lists every destination).
 */
import { MODEL_NAME, TRUST_FACTS as F } from "../lib/trustFacts";

export const DIAGRAM_LABEL =
  `Data flow. Your computer runs the window, its local server on ${F.loopback}, the editing engine, the independent ` +
  "checker, the renderer (Word or LibreOffice) and the sandboxes, and holds your originals, the edited copies, the " +
  "records, the logs and the key. While work you started runs, requests go over HTTPS with your key to the Anthropic " +
  `API (${F.anthropicHost}), where ${MODEL_NAME} answers; they carry your request, document text and structure, file ` +
  `names and page images. Dashed: the automatic update check, at most once every ${F.updateCheckHours} hours, and a ` +
  `download you click, both to GitHub (${F.githubApiHost}, ${F.githubHost}); links you click open in your browser; and ` +
  "a picture link in Claude's text would be fetched from wherever it points.";

export default function TrustDiagram() {
  const label = { fill: "var(--color-ink)", fontSize: 12, fontFamily: "var(--font-body)" } as const;
  const small = { fill: "var(--color-ink-dim)", fontSize: 10.5, fontFamily: "var(--font-body)" } as const;
  const faint = { fill: "var(--color-ink-faint)", fontSize: 10, fontFamily: "var(--font-body)" } as const;
  const box = { fill: "var(--color-raised)", stroke: "var(--color-edge)" } as const;

  return (
    <figure className="rounded-xl border border-edge bg-raised/30 p-4">
      <svg viewBox="0 0 720 330" className="w-full" role="img" aria-label={DIAGRAM_LABEL}>
        <defs>
          <marker id="ss-arrow" markerWidth="9" markerHeight="9" refX="7" refY="4" orient="auto">
            <path d="M0,0 L8,4 L0,8 z" fill="var(--color-accent)" />
          </marker>
          <marker id="ss-arrow-faint" markerWidth="9" markerHeight="9" refX="7" refY="4" orient="auto">
            <path d="M0,0 L8,4 L0,8 z" fill="var(--color-ink-faint)" />
          </marker>
          <marker id="ss-arrow-warn" markerWidth="9" markerHeight="9" refX="7" refY="4" orient="auto">
            <path d="M0,0 L8,4 L0,8 z" fill="var(--color-warn)" />
          </marker>
        </defs>

        {/* Your computer */}
        <rect x="6" y="14" width="300" height="238" rx="12" fill="var(--color-surface)" stroke="var(--color-ok)" strokeWidth="1.5" />
        <text x="20" y="38" {...label} fontWeight="600">Your computer</text>
        <text x="20" y="54" {...faint}>Everything in this box stays here</text>

        <rect x="20" y="64" width="272" height="56" rx="7" {...box} />
        <text x="32" y="82" {...small}>The window · its server on {F.loopback}</text>
        <text x="32" y="98" {...small}>Engine · independent checker · sandboxes</text>
        <text x="32" y="113" {...small}>Word or LibreOffice (renders the pages)</text>

        <rect x="20" y="128" width="272" height="112" rx="7" {...box} />
        <text x="32" y="146" {...small}>Your originals (read, never written)</text>
        <text x="32" y="163" {...small}>Edited copies · records · previews</text>
        <text x="32" y="180" {...small}>Conversations · logs · repair memory</text>
        <text x="32" y="197" {...small}>Dropped-file copies · render cache</text>
        <text x="32" y="214" {...small}>Your key (Windows Credential Manager)</text>
        <text x="32" y="231" {...faint}>No upload, no telemetry, no account</text>

        {/* To and from Anthropic */}
        <text x="314" y="84" {...faint}>HTTPS · your key</text>
        <line x1="310" y1="94" x2="446" y2="94" stroke="var(--color-accent)" strokeWidth="1.5" markerEnd="url(#ss-arrow)" />
        <text x="314" y="110" {...faint}>request · document text</text>
        <text x="314" y="123" {...faint}>names · page images</text>
        <line x1="446" y1="146" x2="310" y2="146" stroke="var(--color-accent)" strokeWidth="1.5" markerEnd="url(#ss-arrow)" />
        <text x="314" y="162" {...faint}>plan · questions · findings</text>
        <text x="314" y="176" {...faint}>only during work you start</text>

        <rect x="452" y="44" width="262" height="148" rx="12" fill="var(--color-surface)" stroke="var(--color-accent)" strokeWidth="1.5" />
        <text x="466" y="68" {...label} fontWeight="600">Anthropic API</text>
        <text x="466" y="86" {...faint}>{F.anthropicHost}</text>
        <text x="466" y="110" {...small}>{MODEL_NAME}</text>
        <text x="466" y="126" {...faint}>plans · questions · custom methods</text>
        <text x="466" y="142" {...faint}>visual check · recovery</text>
        <text x="466" y="166" {...faint}>a declined request may be answered</text>
        <text x="466" y="180" {...faint}>by a model Anthropic chooses</text>

        {/* GitHub */}
        <line x1="150" y1="254" x2="150" y2="286" stroke="var(--color-ink-faint)" strokeWidth="1.2" strokeDasharray="4 4" markerEnd="url(#ss-arrow-faint)" />
        <text x="160" y="276" {...faint}>Update check (automatic) and download (your click)</text>
        <text x="160" y="296" {...faint}>→ GitHub: {F.githubApiHost}, {F.githubHost} — version only</text>

        {/* Browser and the picture gap */}
        <line x1="306" y1="226" x2="446" y2="226" stroke="var(--color-ink-faint)" strokeWidth="1.2" strokeDasharray="4 4" markerEnd="url(#ss-arrow-faint)" />
        <text x="452" y="222" {...faint}>Links you click → your browser</text>
        <line x1="306" y1="244" x2="446" y2="262" stroke="var(--color-warn)" strokeWidth="1.2" strokeDasharray="2 4" markerEnd="url(#ss-arrow-warn)" />
        <text x="452" y="262" {...faint}>A picture link in Claude’s text</text>
        <text x="452" y="276" {...faint}>would be fetched (a known gap)</text>
      </svg>
      <figcaption className="mt-3 text-xs leading-relaxed text-ink-faint">
        There is no Spec Surgeon server, account or cloud storage. Solid lines run only while work you started runs;
        dashed lines are automatic, optional or need your click.
      </figcaption>
    </figure>
  );
}
