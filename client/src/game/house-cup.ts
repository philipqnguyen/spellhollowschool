// The House Cup in the running game: polls `GET /api/house-cup`
// every 60 s while in game (and when the panel opens, if the last answer is
// older than 10 s), and hands the standings to the panel, the Great Hall's
// hourglasses and banners (castle/cup.ts) and the nameplates' cup badge.
// HTTP only: no protocol (docs/architecture.md).

import { authedRequest } from "../api";
import { audio } from "../audio";
import { HouseCupPanel, type BoardClass } from "../ui/house-cup";
import { CUP_HOUSES, lastText, parseCup, type CupHouse, type CupState } from "../ui/house-cup-state";
import type { KitCup } from "./castle/cup";

/** How often the standings are fetched while in game (ms). */
export const CUP_POLL_MS = 60_000;
/** Opening the panel fetches again if the last answer is older than this (ms). */
const FRESH_MS = 10_000;
/** A failed fetch tries again after this (ms). */
const RETRY_MS = 15_000;

export interface HouseCupHubOptions {
  hudRoot: HTMLElement;
  hudActions: HTMLElement;
  /** Our house id (`"skytalon"`). */
  house: string;
  kit: KitCup;
  boardClasses: () => readonly BoardClass[];
  onWay: (classId: string) => void;
  notify: (text: string) => void;
}

export class HouseCupHub {
  readonly panel: HouseCupPanel;
  private state: CupState | null = null;
  private fetchedAt = -Infinity;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private inflight = false;
  private disposed = false;
  private tickIn = 0;

  constructor(private readonly opts: HouseCupHubOptions) {
    this.panel = new HouseCupPanel({
      house: opts.house,
      boardClasses: opts.boardClasses,
      onWay: opts.onWay,
      onOpen: () => {
        if (performance.now() - this.fetchedAt > FRESH_MS) void this.poll();
      },
    });
    opts.hudActions.append(this.panel.button);
    opts.hudRoot.append(this.panel.root);
  }

  /** Starts polling (once in game). */
  start(): void {
    if (this.timer === null && !this.inflight) void this.poll();
  }

  /** Whether players of `house` wear the cup badge now. */
  badged(house: string): boolean {
    return this.state?.badge.has(house.toLowerCase() as CupHouse) ?? false;
  }

  get current(): CupState | null {
    return this.state;
  }

  /** Opens the panel; `board`: from a notice board, with the house's next classes. */
  open(board: boolean): void {
    this.panel.toggle(true, board);
  }

  handleKey(e: KeyboardEvent): boolean {
    return this.panel.handleKey(e);
  }

  /** Chat commands: /cup, /housecup. */
  command(line: string): boolean {
    const cmd = line.trim().split(/\s+/)[0]?.toLowerCase();
    if (cmd !== "/cup" && cmd !== "/housecup") return false;
    this.panel.toggle(true);
    return true;
  }

  /** Per frame: the hourglasses ease; the open panel's countdown ticks once a second. */
  update(dt: number): void {
    this.opts.kit.update(dt);
    this.tickIn -= dt;
    if (this.tickIn <= 0) {
      this.tickIn = 1;
      this.panel.tick();
    }
  }

  private async poll(): Promise<void> {
    if (this.disposed || this.inflight) return;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    this.inflight = true;
    let next = CUP_POLL_MS;
    try {
      const s = parseCup(await authedRequest<unknown>("GET", "/house-cup"), Date.now());
      if (s && !this.disposed) this.apply(s);
    } catch (e) {
      console.warn("[house cup] standings unavailable:", e);
      next = RETRY_MS;
    } finally {
      this.inflight = false;
    }
    if (!this.disposed) this.timer = setTimeout(() => void this.poll(), next);
  }

  private apply(s: CupState): void {
    const before = this.state;
    this.state = s;
    this.fetchedAt = performance.now();
    this.opts.kit.setFills(new Map(s.houses.map((x) => [x.id, x.share])));
    this.opts.kit.setBadge(s.badge);
    this.panel.update(s);
    // A week closed while we played: say who won.
    const closed = !!before && !!s.last && (!before.last || before.last.week.startMs !== s.last.week.startMs);
    if (closed) {
      audio.stinger("stinger.cup_week");
      this.opts.notify(`The House Cup week has closed. ${lastText(s)}`);
    } else if (before && before.week.startMs === s.week.startMs) {
      // Our house's points grew in the same week: gems falling in its hourglass.
      const ours = (c: CupState) => c.houses.find((x) => x.id === this.opts.house.toLowerCase())?.points ?? 0;
      if (ours(s) > ours(before)) audio.play("cup.points");
    }
  }

  /** For the F3 overlay. */
  perfLine(): [string, string] {
    const { sands, banners } = this.opts.kit.counts;
    const s = this.state;
    const pts = s ? CUP_HOUSES.map((id) => s.houses.find((x) => x.id === id)?.points ?? 0).join("/") : "-";
    return ["house cup", `${pts}, badge ${s ? [...s.badge].join(",") || "none" : "-"}, ${sands} sand, ${banners} banner groups`];
  }

  dispose(): void {
    this.disposed = true;
    if (this.timer !== null) clearTimeout(this.timer);
    this.panel.root.remove();
    this.panel.button.remove();
  }
}
