// The chat panel: a scrolling log and an input line. Enter opens the input,
// Enter again sends, Escape closes it. Say is the default channel; see
// chatparse.ts for /h, /p, /c, /w and /r. Local commands (/wave, /bow, /sit,
// /invite, /leave, /cinvite, /cleave) go to `ChatOptions.localCommand` first
// and are never sent as chat. Party lines are blue with a "[Party]" label
// (style.css `--party`); club lines spring green with the club's tag,
// "[TAG]" (`--club`).
//
// Our own lines show at once. The server refuses a line with `ChatRejected
// { clientSeq, reason }` (docs/architecture.md, "Chat"); the panel then takes
// the echo back out and says why, so a refused line never looks sent.

import type { ChatChannelName, ChatRejectReason, KickCode } from "../wasm/sim_wasm";
import { h } from "./dom";
import { parseChat, utf8Length, type ChatCommand } from "./chatparse";

const MAX_LINES = 120;

/** The chat panel's size (px): the log keeps it whatever the number of lines; a drag on the top-right grip changes it. */
export interface ChatSize {
  width: number;
  /** The log's height (the input line is under it). */
  height: number;
}
export const CHAT_DEFAULT_SIZE: ChatSize = { width: 440, height: 190 };
export const CHAT_MIN_SIZE: ChatSize = { width: 280, height: 90 };
/** Kept clear of the window's edge (the panel sits 12 px in) and of the top bar and its chips (about 150 px). */
const CHAT_MARGIN_X = 24;
const CHAT_MARGIN_Y = 210;
const SIZE_KEY = "shs.chatSize";

/** `size` within the minimum and what the window has room for. Pure. */
export function clampChatSize(size: ChatSize, viewport: { width: number; height: number }): ChatSize {
  const maxW = Math.max(CHAT_MIN_SIZE.width, viewport.width - CHAT_MARGIN_X);
  const maxH = Math.max(CHAT_MIN_SIZE.height, viewport.height - CHAT_MARGIN_Y);
  const clamp = (v: number, lo: number, hi: number) => Math.round(Math.min(hi, Math.max(lo, Number.isFinite(v) ? v : lo)));
  return { width: clamp(size.width, CHAT_MIN_SIZE.width, maxW), height: clamp(size.height, CHAT_MIN_SIZE.height, maxH) };
}

/** The size a drag from `start` gives: right widens, up heightens (the panel is anchored bottom left). Pure. */
export function dragChatSize(start: ChatSize, dx: number, dy: number, viewport: { width: number; height: number }): ChatSize {
  return clampChatSize({ width: start.width + dx, height: start.height - dy }, viewport);
}

function loadChatSize(): ChatSize {
  try {
    const raw = JSON.parse(localStorage.getItem(SIZE_KEY) ?? "null") as Partial<ChatSize> | null;
    if (raw && typeof raw.width === "number" && typeof raw.height === "number") return { width: raw.width, height: raw.height };
  } catch {
    // No storage (a private window): the default size.
  }
  return { ...CHAT_DEFAULT_SIZE };
}

function saveChatSize(size: ChatSize): void {
  try {
    localStorage.setItem(SIZE_KEY, JSON.stringify(size));
  } catch {
    // Not remembered; the size still applies until the page closes.
  }
}
/** Own lines remembered for a possible `ChatRejected` (the reply comes within a round trip). */
const MAX_PENDING = 32;

export interface ChatLine {
  channel: ChatChannelName | "system";
  from?: string;
  /** Whisper partner: recipient of ours, sender of theirs. */
  to?: string;
  outgoing?: boolean;
  text: string;
  /** Our own line's `Chat.client_seq`, so a `ChatRejected` can find it. */
  clientSeq?: number;
}

/** Why a line of ours was not delivered, for the log. */
export function rejectionText(reason: ChatRejectReason, line?: Pick<ChatLine, "channel" | "to">): string {
  switch (reason) {
    case "rate_limited":
      return "Not sent: you are sending messages too quickly. Wait a moment and try again.";
    case "filtered":
      return "Not sent: that message contains words the castle does not allow.";
    case "unknown_target":
      return line?.channel === "whisper" && line.to ? `Not sent: ${line.to} is not online.` : "Not sent: nobody by that name is online.";
    case "invalid":
      return "Not sent: that message is empty or too long.";
    case "no_channel":
      return line?.channel === "club" ? "Not sent: you're not in a club." : "Not sent: you're not in a party. Invite someone with /invite Name.";
  }
}

/** What to tell the player when the server ends the session (`Kick.code`); `reason` is the server's text. */
export function kickNotice(code: KickCode, reason: string): { heading: string; body: string } {
  switch (code) {
    case "replaced":
      return { heading: "Signed in elsewhere", body: "Your character entered the castle from another window or device, so this one has left." };
    case "banned":
      return { heading: "You may not enter the castle", body: reason || "This account has been suspended." };
    case "session_expired":
      return { heading: "Your session has ended", body: "Please sign in again." };
    case "version_mismatch":
      return { heading: "A new version is out", body: "The castle has been updated. Reload the page to continue." };
    case "no_character":
      return { heading: "No character chosen", body: "That character is not here any more. Choose a character to enter the castle." };
    case "flood":
      return { heading: "Too much at once", body: "Your connection sent more than the castle allows and was closed. You can return in a moment." };
    case "protocol_error":
      return { heading: "Something went wrong", body: "The connection broke in a way it should not have. Reloading the page usually helps." };
    case "server_restart":
    case "timeout":
    case "too_slow":
    case "server_full":
      // Temporary: the client reconnects by itself and shows the reason in a banner.
      return { heading: "You have left the castle", body: reason || "The server closed the connection." };
  }
}

export interface ChatOptions {
  maxBytes: number;
  /** House colour for the house channel. */
  houseColour: string;
  houseName: string;
  knownNames: () => Iterable<string>;
  /** Sends a parsed command; return an error string to show instead. */
  send: (cmd: ChatCommand) => string | null;
  /**
   * Commands this client handles itself (/wave, /bow, /sit), asked before
   * the line is parsed as chat: return true if `line` was one; it is then
   * not sent as chat.
   */
  localCommand?: (line: string) => boolean;
  /** The input took or released focus (movement keys are ignored while typing). */
  onFocusChange: (typing: boolean) => void;
  /** Our club's tag, for club lines' "[TAG]" label (null: in no club). */
  clubTag?: () => string | null;
}

export class ChatPanel {
  readonly root: HTMLElement;
  private readonly log: HTMLOListElement;
  private readonly input: HTMLInputElement;
  private readonly hint: HTMLElement;
  private lastWhisperFrom: string | null = null;
  /** Our recent lines by `clientSeq`, oldest first. */
  private readonly pending = new Map<number, { li: HTMLElement; line: ChatLine }>();
  /** The size chosen (unclamped: a smaller window clamps it without forgetting it). */
  private size: ChatSize = loadChatSize();
  private readonly onResize = () => this.applySize();

  constructor(private readonly opts: ChatOptions) {
    this.log = h("ol", { class: "chat-log", "aria-live": "polite", "aria-label": "Chat" });
    this.input = h("input", {
      class: "chat-input",
      type: "text",
      // A loose cap (UTF-16 units, plus room for "/w Name "); the byte limit is checked on send.
      maxlength: opts.maxBytes + 40,
      placeholder: "Press Enter to talk · /h house · /p party · /c club · /w Name whisper",
      "aria-label": "Chat message",
      spellcheck: "true",
      autocomplete: "off",
    });
    this.hint = h("span", { class: "chat-channel" }, "Say");
    this.root = h(
      "section",
      { class: "chat panel", style: `--house: ${opts.houseColour}` },
      this.buildGrip(),
      this.log,
      h("form", { class: "chat-form", onsubmit: (e: Event) => (e.preventDefault(), this.submit()) }, this.hint, this.input),
    );
    this.applySize();
    window.addEventListener("resize", this.onResize);
    this.input.addEventListener("focus", () => opts.onFocusChange(true));
    this.input.addEventListener("blur", () => opts.onFocusChange(false));
    this.input.addEventListener("input", () => this.updateHint());
    this.input.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        this.input.value = "";
        this.updateHint();
        this.input.blur();
      }
      // Tab (the game's "next nearby") never moves the page's focus out of the chat.
      if (e.key === "Tab" && !e.ctrlKey && !e.metaKey && !e.altKey) e.preventDefault();
      e.stopPropagation(); // typing never reaches the game's key handlers
    });
  }

  /**
   * The grip at the top right: drag it up or right to make the chat bigger,
   * back to make it smaller; arrow keys step it 20 px; a double-click goes
   * back to the default size. The size is remembered in this browser.
   */
  private buildGrip(): HTMLElement {
    const grip = h("div", {
      class: "chat-resize",
      role: "separator",
      tabindex: "0",
      "aria-label": "Resize chat (drag, or arrow keys)",
      title: "Drag to resize · double-click to reset",
    });
    let start: { x: number; y: number; size: ChatSize } | null = null;
    grip.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      start = { x: e.clientX, y: e.clientY, size: this.shownSize() };
      grip.setPointerCapture(e.pointerId);
      this.root.classList.add("resizing");
    });
    grip.addEventListener("pointermove", (e) => {
      if (!start) return;
      this.size = dragChatSize(start.size, e.clientX - start.x, e.clientY - start.y, viewport());
      this.applySize();
    });
    const end = (e: PointerEvent) => {
      if (!start) return;
      start = null;
      if (grip.hasPointerCapture(e.pointerId)) grip.releasePointerCapture(e.pointerId);
      this.root.classList.remove("resizing");
      saveChatSize(this.size);
    };
    grip.addEventListener("pointerup", end);
    grip.addEventListener("pointercancel", end);
    grip.addEventListener("dblclick", () => {
      this.size = { ...CHAT_DEFAULT_SIZE };
      this.applySize();
      saveChatSize(this.size);
    });
    grip.addEventListener("keydown", (e) => {
      const step = { ArrowRight: [20, 0], ArrowLeft: [-20, 0], ArrowUp: [0, -20], ArrowDown: [0, 20] }[e.key];
      if (!step) return;
      e.preventDefault();
      e.stopPropagation(); // the arrows turn the camera otherwise
      this.size = dragChatSize(this.shownSize(), step[0]!, step[1]!, viewport());
      this.applySize();
      saveChatSize(this.size);
    });
    return grip;
  }

  /** The size on screen now (the chosen one, clamped to the window). */
  private shownSize(): ChatSize {
    return clampChatSize(this.size, viewport());
  }

  private applySize(): void {
    const atBottom = this.log.scrollHeight - this.log.scrollTop - this.log.clientHeight < 24;
    const s = this.shownSize();
    this.root.style.setProperty("--chat-w", `${s.width}px`);
    this.root.style.setProperty("--chat-h", `${s.height}px`);
    if (atBottom) this.log.scrollTop = this.log.scrollHeight;
  }

  dispose(): void {
    window.removeEventListener("resize", this.onResize);
  }

  /** Focuses the input (Enter in the game). */
  open(prefill = ""): void {
    if (prefill) this.input.value = prefill;
    this.input.focus();
    this.updateHint();
  }

  get isTyping(): boolean {
    return document.activeElement === this.input;
  }

  private updateHint(): void {
    const v = this.input.value.trimStart().toLowerCase();
    const channel = /^\/(h|house)\b/.test(v) ? "house" : /^\/(w|whisper|tell|r|reply)\b/.test(v) ? "whisper" : /^\/(p|party)\s/.test(v) ? "party" : /^\/(c|club)\s/.test(v) ? "club" : "say";
    this.hint.textContent = channel === "house" ? this.opts.houseName || "House" : channel === "whisper" ? "Whisper" : channel === "party" ? "Party" : channel === "club" ? "Club" : "Say";
    this.hint.dataset.channel = channel;
  }

  private submit(): void {
    if (this.opts.localCommand?.(this.input.value)) {
      this.input.value = "";
      this.updateHint();
      this.input.blur();
      return;
    }
    const result = parseChat(this.input.value, { knownNames: this.opts.knownNames(), lastWhisperFrom: this.lastWhisperFrom });
    if (!result.ok) {
      this.system(result.error);
      return;
    }
    if (!result.cmd) {
      this.input.blur();
      return;
    }
    if (utf8Length(result.cmd.text) > this.opts.maxBytes) {
      this.system(`That message is too long (at most ${this.opts.maxBytes} bytes).`);
      return;
    }
    const error = this.opts.send(result.cmd);
    if (error) {
      this.system(error);
      return;
    }
    this.input.value = "";
    this.updateHint();
    this.input.blur();
  }

  system(text: string): void {
    this.add({ channel: "system", text });
  }

  add(line: ChatLine): void {
    if (line.channel === "whisper" && !line.outgoing && line.from) this.lastWhisperFrom = line.from;
    const who =
      line.channel === "system"
        ? null
        : line.channel === "whisper"
          ? line.outgoing
            ? `To ${line.to}: `
            : `${line.from} whispers: `
          : line.channel === "house"
            ? `[${this.opts.houseName || "House"}] ${line.from}: `
            : line.channel === "party"
              ? `[Party] ${line.from}: `
              : line.channel === "club"
                ? `[${this.opts.clubTag?.() ?? "Club"}] ${line.from}: `
                : `${line.from}: `;
    const li = h("li", { class: `chat-line chat-${line.channel}` }, who ? h("span", { class: "chat-who" }, who) : null, line.text);
    const atBottom = this.log.scrollHeight - this.log.scrollTop - this.log.clientHeight < 24;
    this.log.append(li);
    while (this.log.childElementCount > MAX_LINES) this.log.firstElementChild?.remove();
    if (atBottom) this.log.scrollTop = this.log.scrollHeight;
    if (line.outgoing && line.clientSeq !== undefined) {
      this.pending.set(line.clientSeq, { li, line });
      if (this.pending.size > MAX_PENDING) this.pending.delete(this.pending.keys().next().value!);
    }
  }

  /**
   * `ChatRejected`: removes our line `clientSeq` and says why. A line refused
   * for sending too fast goes back into an empty input, ready to resend.
   */
  reject(clientSeq: number, reason: ChatRejectReason): void {
    const entry = this.pending.get(clientSeq);
    this.pending.delete(clientSeq);
    entry?.li.remove();
    this.system(rejectionText(reason, entry?.line));
    if (entry && reason === "rate_limited" && !this.input.value) {
      const l = entry.line;
      this.input.value = l.channel === "house" ? `/h ${l.text}` : l.channel === "party" ? `/p ${l.text}` : l.channel === "club" ? `/c ${l.text}` : l.channel === "whisper" && l.to ? `/w ${quoteName(l.to)} ${l.text}` : l.text;
      this.updateHint();
    }
  }
}

function viewport(): { width: number; height: number } {
  return { width: window.innerWidth, height: window.innerHeight };
}

/** A name as `/w` takes it: quoted when it has spaces. */
export function quoteName(name: string): string {
  return /\s/.test(name) ? `"${name}"` : name;
}
