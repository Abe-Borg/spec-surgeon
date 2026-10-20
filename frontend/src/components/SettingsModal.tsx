import { useEffect, useState } from "react";
import type { Settings } from "../types";
import { getSettings, saveSettings } from "../lib/api";
import { ModalShell, primaryBtn } from "./ModalShell";
import Tip from "./Tip";

interface Props {
  stepsPerPause: number;
  onClose: () => void;
  /** The connection may have changed: the caller refreshes the view. */
  onSaved: () => void;
}

/**
 * Connection setup, separate from the document task. One dialog, "AI
 * connection": the key, whether to remember it securely, and the sentence
 * on spending (docs/CONVERSATIONAL_DESKTOP.md, "Connection and resource
 * use"). No model tuning controls.
 */
export default function SettingsModal({ stepsPerPause, onClose, onSaved }: Props) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [key, setKey] = useState("");
  const [remember, setRemember] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let alive = true;
    getSettings()
      .then((s) => {
        if (!alive) return;
        setSettings(s);
        setRemember(s.can_remember);
      })
      .catch((failure: unknown) => {
        if (alive) setError(failure instanceof Error ? failure.message : String(failure));
      });
    return () => {
      alive = false;
    };
  }, []);

  const save = async () => {
    if (saving) return;
    setSaving(true);
    setError("");
    setSaved(false);
    try {
      const next = await saveSettings(key.trim(), remember && !!settings?.can_remember);
      setSettings(next);
      setKey("");
      setSaved(true);
      onSaved();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setSaving(false);
    }
  };

  const canRemember = !!settings?.can_remember;
  const keyPresent = !!settings?.key_present;

  return (
    <ModalShell title="Settings" onClose={onClose} wide>
      <div className="flex flex-col gap-4">
        <section>
          <h3 className="font-[family-name:var(--font-display)] text-sm font-semibold text-ink">
            AI connection
          </h3>
          <p className="mt-0.5 text-xs text-ink-dim">
            Enter your Anthropic API key to use the conversation.
          </p>
          <input
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={key}
            onChange={(e) => setKey(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && key.trim()) void save();
            }}
            placeholder={keyPresent ? "A key is connected; enter a new one to replace it" : "sk-ant-…"}
            aria-label="Anthropic API key"
            className="mt-3 w-full rounded-lg border border-edge bg-bg px-3 py-2 text-sm text-ink outline-none placeholder:text-ink-faint focus:border-accent/70"
          />
          <Tip
            tip={
              canRemember
                ? "Keeps the key in the operating system's credential store, so you do not enter it again."
                : "This computer has no secure credential store the app can use; the key is kept for this session only."
            }
            className="mt-2"
          >
            <label className="flex cursor-pointer items-center gap-2 text-sm text-ink-dim">
              <input
                type="checkbox"
                className="h-3.5 w-3.5 accent-[var(--color-accent)]"
                checked={remember && canRemember}
                disabled={!canRemember}
                onChange={(e) => setRemember(e.target.checked)}
              />
              Remember securely on this computer
            </label>
          </Tip>
        </section>
        <p className="text-xs leading-relaxed text-ink-faint">
          There is no spending limit. Work pauses after every {stepsPerPause} AI steps until you continue,
          the status line shows the estimated spending, and Stop now cuts off the AI step that is running.
          Provider charges can differ from the estimate.
        </p>
        {settings && (
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[11px] text-ink-faint">
            <dt>Edited copies</dt>
            <dd className="truncate text-ink-dim" title={settings.out_root}>
              {settings.out_root || "—"}
            </dd>
            <dt>Diagnostic logs</dt>
            <dd className="truncate text-ink-dim" title={settings.logs_dir}>
              {settings.logs_dir || "—"}
            </dd>
          </dl>
        )}
        {error && (
          <p className="text-xs text-err" role="alert">
            {error}
          </p>
        )}
        {saved && !error && (
          <p className="text-xs text-ok" role="status">
            The key is connected.
          </p>
        )}
        <div className="flex justify-end">
          <Tip tip={key.trim() ? "Connect this key" : "Enter a key first"}>
            <button type="button" className={primaryBtn} disabled={!key.trim() || saving} onClick={() => void save()}>
              {saving ? "Saving…" : "Save"}
            </button>
          </Tip>
        </div>
      </div>
    </ModalShell>
  );
}
