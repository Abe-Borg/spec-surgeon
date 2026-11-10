import { useState } from "react";
import type { Attachment } from "../types";
import { PASSWORD_GO, PASSWORD_SAYS, PASSWORD_TITLE } from "../lib/rescue";
import { ModalShell, primaryBtn, quietBtn } from "./ModalShell";

interface Props {
  /** The refused, password-protected document. */
  document: Attachment;
  busy: boolean;
  onClose: () => void;
  /** The password as typed: it goes to the server with this one request and is kept nowhere on the page. */
  onRescue: (password: string) => void;
}

/**
 * Asks for an encrypted document's password before Try to rescue (lib/rescue.ts). The field is a password field,
 * the value lives only in this dialog's state, and the dialog says what happens to it and to the rescued document,
 * which will have none.
 */
export default function RescuePasswordModal({ document, busy, onClose, onRescue }: Props) {
  const [password, setPassword] = useState("");
  return (
    <ModalShell title={`${PASSWORD_TITLE} — ${document.name}`} onClose={onClose}>
      <form
        className="flex flex-col gap-3 text-sm"
        onSubmit={(e) => {
          e.preventDefault();
          if (password && !busy) onRescue(password);
        }}
      >
        <ul className="list-disc space-y-0.5 pl-4 text-xs leading-relaxed text-ink-dim">
          {PASSWORD_SAYS.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
        <label className="flex flex-col gap-1 text-xs text-ink">
          Password
          <input
            type="password"
            autoComplete="off"
            spellCheck={false}
            className="rounded-lg border border-edge bg-raised px-3 py-2 text-sm text-ink outline-none focus:border-accent"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            data-capability="rescue.password"
          />
        </label>
        <div className="flex justify-end gap-2">
          <button type="button" className={quietBtn} onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className={primaryBtn} disabled={!password || busy}>
            {PASSWORD_GO}
          </button>
        </div>
      </form>
    </ModalShell>
  );
}
