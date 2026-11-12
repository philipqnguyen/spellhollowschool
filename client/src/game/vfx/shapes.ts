// The VFX layer's procedural shapes (its shader's `shape` ids), apart from
// the Babylon code so the pure particle and duel modules can name them.

export const Shape = {
  /** A soft orb with a hot core: bolt heads, wand charge, flashes. */
  orb: 0,
  /** A four-pointed glint: sparks. */
  spark: 1,
  /** A ring with a faint patterned fill: Scutara, shockwaves. */
  ring: 2,
  /** A comet's tail from the centre back along the axis (axis = tail direction and length); draw an orb at its head. */
  streak: 3,
  /** A jagged shard: a shattered shield, a disarmed wand's splinters. */
  shard: 4,
  /**
   * A ring lying in the plane across its axis (axis = the ring's normal; its
   * length is ignored): bind rings round a body, chalk circles on the floor.
   * Burst particles of this shape lie flat (normal up).
   */
  disc: 5,
  /**
   * A band centred on the sprite, spanning its axis (axis = the band's whole
   * length and direction), `size` wide, turned about the axis toward the
   * camera: a wave's wall of light, a tether between two wands.
   */
  beam: 6,
} as const;

/** Shapes drawn along (or across) an axis: their sprites need one. */
export function hasAxis(shape: ShapeId): boolean {
  return shape === Shape.streak || shape === Shape.disc || shape === Shape.beam;
}

export type ShapeId = (typeof Shape)[keyof typeof Shape];
