// Parses what the player types in the chat box. Pure, unit tested.
//
//   hello            Say (everyone nearby), or the chat tab's channel
//                    (`ParseContext.defaultChannel`; ui/chat-state.ts)
//   /s hello         Say, explicitly
//   /h hello         House channel
//   /p hello         Party channel (every member, wherever they are); /party too
//   /c hello         Club channel (every member online, wherever they are); /club too
//   /w Name hello    Whisper. Names may contain spaces ("Anne Marie"), so the
//                    longest known name the text starts with wins; quotes work
//                    too: /w "Anne Marie" hello. Otherwise the first word.
//   /tell Name hello Whisper too (an alias of /w)
//   /r hello         Reply to whoever whispered last

import type { ChatChannelName } from "../wasm/sim_wasm";

export interface ChatCommand {
  channel: ChatChannelName;
  /** Whisper recipient. */
  to?: string;
  text: string;
}

export type ParseResult = { ok: true; cmd: ChatCommand } | { ok: false; error: string } | { ok: true; cmd: null };

export interface ParseContext {
  /** Names the player might whisper to (visible players, recent chat partners). */
  knownNames: Iterable<string>;
  /** Who whispered last, for /r. */
  lastWhisperFrom: string | null;
  /** Where a line without a command goes (the chat tab's channel; say when absent). */
  defaultChannel?: ChatChannelName;
  /** Whom a line without a command whispers to when `defaultChannel` is whisper (the last whisper partner). */
  whisperTo?: string | null;
}

const HELP = "Commands: /s say, /h house, /p party, /c club, /w (or /tell) Name message, /r reply, /invite Name, /leave, /cinvite Name, /cleave, /wave, /bow, /sit.";

export function parseChat(input: string, ctx: ParseContext): ParseResult {
  const line = input.trim();
  if (!line) return { ok: true, cmd: null };
  if (!line.startsWith("/")) return plainLine(line, ctx);

  const space = line.search(/\s/);
  const verb = (space < 0 ? line : line.slice(0, space)).toLowerCase();
  const rest = space < 0 ? "" : line.slice(space).trim();

  switch (verb) {
    case "/s":
    case "/say":
      return rest ? { ok: true, cmd: { channel: "say", text: rest } } : { ok: false, error: "Type a message after /s." };
    case "/h":
    case "/house":
      return rest ? { ok: true, cmd: { channel: "house", text: rest } } : { ok: false, error: "Type a message after /h." };
    case "/p":
    case "/party":
      return rest ? { ok: true, cmd: { channel: "party", text: rest } } : { ok: false, error: "Type a message after /p." };
    case "/c":
    case "/club":
      return rest ? { ok: true, cmd: { channel: "club", text: rest } } : { ok: false, error: "Type a message after /c." };
    case "/r":
    case "/reply":
      if (!ctx.lastWhisperFrom) return { ok: false, error: "Nobody has whispered to you yet." };
      return rest ? { ok: true, cmd: { channel: "whisper", to: ctx.lastWhisperFrom, text: rest } } : { ok: false, error: "Type a message after /r." };
    case "/w":
    case "/whisper":
    case "/tell":
      return parseWhisper(rest, ctx.knownNames);
    default:
      return { ok: false, error: `Unknown command ${verb}. ${HELP}` };
  }
}

/** A line without a command: to the tab's channel. */
function plainLine(text: string, ctx: ParseContext): ParseResult {
  const channel = ctx.defaultChannel ?? "say";
  if (channel !== "whisper") return { ok: true, cmd: { channel, text } };
  if (!ctx.whisperTo) return { ok: false, error: "Nobody to whisper to yet. Use /w Name message." };
  return { ok: true, cmd: { channel, to: ctx.whisperTo, text } };
}

function parseWhisper(rest: string, knownNames: Iterable<string>): ParseResult {
  let to = "";
  let text = "";
  const quoted = /^"([^"]+)"\s*(.*)$/s.exec(rest);
  if (quoted) {
    to = quoted[1]!.trim();
    text = quoted[2]!.trim();
  } else {
    const lower = rest.toLowerCase();
    let best = "";
    for (const name of knownNames) {
      const n = name.toLowerCase();
      // Must be followed by whitespace (or end) so "Ann" doesn't match "Anne".
      if (n.length > best.length && lower.startsWith(n) && (lower.length === n.length || /\s/.test(lower[n.length]!))) {
        best = name;
      }
    }
    if (best) {
      to = best;
      text = rest.slice(best.length).trim();
    } else {
      const space = rest.search(/\s/);
      to = space < 0 ? rest : rest.slice(0, space);
      text = space < 0 ? "" : rest.slice(space).trim();
    }
  }
  if (!to) return { ok: false, error: "Who to? Use /w Name message." };
  if (!text) return { ok: false, error: `Type a message for ${to}.` };
  return { ok: true, cmd: { channel: "whisper", to, text } };
}

/** UTF-8 length, which is what the server's 256-byte chat limit counts. */
export function utf8Length(s: string): number {
  return new TextEncoder().encode(s).length;
}
