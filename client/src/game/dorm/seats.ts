// Sitting on placed furniture: the seats of a dorm room's chairs, stools,
// armchairs and beds (the catalog's `furniture.seats`) for the sit emote,
// like the castle's chairs (castle/seats.ts): a character playing `sit` by
// one is drawn on it. Visual only, as there: the server checks the sit
// emote itself (standing still, not riding, its rate), for the owner and
// guests alike. Rebuilt whenever the room changes.

import { Seats, type SeatPlace } from "../castle/seats";
import type { SeatLookup } from "../characters/cast";
import type { DormRoom } from "./room";

export class FurnitureSeats implements SeatLookup {
  private seats: Seats | null = null;
  private builtVersion = -1;

  /** `base`: the room's own seats, if its layout has any (used when no furniture seat is in reach). */
  constructor(
    private readonly room: DormRoom,
    private readonly base: SeatLookup | null = null,
  ) {}

  /** How many furniture seat rows there are now (tests, probes). */
  get count(): number {
    this.rebuild();
    return this.seats?.count ?? 0;
  }

  find(x: number, y: number, z: number, out: SeatPlace, facing = Number.NaN): boolean {
    this.rebuild();
    if (this.seats?.find(x, y, z, out, facing)) return true;
    return this.base?.find(x, y, z, out, facing) ?? false;
  }

  private rebuild(): void {
    if (this.builtVersion === this.room.version) return;
    this.builtVersion = this.room.version;
    const rows = this.room.seatRows();
    this.seats = rows.length ? Seats.fromRows(rows) : null;
  }
}
