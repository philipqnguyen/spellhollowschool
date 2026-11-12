// Measurement switch: `?charfx=off` in the page URL draws characters as
// before the adoption's client features, in the same build, for A/B
// frame-time comparisons: no shade layer buffer (the toon shader's shade
// code compiles out), no character shadow map, no blobs, no hair sway, and
// the old LOD rule (LOD0 within 15 m, LOD1 beyond, no budget). One feature
// at a time: `?charfx=-shade,-shadows,-blobs,-sway,-lod0,-lod` turns off
// only those listed (`-lod0`: nobody but you at LOD0, the rest by the
// adoption's rules; `-lod`: the old rule). The character look's face
// (docs/art/character-look.md section 3): `-face` draws the heads without their face
// textures (the face path compiled out), `-facesdf` keeps the textures but
// not the face-shadow map. Never set in normal play.

export interface CharacterFxFlags {
  shade: boolean;
  shadows: boolean;
  blobs: boolean;
  sway: boolean;
  /** The nearest characters at LOD0 (else only your own). */
  lod0: boolean;
  /** The adoption's four-tier rules and budget (else the old two-tier rule). */
  lod: boolean;
  /** The face textures (character look). */
  face: boolean;
  /** The face-shadow map at Medium and High (character look). */
  facesdf: boolean;
}

/** The flags for a `charfx` value (null: everything on). Pure. */
export function parseCharacterFx(value: string | null): CharacterFxFlags {
  const on: CharacterFxFlags = { shade: true, shadows: true, blobs: true, sway: true, lod0: true, lod: true, face: true, facesdf: true };
  if (value === null) return on;
  if (value === "off") return { shade: false, shadows: false, blobs: false, sway: false, lod0: false, lod: false, face: false, facesdf: false };
  for (const t of value.split(",")) {
    const k = t.trim().replace(/^-/, "") as keyof CharacterFxFlags;
    if (k in on) on[k] = false;
  }
  return on;
}

function read(): CharacterFxFlags {
  try {
    const search = (globalThis as { location?: { search?: string } }).location?.search ?? "";
    return parseCharacterFx(new URLSearchParams(search).get("charfx"));
  } catch {
    return parseCharacterFx(null);
  }
}

export const CHARACTER_FX = read();
/** Every adoption feature off (`?charfx=off`). */
export const CHARACTER_FX_OFF = !CHARACTER_FX.shade && !CHARACTER_FX.shadows && !CHARACTER_FX.blobs && !CHARACTER_FX.sway && !CHARACTER_FX.lod;
