import { useEffect, useRef, useState } from "react";
import type { Settings } from "../types";
import { getSettings, removeKey, saveFastMode, saveSettings, saveShowObliterate, saveSpendingLimit, saveTheme } from "../lib/api";
import { dollars } from "../lib/chat";
import { fastStatus, fastWords } from "../lib/fastMode";
import { THEMES, applyTheme, resolveTheme } from "../lib/theme";
import { ModalShell, primaryBtn, quietBtn } from "./ModalShell";
import Tip from "./Tip";

interface Props {
  onClose: () => void;
  /** The connection may have changed: the caller refreshes the view. */
  onSaved: () => void;
}

/**
 * Connection setup, separate from the document task: "AI connection" (the
 * key, whether to remember it securely; docs/CONVERSATIONAL_DESKTOP.md,
 * "Connection and resource use"), "Spending check" (the estimated dollars one
 * request may spend before the work pauses, typed by the user, or off), "Fast
 * responses" (Claude's fast mode: what it buys and what it costs, both from
 * the server's price table; turning it on asks for a second click that names
 * the price) and "Appearance" (the colour scheme, applied at once and kept for
 * the next start). Fast mode is the one model control: it changes how quickly
 * the same model answers, never what it is asked or how its plan is checked.
 * "Advanced" shows or hides Obliterate mode in the top bar (a second click to
 * show it; choosing the mode still shows its own warning every time).
 */
export default function SettingsModal({ onClose, onSaved }: Props) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [key, setKey] = useState("");
  const [remember, setRemember] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [themeError, setThemeError] = useState("");
  /** The latest click: an answer to an earlier one arriving after it is ignored. */
  const themeTurn = useRef(0);
  /** Turning fast mode on waits for the second click that names the price. */
  const [confirmingFast, setConfirmingFast] = useState(false);
  const [fastSaving, setFastSaving] = useState(false);
  const [fastError, setFastError] = useState("");
  const [confirmingShow, setConfirmingShow] = useState(false);
  const [showSaving, setShowSaving] = useState(false);
  const [showError, setShowError] = useState("");
  const [spendSaving, setSpendSaving] = useState(false);
  const [spendError, setSpendError] = useState("");
  const [spendStatus, setSpendStatus] = useState("");

  useEffect(() => {
    let alive = true;
    getSettings()
      .then((s) => {
        if (!alive) return;
        setSettings(s);
        setRemember(s.can_remember);
        // The kept scheme is the truth: a page opened without it (the dev server) takes it now.
        if (document.documentElement.dataset.theme !== resolveTheme(s.theme)) applyTheme(s.theme);
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
    setStatus("");
    try {
      const next = await saveSettings(key.trim(), remember && !!settings?.can_remember);
      setSettings(next);
      setKey("");
      setStatus(`The key is saved for ${next.origin === "saved" ? "this computer" : "this session"}. It has not been tested with Anthropic.`);
      onSaved();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (saving) return;
    setSaving(true);
    setError("");
    setStatus("");
    try {
      const next = await removeKey();
      setSettings(next);
      setKey("");
      setStatus("The app's key was removed." + (next.key_present ? " Other credentials are still available." : ""));
      onSaved();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setSaving(false);
    }
  };

  /** Recolour at once; keep it for the next start. A scheme the server did not keep is taken back. */
  const pickTheme = async (id: string) => {
    if (!settings || id === resolveTheme(settings.theme)) return;
    const previous = resolveTheme(settings.theme);
    const turn = ++themeTurn.current;
    applyTheme(id);
    setSettings({ ...settings, theme: id });
    setThemeError("");
    try {
      const next = await saveTheme(id);
      if (turn === themeTurn.current) setSettings(next);
    } catch (failure) {
      if (turn !== themeTurn.current) return;
      applyTheme(previous);
      setSettings((s) => (s ? { ...s, theme: previous } : s));
      setThemeError(failure instanceof Error ? failure.message : String(failure));
    }
  };

  const setFast = async (on: boolean) => {
    if (fastSaving) return;
    setFastSaving(true);
    setFastError("");
    try {
      const next = await saveFastMode(on);
      setSettings(next);
      setConfirmingFast(false);
      onSaved();
    } catch (failure) {
      setFastError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setFastSaving(false);
    }
  };

  const setSpending = async (limit: number | null) => {
    if (spendSaving) return;
    setSpendSaving(true);
    setSpendError("");
    setSpendStatus("");
    try {
      const next = await saveSpendingLimit(limit);
      setSettings(next);
      setSpendStatus(
        typeof next.spending_limit_usd === "number"
          ? `From the next request on, the work pauses before one request could spend more than ${dollars(next.spending_limit_usd)}.`
          : "The spending check is off: nothing pauses the work; Stop still cuts it off.",
      );
      onSaved();
    } catch (failure) {
      setSpendError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setSpendSaving(false);
    }
  };

  const setShowObliterate = async (on: boolean) => {
    if (showSaving) return;
    setShowSaving(true);
    setShowError("");
    try {
      const next = await saveShowObliterate(on);
      setSettings(next);
      setConfirmingShow(false);
      onSaved();
    } catch (failure) {
      setShowError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setShowSaving(false);
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
            placeholder={keyPresent ? "Credentials are available; enter a key to replace the app's key" : "sk-ant-…"}
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
        {settings && keyPresent && !settings.can_remove_key && (
          <p className="text-xs text-ink-faint">Credentials from the environment or SDK profile are managed outside this app.</p>
        )}
        {status && !error && (
          <p className="text-xs text-ok" role="status">
            {status}
          </p>
        )}
        <div className="flex justify-end gap-2">
          {settings?.can_remove_key && (
            <Tip tip="Removes the app's session key and any key it saved securely. Environment credentials and SDK profiles stay intact.">
              <button type="button" className={quietBtn} disabled={saving} onClick={() => void remove()}>Remove app key</button>
            </Tip>
          )}
          <Tip tip={key.trim() ? "Save this key without making a connection test" : "Enter a key first"}>
            <button type="button" className={primaryBtn} disabled={!key.trim() || saving} onClick={() => void save()}>
              {saving ? "Saving…" : "Save"}
            </button>
          </Tip>
        </div>
        {settings && settings.spending_limit_usd !== undefined && (
          <SpendingSection
            limit={settings.spending_limit_usd}
            fallback={settings.spending_default_usd}
            saving={spendSaving}
            error={spendError}
            status={spendStatus}
            onSave={(limit) => void setSpending(limit)}
          />
        )}
        {settings?.fast && (
          <FastSection
            on={!!settings.fast_mode}
            words={fastWords(settings.fast)}
            confirming={confirmingFast}
            saving={fastSaving}
            error={fastError}
            onAsk={() => setConfirmingFast(true)}
            onCancel={() => setConfirmingFast(false)}
            onSet={(on) => void setFast(on)}
          />
        )}
        <section className="border-t border-edge pt-4">
          <h3 className="font-[family-name:var(--font-display)] text-sm font-semibold text-ink">Appearance</h3>
          <p className="mt-0.5 text-xs text-ink-dim">The colour scheme changes at once and is kept for the next start.</p>
          <div role="radiogroup" aria-label="Colour scheme" className="mt-3 grid grid-cols-5 gap-2">
            {THEMES.map((t) => {
              const chosen = !!settings && resolveTheme(settings.theme) === t.id;
              return (
                <Tip key={t.id} tip={t.blurb}>
                  <button
                    type="button"
                    role="radio"
                    aria-checked={chosen}
                    aria-label={t.name}
                    disabled={!settings}
                    onClick={() => void pickTheme(t.id)}
                    className={`w-full rounded-lg border p-1.5 text-left transition-colors disabled:pointer-events-none ${
                      chosen ? "border-accent bg-accent/10" : "border-edge bg-raised hover:border-accent/60"
                    }`}
                  >
                    <span aria-hidden className="relative block h-11 overflow-hidden rounded-md" style={{ background: t.swatch.bg }}>
                      <span
                        className="absolute top-2 right-2 bottom-0 left-2 rounded-t-[4px]"
                        style={{ background: t.swatch.paper }}
                      />
                      <span
                        className="absolute top-3.5 left-4 h-1.5 w-7 rounded-full"
                        style={{ background: t.swatch.accent }}
                      />
                    </span>
                    <span className={`mt-1.5 block truncate text-[11px] ${chosen ? "text-accent" : "text-ink-dim"}`}>
                      {t.name}
                    </span>
                  </button>
                </Tip>
              );
            })}
          </div>
          {themeError && (
            <p className="mt-2 text-xs text-err" role="alert">
              {themeError}
            </p>
          )}
        </section>
        {settings && settings.show_obliterate !== undefined && (
          <AdvancedSection
            on={!!settings.show_obliterate}
            confirming={confirmingShow}
            saving={showSaving}
            error={showError}
            onAsk={() => setConfirmingShow(true)}
            onCancel={() => setConfirmingShow(false)}
            onSet={(on) => void setShowObliterate(on)}
          />
        )}
      </div>
    </ModalShell>
  );
}

/**
 * "Spending check": the estimated dollars one request (a message, a pick, an approval) may spend. The app does not start
 * an AI step that could take the request past it; the work pauses with its progress saved until the user continues,
 * which allows the amount again. Typed by the user, or off. Stop, beside the spending while work runs, is the kill
 * switch.
 */
function SpendingSection({
  limit,
  fallback,
  saving,
  error,
  status,
  onSave,
}: {
  limit: number | null;
  /** The amount the check starts at, shown as the field's hint. */
  fallback?: number;
  saving: boolean;
  error: string;
  status: string;
  onSave: (limit: number | null) => void;
}) {
  const [text, setText] = useState(limit === null ? "" : String(limit));
  const typed = Number(text);
  const valid = text.trim() !== "" && Number.isFinite(typed) && typed > 0;
  const unchanged = valid && limit !== null && Math.abs(typed - limit) < 0.005;
  return (
    <section className="border-t border-edge pt-4">
      <h3 className="font-[family-name:var(--font-display)] text-sm font-semibold text-ink">Spending check</h3>
      <p className="mt-0.5 text-xs leading-relaxed text-ink-dim">
        The most one request (a message, a pick, an approval) may spend, as the app estimates it. An AI step that could
        take the request past it is not started: the work pauses with its progress saved until you continue, which
        allows the amount again. While work runs, Stop beside the spending in the top bar cuts it off at once.
        Estimates come from a built-in price table; provider charges can differ.
      </p>
      <p className="mt-2 text-xs text-ink-faint" role="status">
        {limit === null ? "Now: off. Nothing pauses the work." : `Now: ${dollars(limit)} per request.`}
      </p>
      <div className="mt-2 flex items-center gap-2">
        <label className="flex items-center gap-1 rounded-lg border border-edge bg-bg px-2 py-1.5 text-sm text-ink focus-within:border-accent/70">
          <span className="text-ink-faint" aria-hidden>
            $
          </span>
          <input
            type="number"
            inputMode="decimal"
            min="0.01"
            step="1"
            value={text}
            placeholder={fallback === undefined ? "" : String(fallback)}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && valid && !unchanged) onSave(typed);
            }}
            aria-label="Spending check in US dollars per request"
            className="w-24 bg-transparent outline-none tabular-nums placeholder:text-ink-faint"
          />
        </label>
        <Tip tip={valid ? "The next request uses it" : "Type an amount in US dollars, more than zero"}>
          <button type="button" className={primaryBtn} disabled={!valid || unchanged || saving} onClick={() => onSave(typed)}>
            {saving ? "Applying…" : "Apply"}
          </button>
        </Tip>
        {limit !== null && (
          <Tip tip="Nothing pauses the work for its spending; Stop still cuts it off">
            <button type="button" className={quietBtn} disabled={saving} onClick={() => {
              setText("");
              onSave(null);
            }}>
              Turn off
            </button>
          </Tip>
        )}
      </div>
      {error && (
        <p className="mt-2 text-xs text-err" role="alert">
          {error}
        </p>
      )}
      {status && !error && (
        <p className="mt-2 text-xs text-ok" role="status">
          {status}
        </p>
      )}
    </section>
  );
}

/**
 * "Fast responses": a switch that is off until the user turns it on. Both sides are always on screen — the benefit,
 * then the price in a warning box — and turning it on takes a second click on a button that names the price.
 * Turning it off is one click.
 */
function FastSection({
  on,
  words,
  confirming,
  saving,
  error,
  onAsk,
  onCancel,
  onSet,
}: {
  on: boolean;
  words: ReturnType<typeof fastWords>;
  confirming: boolean;
  saving: boolean;
  error: string;
  onAsk: () => void;
  onCancel: () => void;
  onSet: (on: boolean) => void;
}) {
  const status = fastStatus(on, words);
  return (
    <section className="border-t border-edge pt-4">
      <div className="flex items-center justify-between gap-3">
        <h3 className="font-[family-name:var(--font-display)] text-sm font-semibold text-ink">Fast responses</h3>
        <button
          type="button"
          role="switch"
          aria-checked={on}
          aria-label="Fast responses"
          disabled={saving}
          onClick={() => (on ? onSet(false) : confirming ? onCancel() : onAsk())}
          className={`relative h-5 w-9 flex-none rounded-full border transition-colors disabled:opacity-60 ${
            on ? "border-accent bg-accent" : "border-edge bg-raised"
          }`}
        >
          <span
            aria-hidden
            className={`absolute top-0.5 h-3.5 w-3.5 rounded-full transition-all ${
              on ? "left-[1.1rem] bg-on-accent" : "left-0.5 bg-ink-faint"
            }`}
          />
        </button>
      </div>
      <p className="mt-0.5 text-xs text-ink-dim">{words.benefit}</p>
      <div className="mt-3 rounded-lg border-l-2 border-warn bg-warn/10 px-3 py-2 text-xs leading-relaxed text-ink-dim">
        <span className="font-medium text-warn">The price: </span>
        {words.cost}
      </div>
      <ul className="mt-2 list-disc space-y-0.5 pl-4 text-[11px] leading-relaxed text-ink-faint">
        {words.notes.map((n) => (
          <li key={n}>{n}</li>
        ))}
      </ul>
      {words.unavailable && (
        <p className="mt-2 text-xs text-warn" role="status">
          {words.unavailable}
        </p>
      )}
      {confirming && !on && (
        <div className="mt-3 flex flex-wrap items-center justify-end gap-2" role="group" aria-label="Confirm fast responses">
          <span className="w-full text-xs text-ink-dim">Turn on fast responses for every AI step from now on?</span>
          <button type="button" className={quietBtn} disabled={saving} onClick={onCancel}>
            Keep standard speed
          </button>
          <button type="button" className={primaryBtn} disabled={saving} onClick={() => onSet(true)}>
            {saving ? "Saving…" : words.confirm}
          </button>
        </div>
      )}
      {status && (
        <p className={`mt-2 text-xs ${status.tone === "ok" ? "text-ok" : "text-warn"}`} role="status">
          {status.text}
        </p>
      )}
      {error && (
        <p className="mt-2 text-xs text-err" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

/**
 * "Advanced": a switch that shows Obliterate mode in the top bar, off until the user turns it on. Showing it takes a
 * second click; it only adds the choice — choosing the mode shows its warning each time. Hiding it leaves the mode.
 */
function AdvancedSection({
  on,
  confirming,
  saving,
  error,
  onAsk,
  onCancel,
  onSet,
}: {
  on: boolean;
  confirming: boolean;
  saving: boolean;
  error: string;
  onAsk: () => void;
  onCancel: () => void;
  onSet: (on: boolean) => void;
}) {
  return (
    <section className="border-t border-edge pt-4">
      <h3 className="font-[family-name:var(--font-display)] text-sm font-semibold text-ink">Advanced</h3>
      <div className="mt-2 flex items-center justify-between gap-3">
        <span className="text-sm text-ink">Show Obliterate mode</span>
        <button
          type="button"
          role="switch"
          aria-checked={on}
          aria-label="Show Obliterate mode"
          disabled={saving}
          onClick={() => (on ? onSet(false) : confirming ? onCancel() : onAsk())}
          className={`relative h-5 w-9 flex-none rounded-full border transition-colors disabled:opacity-60 ${
            on ? "border-err bg-err" : "border-edge bg-raised"
          }`}
        >
          <span
            aria-hidden
            className={`absolute top-0.5 h-3.5 w-3.5 rounded-full transition-all ${
              on ? "left-[1.1rem] bg-white" : "left-0.5 bg-ink-faint"
            }`}
          />
        </button>
      </div>
      <p className="mt-0.5 text-xs text-ink-dim">
        Adds a third mode to the top bar: the conversation without the guardrails that protect your documents’ content
        (Claude may write wording and change anything Word stores, and a copy whose checks fail is delivered marked
        UNVERIFIED). Copies only; your originals are never written. Choosing it shows its warning every time, and the
        app never opens in it.
      </p>
      {confirming && !on && (
        <div className="mt-3 flex flex-wrap items-center justify-end gap-2" role="group" aria-label="Confirm show Obliterate mode">
          <span className="w-full text-xs text-ink-dim">Show Obliterate mode in the top bar?</span>
          <button type="button" className={quietBtn} disabled={saving} onClick={onCancel}>
            Keep it hidden
          </button>
          <button type="button" className={primaryBtn} disabled={saving} onClick={() => onSet(true)}>
            {saving ? "Saving…" : "Show it"}
          </button>
        </div>
      )}
      {error && (
        <p className="mt-2 text-xs text-err" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
