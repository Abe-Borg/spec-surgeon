/**
 * Shared by the screenshot and flow tools: start the mock server on a port
 * with its own token, and launch Chromium — Playwright's own, or when the
 * installed revision does not match this Playwright, the newest Chromium
 * binary found under PLAYWRIGHT_BROWSERS_PATH (or CHROMIUM_PATH) by path.
 */
import { spawn } from "node:child_process";
import { existsSync, readdirSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

export const FRONTEND = resolve(dirname(fileURLToPath(import.meta.url)), "..");

export function findChromium() {
  if (process.env.CHROMIUM_PATH && existsSync(process.env.CHROMIUM_PATH)) return process.env.CHROMIUM_PATH;
  const roots = [process.env.PLAYWRIGHT_BROWSERS_PATH, "/opt/pw-browsers"].filter(Boolean);
  const candidates = [];
  for (const root of roots) {
    if (!existsSync(root)) continue;
    for (const entry of readdirSync(root)) {
      if (!/^chromium-\d+$/.test(entry)) continue;
      for (const rel of ["chrome-linux/chrome", "chrome-win/chrome.exe", "chrome-mac/Chromium.app/Contents/MacOS/Chromium"]) {
        const file = join(root, entry, rel);
        if (existsSync(file) && statSync(file).isFile()) candidates.push({ file, rev: Number(entry.split("-")[1]) });
      }
    }
  }
  candidates.sort((a, b) => b.rev - a.rev);
  return candidates[0]?.file ?? null;
}

export async function launch() {
  try {
    return await chromium.launch();
  } catch (failure) {
    const file = findChromium();
    if (!file) throw failure;
    console.log(`Playwright's own Chromium is not installed; launching ${file}`);
    return chromium.launch({ executablePath: file });
  }
}

/** Start the mock server; resolves once it answers, with `kill()` and `base`. */
export async function startMock({ port, token, delay = "25" }) {
  const child = spawn(process.execPath, [join(FRONTEND, "mock", "server.mjs")], {
    env: { ...process.env, PORT: String(port), MOCK_TOKEN: token, MOCK_DELAY: delay },
    stdio: ["ignore", "pipe", "inherit"],
  });
  child.stdout.on("data", () => undefined);
  const base = `http://127.0.0.1:${port}`;
  for (let i = 0; i < 100; i++) {
    try {
      const resp = await fetch(`${base}/mock/health`);
      if (resp.ok) {
        return {
          base,
          kill: () => child.kill(),
          scenario: async (name) => {
            const resp = await fetch(`${base}/mock/scenario`, { method: "POST", body: JSON.stringify({ name }) });
            if (!resp.ok) throw new Error(`scenario ${name}: ${resp.status}`);
          },
        };
      }
    } catch {
      // not up yet
    }
    await new Promise((r) => setTimeout(r, 100));
  }
  child.kill();
  throw new Error("The mock server did not start");
}
