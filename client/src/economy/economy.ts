// The economy in the running game, in one place for game.ts: the purse and
// the owl on the HUD, owl mail, the owl-order catalogue (at its stand in the
// common room), the Owl Exchange (at its counter), trading (requests, the
// window, the player menu and /trade), and the realtime messages that keep
// them current (`Wallet`, `MailNotice`, `StationOpened` for the counter and
// the stand, `Trade*`).
//
// game.ts hands every server message to `onMessage` first (it answers true
// for the ones it consumed), asks `handleKey` and `command` before its own
// keys and chat, and calls `update` on its HUD tick.

import { authedRequest } from "../api";
import { audio } from "../audio";
import { fromWire, type WireAppearance } from "../content/appearance";
import { ExchangeWindow } from "../ui/exchange";
import { MailWindow } from "../ui/mail";
import { ShopWindow } from "../ui/shop";
import { PlayerMenu, respond, TradeToasts, TradeWindow, type PlayerMenuAction } from "../ui/trade";
import { WalletDisplay } from "../ui/wallet";
import { encodeTradeCancel, encodeTradeRequest, type Appearance, type Interactable, type ProtocolConstants, type ServerMessage, type Vec3 } from "../wasm/sim_wasm";
import { loadIconManifest } from "./icons";
import { ECONOMY } from "./money";
import { grantHolds, horizontalDistance, pickCharacter, type PickTarget } from "./pick";
import { TradeSession } from "./trade-state";

export interface EconomyOptions {
  /** Where the windows and toasts go. */
  hudRoot: HTMLElement;
  /** The HUD bar's purse slot (wallet and mail button). */
  purseSlot: HTMLElement;
  /** The world canvas, for right-clicking players. */
  canvas: HTMLCanvasElement;
  myName: string;
  protocol: Pick<ProtocolConstants, "tradeRequestSecs" | "tradeItemsMax">;
  send: (frame: Uint8Array) => void;
  /** A line for the chat log. */
  notify: (text: string) => void;
  /** Opens the chat to whisper `name`. */
  whisper: (name: string) => void;
  /** Our entity id (-1 before Welcome). */
  selfId: () => number;
  /** Our feet. */
  selfPos: () => Vec3;
  /** The characters in view, with where their heads are. */
  nearby: () => readonly PickTarget[];
  /** The camera's view-projection (`scene.getTransformMatrix().m`). */
  viewProjection: () => ArrayLike<number>;
  /** The interactables of the zone shown. */
  interactables: () => readonly Interactable[];
  /** Names worth suggesting for letters (friends). */
  friendNames: () => Iterable<string>;
  /** More entries for the player menu (dueling: "Challenge to a duel"), after the economy's own. */
  menuItems?: (target: PickTarget) => readonly PlayerMenuAction[];
}

/** A right click that moved less than this is a click, not a camera drag (px). */
const CLICK_SLOP_PX = 6;
const CLICK_MS = 600;
/** Our look for the Exchange's try-on is fetched at most this often (ms). */
const LOOK_TTL_MS = 30_000;

export class Economy {
  readonly wallet = new WalletDisplay();
  readonly mail: MailWindow;
  /** Sound: the balance and unread count last heard (null: none since the connection opened). */
  private heardCrowns: number | null = null;
  private heardUnread: number | null = null;
  readonly shop: ShopWindow;
  readonly exchange: ExchangeWindow;
  private readonly session = new TradeSession();
  private readonly tradeWindow: TradeWindow;
  private readonly toasts: TradeToasts;
  private readonly menu = new PlayerMenu();
  /** The counter the Exchange was opened at (for walking away). */
  private counter: Interactable | null = null;
  /** The Owl Order Catalogue stand the catalogue was opened at (for walking away). */
  private stand: Interactable | null = null;
  private look: { at: number; appearance: Promise<Appearance | null> } | null = null;
  private press: { x: number; y: number; t: number } | null = null;
  private readonly unsubscribe: () => void;
  private readonly onPointerDown = (e: PointerEvent) => {
    if (e.button === 2) this.press = { x: e.clientX, y: e.clientY, t: performance.now() };
  };
  private readonly onPointerUp = (e: PointerEvent) => {
    const p = this.press;
    this.press = null;
    if (e.button !== 2 || !p) return;
    if (Math.hypot(e.clientX - p.x, e.clientY - p.y) > CLICK_SLOP_PX || performance.now() - p.t > CLICK_MS) return;
    this.pickAt(e.clientX, e.clientY);
  };

  constructor(private readonly opts: EconomyOptions) {
    const balance = () => this.wallet.crowns;
    const onBalance = (g: number) => this.wallet.set(g);
    this.mail = new MailWindow({
      myName: opts.myName,
      balance,
      onBalance,
      knownNames: () => [...opts.friendNames(), ...opts.nearby().map((t) => t.name)],
      notify: opts.notify,
      layer: opts.hudRoot,
    });
    this.shop = new ShopWindow({ balance, onBalance, notify: opts.notify });
    this.exchange = new ExchangeWindow({ myName: opts.myName, balance, onBalance, notify: opts.notify, appearance: () => this.myLook() });
    this.tradeWindow = new TradeWindow({ session: this.session, send: opts.send, balance, itemsMax: opts.protocol.tradeItemsMax, notify: opts.notify });
    this.toasts = new TradeToasts(
      this.session,
      (tradeId, accept) => {
        const req = this.session.responded(tradeId);
        respond(opts.send, tradeId, accept);
        if (!accept && req) opts.notify(`You declined ${req.fromName}'s trade.`);
        this.toasts.render();
      },
      (text) => opts.notify(text),
      (tradeId) => opts.send(encodeTradeCancel(tradeId)),
    );
    this.unsubscribe = this.wallet.subscribe(() => {
      this.shop.balanceChanged();
      this.exchange.balanceChanged();
      if (this.tradeWindow.isOpen) this.tradeWindow.render();
    });
    opts.purseSlot.append(this.wallet.root, this.mail.button);
    opts.hudRoot.append(this.toasts.root, this.mail.root, this.shop.root, this.exchange.root, this.tradeWindow.root, this.menu.root);
    opts.canvas.addEventListener("pointerdown", this.onPointerDown);
    opts.canvas.addEventListener("pointerup", this.onPointerUp);
    loadIconManifest();
  }

  // ---------------------------------------------------------------- messages

  /** A shopkeeper's greeting as its shop opens (NPCs, game/npc/hub.ts): shown in the window of `kind`. */
  greet(kind: "owl_exchange" | "owl_order", name: string, text: string): void {
    if (kind === "owl_exchange") this.exchange.greet(name, text);
    else this.shop.greet(name, text);
  }

  /** Handles the economy's server messages; true if `msg` was one of them (and is done with). */
  onMessage(msg: ServerMessage): boolean {
    switch (msg.type) {
      case "welcome":
        // A new connection: balances and counts are reported afresh, and
        // every trade and counter grant ended with the old one.
        this.wallet.reconnected();
        this.mail.reconnected();
        // Sound: the first balance and mail count after a connection are a report, not a change.
        this.heardCrowns = null;
        this.heardUnread = null;
        this.session.reset();
        this.renderTrade();
        this.exchange.leftCounter(null);
        this.shop.leftStand(null);
        return false;
      case "zoneChanged":
        this.exchange.leftCounter("You left the Owl Exchange counter.");
        this.shop.leftStand("You left the Owl Order Catalogue.");
        return false;
      case "wallet":
        if (this.heardCrowns !== null && msg.crowns !== this.heardCrowns) audio.play(msg.crowns > this.heardCrowns ? "ui.coins_in" : "ui.coins_out");
        this.heardCrowns = msg.crowns;
        this.wallet.set(msg.crowns);
        return true;
      case "mailNotice":
        if (this.heardUnread !== null && msg.unread > this.heardUnread) audio.play("mail.arrive");
        this.heardUnread = msg.unread;
        this.mail.notice(msg.unread);
        return true;
      case "stationOpened":
        if (msg.kind === "owl_order") {
          this.stand = this.opts.interactables().find((i) => i.id === msg.target) ?? null;
          this.shop.openAtStand(msg.expiresInSecs);
          return true;
        }
        if (msg.kind !== "owl_exchange") return false;
        this.counter = this.opts.interactables().find((i) => i.id === msg.target) ?? null;
        this.exchange.openAtCounter(msg.expiresInSecs);
        return true;
      case "tradeRequested":
        if (msg.from === this.opts.selfId()) {
          // Our own request echoed back with its id.
          this.session.acknowledged(msg.tradeId, msg.expiresInSecs, performance.now());
          this.toasts.render();
          return true;
        }
        this.session.onRequested(msg.tradeId, msg.from, msg.fromName, msg.expiresInSecs, performance.now());
        audio.play("ui.invite");
        this.opts.notify(`${msg.fromName} would like to trade with you.`);
        this.toasts.render();
        return true;
      case "tradeState": {
        const opening = !this.session.open;
        this.session.onState(msg.view);
        if (opening) this.opts.notify(`Trading with ${msg.view.partnerName}.`);
        this.renderTrade();
        return true;
      }
      case "tradeResult": {
        const text = this.session.onResult(msg.tradeId, msg.outcome);
        if (msg.outcome.kind === "completed") audio.play("trade.complete");
        if (text) this.opts.notify(text);
        this.renderTrade();
        return true;
      }
      case "tradeError": {
        const text = this.session.onError(msg.tradeId, msg.code);
        if (this.tradeWindow.isOpen && msg.tradeId !== undefined) this.tradeWindow.showError(text);
        else this.opts.notify(text);
        this.renderTrade();
        return true;
      }
      default:
        return false;
    }
  }

  private renderTrade(): void {
    this.tradeWindow.render();
    this.toasts.render();
  }

  // ---------------------------------------------------------------- input

  /** Characters look at their target: the partner's entity id while a trade window is open (null otherwise). */
  /** Our Crowns as the server last said (0 before the first `Wallet`). */
  get crowns(): number {
    return this.heardCrowns ?? 0;
  }

  get tradePartner(): number | null {
    return this.session.view?.partner ?? null;
  }

  /** The trade window, a window of the stack (game.ts). */
  get tradeRoot(): HTMLElement {
    return this.tradeWindow.root;
  }

  /** Esc reaching the trade window on top of the stack: asks, and a second Esc cancels (as before). */
  tradeEscape(): void {
    this.tradeWindow.handleKey(new KeyboardEvent("keydown", { key: "Escape" }));
  }

  /** Keys the economy takes: N mail (M is the map), and Escape for whichever of its windows is open. */
  handleKey(e: KeyboardEvent): boolean {
    if (this.menu.isOpen && e.key === "Escape") {
      this.menu.close();
      return true;
    }
    return this.tradeWindow.handleKey(e) || this.exchange.handleKey(e) || this.mail.handleKey(e) || this.shop.handleKey(e);
  }

  /** Chat commands: /trade Name, /mail Name, /listings. True if `line` was one. */
  command(line: string): boolean {
    const m = /^\/(trade|mail|owl|listings)\b\s*(.*)$/i.exec(line.trim());
    if (!m) return false;
    const verb = m[1]!.toLowerCase();
    const arg = m[2]!.trim().replace(/^"(.*)"$/, "$1");
    if (verb === "listings") {
      this.exchange.openMine();
    } else if (verb === "mail" || verb === "owl") {
      if (arg) this.mail.writeTo(arg);
      else this.mail.toggle(true);
    } else if (!arg) {
      this.opts.notify("Trade with whom? Type /trade Name, or right-click someone nearby.");
    } else {
      const target = this.opts.nearby().find((t) => t.name.toLowerCase() === arg.toLowerCase());
      if (!target) this.opts.notify(`Nobody called ${arg} is close enough to see.`);
      else this.requestTrade(target);
    }
    return true;
  }

  /** Asks `target` to trade, after the checks the server will make anyway. */
  requestTrade(target: Pick<PickTarget, "id" | "name" | "x" | "y" | "z">): void {
    const problem = this.session.canRequest(target.id, this.opts.selfId());
    if (problem) {
      this.opts.notify(problem);
      return;
    }
    const range = ECONOMY.trade.distance_m;
    if (horizontalDistance(this.opts.selfPos(), target) > range) {
      this.opts.notify(`Walk closer to ${target.name} to trade (within ${range} m).`);
      return;
    }
    this.opts.send(encodeTradeRequest(target.id));
    this.session.requested(target.id, target.name, performance.now(), this.opts.protocol.tradeRequestSecs);
    this.toasts.render();
  }

  /** A right click on the world: the menu for the player under it, if any. */
  private pickAt(clientX: number, clientY: number): void {
    const rect = this.opts.canvas.getBoundingClientRect();
    const hit = pickCharacter(this.opts.nearby(), this.opts.viewProjection(), rect.width, rect.height, clientX - rect.left, clientY - rect.top);
    if (!hit) return;
    this.menu.open(hit.name, clientX + 4, clientY + 4, [
      { label: "Trade", run: () => this.requestTrade(hit) },
      { label: "Whisper", run: () => this.opts.whisper(hit.name) },
      { label: "Send an owl", run: () => this.mail.writeTo(hit.name) },
      ...(this.opts.menuItems?.(hit) ?? []),
    ]);
  }

  // ---------------------------------------------------------------- frame

  /** The HUD tick: the counter and the catalogue close when we walk away from them. */
  update(): void {
    if (this.counter) this.exchange.update(grantHolds(this.counter, this.opts.selfPos()));
    if (this.stand) this.shop.update(grantHolds(this.stand, this.opts.selfPos()));
  }

  /** Our appearance for the Exchange's try-on, fetched now and then. */
  private myLook(): Promise<Appearance | null> {
    const now = performance.now();
    if (!this.look || now - this.look.at > LOOK_TTL_MS) {
      const appearance = authedRequest<{ appearance: WireAppearance | null }>("GET", "/characters/me")
        .then((me) => (me.appearance ? fromWire(me.appearance) : null))
        .catch(() => null);
      this.look = { at: now, appearance };
    }
    return this.look.appearance;
  }

  dispose(): void {
    this.opts.canvas.removeEventListener("pointerdown", this.onPointerDown);
    this.opts.canvas.removeEventListener("pointerup", this.onPointerUp);
    this.unsubscribe();
    this.menu.dispose();
    this.toasts.dispose();
    this.tradeWindow.dispose();
    this.exchange.dispose();
    this.shop.dispose();
    this.mail.dispose();
    this.wallet.dispose();
  }
}
