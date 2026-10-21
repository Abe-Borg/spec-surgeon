/**
 * The job stream's frame parser (lib/sse.ts): `data: <json>\n\n` frames,
 * however the network cuts them.
 */
import assert from "node:assert/strict";
import test from "node:test";

import { createSseParser, parseFrame } from "../src/lib/sse.ts";

test("a complete frame yields its JSON", () => {
  assert.deepEqual(parseFrame('data: {"type":"status","kind":"reading"}'), [{ type: "status", kind: "reading" }]);
});

test("a frame cut anywhere — mid-line, mid-separator, mid-JSON — is reassembled", () => {
  const frames = ['data: {"type":"text_delta","text":"Hel"}\n\n', 'data: {"type":"text_delta","text":"lo"}\n\n'];
  const whole = frames.join("");
  for (let cut = 1; cut < whole.length; cut++) {
    const parser = createSseParser();
    const events = [...parser.push(whole.slice(0, cut)), ...parser.push(whole.slice(cut))];
    assert.deepEqual(
      events,
      [
        { type: "text_delta", text: "Hel" },
        { type: "text_delta", text: "lo" },
      ],
      `cut at ${cut}`,
    );
    assert.equal(parser.pending(), "");
  }
});

test("several frames in one chunk come out in order", () => {
  const parser = createSseParser();
  const events = parser.push('data: {"a":1}\n\ndata: {"a":2}\n\ndata: {"a":3}\n\n');
  assert.deepEqual(events, [{ a: 1 }, { a: 2 }, { a: 3 }]);
});

test("CRLF line endings are frames too", () => {
  const parser = createSseParser();
  assert.deepEqual(parser.push('data: {"a":1}\r\n\r\ndata: {"a":2}\r\n\r\n'), [{ a: 1 }, { a: 2 }]);
});

test("a malformed frame is skipped, not fatal; comments and other fields are ignored", () => {
  const parser = createSseParser();
  const events = parser.push(': keep-alive\n\ndata: {not json}\n\nevent: x\ndata: {"ok":true}\n\n');
  assert.deepEqual(events, [{ ok: true }]);
});

test("a final frame without its blank line is left pending for the end of the body", () => {
  const parser = createSseParser();
  assert.deepEqual(parser.push('data: {"type":"done"}'), []);
  assert.equal(parser.pending(), 'data: {"type":"done"}');
  assert.deepEqual(parseFrame(parser.pending()), [{ type: "done" }]);
});

test("a frame whose JSON contains a newline escape or a blank-looking string stays one event", () => {
  const parser = createSseParser();
  const events = parser.push('data: {"type":"text_delta","text":"one\\n\\ntwo"}\n\n');
  assert.deepEqual(events, [{ type: "text_delta", text: "one\n\ntwo" }]);
});
