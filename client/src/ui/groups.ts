// The groups a character can share with the player, and which one tints
// their nameplate (docs/architecture.md "Parties", "Clubs"): party blue,
// then club green. Pure, unit tested in groups.test.ts.
//
// A nameplate shows one group at most: the first of `GROUP_PRECEDENCE` the
// character belongs to. A new kind of group adds its kind here, in its place
// in the order, and a `.nameplate[data-group="<kind>"]` rule in style.css;
// the plate code itself needs no change.

/** A kind of group, as nameplates and CSS name it. */
export type GroupKind = "party" | "club";

/** Which group wins when a character is in several of ours: earlier wins. */
export const GROUP_PRECEDENCE: readonly GroupKind[] = ["party", "club"];

/** One group of ours: does the character drawn as `entityId` belong to it? */
export interface GroupMembership<K extends string = GroupKind> {
  readonly kind: K;
  has(entityId: number): boolean;
}

/** The group that tints `entityId`'s nameplate (the first of `order` it is in), or null for none. */
export function plateGroup<K extends string = GroupKind>(entityId: number, groups: readonly GroupMembership<K>[], order: readonly K[] = GROUP_PRECEDENCE as readonly string[] as readonly K[]): K | null {
  for (const kind of order) {
    for (const g of groups) if (g.kind === kind && g.has(entityId)) return kind;
  }
  return null;
}
