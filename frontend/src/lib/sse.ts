// Adapted from Build-a-Spec's frontend/src/lib/api.ts `readSse` (copy-based reuse).
/**
 * The job stream reader (docs/WEB_API.md, "The job stream"): each frame is
 * `data: <json>\n\n`; the last is `done` or `error`.
 *
 * The frame parser is a pure object so `node --test` can feed it chunks cut
 * at any byte: deltas arrive in network bursts that split frames, lines and
 * even multi-byte characters. `readSse` wraps it around a fetch body.
 */

export interface SseParser {
  /** Feed one chunk of decoded text; returns the complete frames it closed. */
  push(chunk: string): unknown[];
  /** The text of a frame left open when the stream ended (a cut-off frame). */
  pending(): string;
}

/** Parse the `data:` lines of one complete frame; malformed JSON is skipped. */
export function parseFrame(frame: string): unknown[] {
  const events: unknown[] = [];
  const data: string[] = [];
  for (const rawLine of frame.split("\n")) {
    const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
    if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
  }
  if (data.length === 0) return events;
  // Several `data:` lines of one frame are one event (the SSE spec joins them
  // with newlines); the server sends one line per frame.
  try {
    events.push(JSON.parse(data.join("\n")));
  } catch {
    // Malformed frame — skip rather than kill the stream.
  }
  return events;
}

export function createSseParser(): SseParser {
  let buffer = "";
  return {
    push(chunk) {
      buffer += chunk;
      const events: unknown[] = [];
      let sep: number;
      // A frame ends at a blank line, however the line breaks are spelled.
      while ((sep = buffer.search(/\r?\n\r?\n/)) !== -1) {
        const match = /\r?\n\r?\n/.exec(buffer.slice(sep))!;
        const frame = buffer.slice(0, sep);
        buffer = buffer.slice(sep + match[0].length);
        events.push(...parseFrame(frame));
      }
      return events;
    },
    pending() {
      return buffer;
    },
  };
}

/** Read SSE frames off a fetch Response body and yield parsed JSON. */
export async function* readSse<T>(
  resp: Response,
  signal?: AbortSignal,
): AsyncGenerator<T> {
  if (!resp.body) return;
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  const parser = createSseParser();

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      for (const event of parser.push(decoder.decode(value, { stream: true }))) {
        yield event as T;
      }
    }
    // A final frame without its blank line still counts once the body ends.
    for (const event of parseFrame(parser.pending())) yield event as T;
  } finally {
    // Reached on normal end, on an abort, AND when a consumer breaks out of
    // its `for await` — which is the case that used to leak, since unwinding
    // this generator released nothing. Cancelling an already-aborted reader
    // is a no-op, so the `signal` check is only to keep that path quiet.
    if (!signal?.aborted) {
      await reader.cancel().catch(() => undefined);
    }
  }
}
