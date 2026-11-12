import { describe, expect, it } from "vitest";
import { parseChat, utf8Length } from "./chatparse";

const ctx = { knownNames: ["Anne Marie", "Anne", "Tom"], lastWhisperFrom: null as string | null };

describe("parseChat", () => {
  it("says plain text and ignores blank input", () => {
    expect(parseChat("  hello  ", ctx)).toEqual({ ok: true, cmd: { channel: "say", text: "hello" } });
    expect(parseChat("   ", ctx)).toEqual({ ok: true, cmd: null });
    expect(parseChat("/s hi", ctx)).toEqual({ ok: true, cmd: { channel: "say", text: "hi" } });
  });

  it("sends /h to the house", () => {
    expect(parseChat("/h meet at the hall", ctx)).toEqual({ ok: true, cmd: { channel: "house", text: "meet at the hall" } });
    expect(parseChat("/H", ctx).ok).toBe(false);
  });

  it("sends /p and /party to the party", () => {
    expect(parseChat("/p meet at the library", ctx)).toEqual({ ok: true, cmd: { channel: "party", text: "meet at the library" } });
    expect(parseChat("/PARTY on my way", ctx)).toEqual({ ok: true, cmd: { channel: "party", text: "on my way" } });
    expect(parseChat("/p", ctx)).toEqual({ ok: false, error: "Type a message after /p." });
    // The party commands are not chat (game/party.ts takes them first); unknown here.
    expect(parseChat("/pinvite Tom", ctx).ok).toBe(false);
  });

  it("sends /c and /club to the club", () => {
    expect(parseChat("/c meeting at the lake", ctx)).toEqual({ ok: true, cmd: { channel: "club", text: "meeting at the lake" } });
    expect(parseChat("/CLUB see you there", ctx)).toEqual({ ok: true, cmd: { channel: "club", text: "see you there" } });
    expect(parseChat("/c", ctx)).toEqual({ ok: false, error: "Type a message after /c." });
    // /cast, /cup and the club commands are not club chat (their owners take them first).
    expect(parseChat("/cinvite Tom", ctx).ok).toBe(false);
    expect(parseChat("/cast", ctx).ok).toBe(false);
  });

  it("whispers to the longest known name, so names may contain spaces", () => {
    expect(parseChat("/w Anne Marie see you", ctx)).toEqual({ ok: true, cmd: { channel: "whisper", to: "Anne Marie", text: "see you" } });
    expect(parseChat("/w anne hi", ctx)).toEqual({ ok: true, cmd: { channel: "whisper", to: "Anne", text: "hi" } });
    // "Tom" must not match "Tomas".
    expect(parseChat("/w Tomas hi", ctx)).toEqual({ ok: true, cmd: { channel: "whisper", to: "Tomas", text: "hi" } });
  });

  it("takes /tell as /w", () => {
    expect(parseChat("/tell Anne Marie see you", ctx)).toEqual(parseChat("/w Anne Marie see you", ctx));
    expect(parseChat('/tell "Mary Kate" hi', ctx)).toEqual({ ok: true, cmd: { channel: "whisper", to: "Mary Kate", text: "hi" } });
    expect(parseChat("/TELL Tomas hi", ctx)).toEqual({ ok: true, cmd: { channel: "whisper", to: "Tomas", text: "hi" } });
    expect(parseChat("/tell Tomas", ctx)).toEqual({ ok: false, error: "Type a message for Tomas." });
    const unknown = parseChat("/nope", ctx);
    expect(unknown.ok).toBe(false);
    if (!unknown.ok) expect(unknown.error).toContain("/tell");
  });

  it("accepts quoted names and rejects incomplete whispers", () => {
    expect(parseChat('/w "Mary Kate" hello there', ctx)).toEqual({ ok: true, cmd: { channel: "whisper", to: "Mary Kate", text: "hello there" } });
    expect(parseChat("/w", ctx).ok).toBe(false);
    expect(parseChat("/w Tom", ctx).ok).toBe(false);
  });

  it("replies to the last whisperer", () => {
    expect(parseChat("/r thanks", ctx).ok).toBe(false);
    expect(parseChat("/r thanks", { ...ctx, lastWhisperFrom: "Tom" })).toEqual({ ok: true, cmd: { channel: "whisper", to: "Tom", text: "thanks" } });
  });

  it("rejects unknown commands", () => {
    const r = parseChat("/dance", ctx);
    expect(r.ok).toBe(false);
  });
});

describe("utf8Length", () => {
  it("counts bytes, not characters", () => {
    expect(utf8Length("abc")).toBe(3);
    expect(utf8Length("é")).toBe(2);
    expect(utf8Length("🦉")).toBe(4);
  });
});
