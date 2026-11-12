// The game WebSocket (docs/architecture.md, "Network"): binary postcard frames
// encoded and decoded by sim-wasm, `Hello` first, reconnect with backoff.
//
// Reconnecting: after an unexpected close the connection waits `backoffDelay`
// and calls `beforeReconnect` (which checks the session over HTTP, so an
// expired session goes back to sign-in rather than retrying forever), then
// opens a new socket. A final `Kick` ends the session for good: no reconnect.
//
// Each socket gets its own `ServerDecoder`: snapshots carry deltas against
// what this connection received before (protocol v2) and cached entries for
// characters it was already sent (v3), which the decoder applies and fills
// in, so `snapshot.states` are absolute and `snapshot.entered` complete. A
// snapshot that does not fit (out of sync) closes the socket, and the
// reconnect starts afresh.

import { ServerDecoder, encodeHello, encodePing, type KickCode, type ServerMessage } from "../wasm/sim_wasm";

/** Delay before reconnect attempt `attempt` (0-based): 0.5 s doubling to 15 s, with jitter. */
export function backoffDelay(attempt: number, random: () => number = Math.random): number {
  const base = Math.min(15_000, 500 * 2 ** Math.min(attempt, 10));
  return Math.round(base * (0.5 + random() * 0.5));
}

/**
 * Whether a `Kick` is temporary, so the client should reconnect with backoff:
 * a server restart, a timeout, a connection too slow to keep up, or a full
 * castle (`protocol::KickCode::is_retryable`). Everything else (signed in
 * elsewhere, expired session, outdated client) is final; reconnecting after
 * "signed in elsewhere" would make two tabs kick each other forever.
 */
export function isRetryableKick(code: KickCode): boolean {
  return code === "server_restart" || code === "timeout" || code === "too_slow" || code === "server_full";
}

export type ConnectionStatus =
  | { kind: "connecting"; attempt: number }
  | { kind: "open" }
  /** `code` and `reason` are set when a temporary kick caused the wait. */
  | { kind: "waiting"; attempt: number; delayMs: number; reason: string | null; code: KickCode | null }
  /** A final kick: the connection will not be retried. `reason` is display text only. */
  | { kind: "kicked"; code: KickCode; reason: string }
  | { kind: "closed" };

export interface ConnectionHandlers {
  onMessage(msg: ServerMessage): void;
  onStatus(status: ConnectionStatus): void;
  /** Runs before each reconnect; return false to stop reconnecting. */
  beforeReconnect(): Promise<boolean>;
}

/** No `Welcome` or `Queued` this long after `Hello` means the server is stuck; reconnect. */
const HELLO_TIMEOUT_MS = 10_000;
const PING_INTERVAL_MS = 5_000;

export class Connection {
  private ws: WebSocket | null = null;
  private attempt = 0;
  private stopped = false;
  private timers: ReturnType<typeof setTimeout>[] = [];
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private admitted = false;
  /** A temporary kick, shown while waiting to reconnect. */
  private kickReason: string | null = null;
  private kickCode: KickCode | null = null;
  /** Round-trip time from the last Ping/Pong, ms. */
  rttMs: number | null = null;
  private readonly epoch = performance.now();

  constructor(
    private readonly url: string,
    private readonly token: string,
    /** The character this client was started for: every `Hello` names it, and the server kicks with `replaced` if the session now plays another. */
    private readonly characterId: number,
    private readonly handlers: ConnectionHandlers,
  ) {}

  get isOpen(): boolean {
    return this.ws?.readyState === WebSocket.OPEN && this.admitted;
  }

  connect(): void {
    if (this.stopped) return;
    this.handlers.onStatus({ kind: "connecting", attempt: this.attempt });
    const ws = new WebSocket(this.url);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    this.admitted = false;
    const decoder = new ServerDecoder();

    ws.onopen = () => {
      sendFrame(ws, encodeHello(this.token, this.characterId));
      this.later(HELLO_TIMEOUT_MS, () => {
        if (this.ws === ws && !this.admitted) ws.close(4000, "no welcome");
      });
    };
    ws.onmessage = (ev) => {
      if (!(ev.data instanceof ArrayBuffer)) return; // the protocol is binary only
      let msg: ServerMessage;
      try {
        msg = decoder.decode(new Uint8Array(ev.data));
      } catch (e) {
        // Malformed, or a snapshot that no longer fits this connection's
        // visible set: start over on a fresh connection.
        console.warn("closing after an undecodable server frame", e);
        ws.close(4001, "undecodable frame");
        return;
      }
      this.onServerMessage(ws, msg);
    };
    ws.onclose = () => {
      decoder.free();
      if (this.ws !== ws) return;
      this.ws = null;
      this.admitted = false;
      this.stopPing();
      if (!this.stopped) this.scheduleReconnect();
    };
  }

  private onServerMessage(ws: WebSocket, msg: ServerMessage): void {
    switch (msg.type) {
      case "welcome":
        this.admitted = true;
        this.kickReason = null;
        this.kickCode = null;
        this.attempt = 0;
        this.handlers.onStatus({ kind: "open" });
        this.startPing(ws);
        break;
      case "queued":
        // Waiting in the login queue is progress: keep the socket however long it takes.
        this.admitted = true;
        break;
      case "pong":
        this.rttMs = this.now() - msg.clientTimeMs;
        break;
      case "kick":
        if (isRetryableKick(msg.code)) {
          // The server closes the socket next; onclose then schedules the reconnect.
          this.kickReason = msg.reason;
          this.kickCode = msg.code;
        } else {
          this.stop();
          this.handlers.onStatus({ kind: "kicked", code: msg.code, reason: msg.reason });
        }
        break;
    }
    this.handlers.onMessage(msg);
  }

  private scheduleReconnect(): void {
    const delayMs = backoffDelay(this.attempt);
    this.attempt++;
    this.handlers.onStatus({ kind: "waiting", attempt: this.attempt, delayMs, reason: this.kickReason, code: this.kickCode });
    this.later(delayMs, async () => {
      let ok: boolean;
      try {
        ok = await this.handlers.beforeReconnect();
      } catch {
        ok = true; // the HTTP check itself failed (server down): keep trying
      }
      if (!ok) this.stop();
      else if (!this.stopped) this.connect();
    });
  }

  /** Sends one encoded `ClientMsg`. Dropped silently while disconnected. */
  send(frame: Uint8Array): void {
    if (this.ws?.readyState === WebSocket.OPEN) sendFrame(this.ws, frame);
  }

  /** Closes the socket and stops reconnecting. */
  stop(): void {
    this.stopped = true;
    for (const t of this.timers) clearTimeout(t);
    this.timers = [];
    this.stopPing();
    const ws = this.ws;
    this.ws = null;
    ws?.close(1000, "bye");
    this.handlers.onStatus({ kind: "closed" });
  }

  private startPing(ws: WebSocket): void {
    this.stopPing();
    const ping = () => {
      if (ws.readyState === WebSocket.OPEN) sendFrame(ws, encodePing(this.now()));
    };
    ping();
    this.pingTimer = setInterval(ping, PING_INTERVAL_MS);
  }

  private stopPing(): void {
    if (this.pingTimer !== null) clearInterval(this.pingTimer);
    this.pingTimer = null;
  }

  /** Milliseconds since this connection object was created, as a u32 for `Ping`. */
  private now(): number {
    return Math.floor(performance.now() - this.epoch) >>> 0;
  }

  private later(ms: number, fn: () => void): void {
    const t = setTimeout(() => {
      this.timers = this.timers.filter((x) => x !== t);
      fn();
    }, ms);
    this.timers.push(t);
  }
}

/**
 * wasm-bindgen types its byte arrays as `Uint8Array<ArrayBufferLike>`; they are
 * always backed by a plain (copied-out) ArrayBuffer, which is what `send` wants.
 */
function sendFrame(ws: WebSocket, frame: Uint8Array): void {
  ws.send(frame as Uint8Array<ArrayBuffer>);
}

/** `ws://host/ws` or `wss://host/ws` for the page's own origin (Vite proxies it in dev). */
export function gameSocketUrl(): string {
  return `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
}
