// Keyset pages for every economy list (docs/architecture.md: every list
// endpoint answers `{ items, next }` and takes `after`), and retry keys for
// the writes the server makes idempotent with `request_id`.
//
// `KeysetPager` walks pages forward and back without offsets: it remembers
// the cursor that opened each page it has shown, so Previous asks for the
// page again by the cursor that led to it. Each request gets a ticket; a reply
// to an older ticket (the player clicked Next twice, or changed the filters
// meanwhile) is ignored, so a slow reply can never overwrite a newer page.
// Pure, unit tested in paging.test.ts.

/** A keyset page as the API sends it. */
export interface Page<T> {
  items: T[];
  next: string | null;
}

/** What to fetch: the `after` cursor (null for the first page) and the ticket to answer with. */
export interface PageRequest {
  ticket: number;
  after: string | null;
}

export type PageMove = "first" | "next" | "prev" | "reload";

export class KeysetPager<T> {
  /** `cursors[i]` opened page i; `cursors[0]` is the first page (null). */
  private cursors: (string | null)[] = [null];
  private index = 0;
  private ticket = 0;
  /** The move in flight and the page it will show. */
  private pending: { ticket: number; index: number; after: string | null } | null = null;
  items: T[] = [];
  next: string | null = null;
  /** Whether a page has arrived since the last `first`. */
  loaded = false;

  /** The page shown, counted from 0. */
  get page(): number {
    return this.index;
  }

  get hasPrev(): boolean {
    return this.index > 0;
  }

  get hasNext(): boolean {
    return this.next !== null;
  }

  get loading(): boolean {
    return this.pending !== null;
  }

  /**
   * Starts a move and returns what to request, or null when there is nowhere
   * to go (Previous on the first page, Next on the last). `first` starts over
   * (new filters); `reload` asks for the page shown again (after a change).
   */
  begin(move: PageMove): PageRequest | null {
    let index: number;
    switch (move) {
      case "first":
        this.cursors = [null];
        this.items = [];
        this.next = null;
        this.loaded = false;
        index = 0;
        break;
      case "next":
        if (this.next === null) return null;
        index = this.index + 1;
        break;
      case "prev":
        if (this.index === 0) return null;
        index = this.index - 1;
        break;
      case "reload":
        index = this.index;
        break;
    }
    const ticket = ++this.ticket;
    const after = move === "next" ? this.next : (this.cursors[index] ?? null);
    this.pending = { ticket, index, after };
    return { ticket, after };
  }

  /** A reply arrived; returns false (and changes nothing) when it is stale. */
  receive(ticket: number, page: Page<T>): boolean {
    const p = this.pending;
    if (!p || p.ticket !== ticket) return false;
    this.cursors[p.index] = p.after;
    this.cursors.length = p.index + 1;
    this.index = p.index;
    this.items = page.items;
    this.next = page.next;
    this.loaded = true;
    this.pending = null;
    return true;
  }

  /** The request for `ticket` failed: keep the page shown. Returns false when stale. */
  fail(ticket: number): boolean {
    if (this.pending?.ticket !== ticket) return false;
    this.pending = null;
    return true;
  }

  /** Replaces one entry on the page shown (by id), e.g. after it was read. */
  update(match: (item: T) => boolean, change: (item: T) => T): void {
    this.items = this.items.map((i) => (match(i) ? change(i) : i));
  }

  /** Removes entries from the page shown (taken down, deleted). */
  remove(match: (item: T) => boolean): void {
    this.items = this.items.filter((i) => !match(i));
  }

  /** "Page 2", or "Page 2 of 2" once the last page is reached. */
  label(): string {
    return this.next === null && this.loaded ? `Page ${this.index + 1} of ${this.index + 1}` : `Page ${this.index + 1}`;
  }
}

/**
 * `request_id`s for retryable writes: the same action (an order, a letter,
 * a purchase of one listing) keeps its id until it succeeds or is refused
 * for good, so a retry after a timeout cannot do it twice; a new action gets
 * a new id.
 */
export class RetryKeys {
  private readonly ids = new Map<string, string>();

  constructor(private readonly make: () => string = randomId) {}

  /** The id for `action`, the same one until `settle(action)`. */
  get(action: string): string {
    let id = this.ids.get(action);
    if (!id) this.ids.set(action, (id = this.make()));
    return id;
  }

  /** The action finished (or was refused): its next attempt is a new one. */
  settle(action: string): void {
    this.ids.delete(action);
  }

  /** Forget every pending id. */
  clear(): void {
    this.ids.clear();
  }
}

/** A random request id (1-64 chars, as the API allows). */
export function randomId(): string {
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  return `r${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
}

/**
 * Whether a failed write may be retried with the same `request_id`: no
 * answer at all (network) or a server error. A 4xx refusal is final, so the
 * next attempt is a new action.
 */
export function isRetryable(status: number): boolean {
  return status === 0 || status >= 500;
}
