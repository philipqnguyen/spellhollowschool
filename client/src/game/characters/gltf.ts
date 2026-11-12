// A small glTF 2.0 binary (.glb) reader for character parts: just what the
// character pipeline needs (nodes, skinned meshes, morph targets, skins,
// materials' base colour, animations and `extras`), with every accessor
// decoded to plain typed arrays.
//
// Characters do not go through Babylon's loader: parts are merged into one
// mesh per character on the CPU (see assemble.ts), so the raw vertex data is
// what we want, and parsing without a scene lets the world and the wardrobe
// mirror (separate engines) share one cache. Pure, unit tested.

const GLB_MAGIC = 0x46546c67; // "glTF"
const CHUNK_JSON = 0x4e4f534a;
const CHUNK_BIN = 0x004e4942;

export type Extras = Record<string, unknown>;

export interface GltfAccessor {
  bufferView?: number;
  byteOffset?: number;
  componentType: number;
  normalized?: boolean;
  count: number;
  type: "SCALAR" | "VEC2" | "VEC3" | "VEC4" | "MAT2" | "MAT3" | "MAT4";
  sparse?: {
    count: number;
    indices: { bufferView: number; byteOffset?: number; componentType: number };
    values: { bufferView: number; byteOffset?: number };
  };
}

export interface GltfPrimitive {
  attributes: Record<string, number>;
  indices?: number;
  material?: number;
  mode?: number;
  targets?: Record<string, number>[];
  extras?: Extras;
}

export interface GltfNode {
  name?: string;
  children?: number[];
  mesh?: number;
  skin?: number;
  translation?: [number, number, number];
  rotation?: [number, number, number, number];
  scale?: [number, number, number];
  matrix?: number[];
  extras?: Extras;
  extensions?: { MSFT_lod?: { ids: number[] } } & Record<string, unknown>;
}

export interface GltfJson {
  asset: { version: string; extras?: Extras };
  scene?: number;
  scenes?: { nodes?: number[]; extras?: Extras }[];
  nodes?: GltfNode[];
  meshes?: { name?: string; primitives: GltfPrimitive[]; weights?: number[]; extras?: Extras & { targetNames?: string[] } }[];
  skins?: { joints: number[]; inverseBindMatrices?: number; skeleton?: number; name?: string }[];
  materials?: { name?: string; pbrMetallicRoughness?: { baseColorFactor?: number[]; baseColorTexture?: { index: number; texCoord?: number } }; extras?: Extras }[];
  textures?: { source?: number; sampler?: number }[];
  images?: { bufferView?: number; mimeType?: string; uri?: string; name?: string }[];
  animations?: {
    name?: string;
    channels: { sampler: number; target: { node?: number; path: "translation" | "rotation" | "scale" | "weights" } }[];
    samplers: { input: number; output: number; interpolation?: "LINEAR" | "STEP" | "CUBICSPLINE" }[];
  }[];
  accessors?: GltfAccessor[];
  bufferViews?: { buffer: number; byteOffset?: number; byteLength: number; byteStride?: number }[];
  buffers?: { byteLength: number; uri?: string }[];
  extensionsUsed?: string[];
  extensionsRequired?: string[];
}

export class GltfError extends Error {
  override name = "GltfError";
}

const COMPONENTS: Record<GltfAccessor["type"], number> = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4, MAT2: 4, MAT3: 9, MAT4: 16 };
const COMPONENT_BYTES: Record<number, number> = { 5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4 };
/** Extensions this reader can ignore safely when a file requires them. */
const SUPPORTED_REQUIRED = new Set(["KHR_mesh_quantization", "KHR_materials_emissive_strength"]);

/** Whether `bytes` start like a binary glTF file (a dev server answers missing files with HTML). */
export function isGlb(bytes: ArrayBuffer): boolean {
  return bytes.byteLength >= 12 && new DataView(bytes).getUint32(0, true) === GLB_MAGIC;
}

/** A parsed .glb: its JSON and binary chunk, with accessor decoding. */
export class Gltf {
  readonly json: GltfJson;
  private readonly bin: Uint8Array;

  constructor(json: GltfJson, bin: Uint8Array) {
    this.json = json;
    this.bin = bin;
    for (const ext of json.extensionsRequired ?? []) {
      if (!SUPPORTED_REQUIRED.has(ext)) throw new GltfError(`unsupported required glTF extension ${ext}`);
    }
  }

  static parse(bytes: ArrayBuffer): Gltf {
    if (!isGlb(bytes)) throw new GltfError("not a binary glTF file");
    const view = new DataView(bytes);
    const version = view.getUint32(4, true);
    if (version !== 2) throw new GltfError(`glTF version ${version} is not supported`);
    const length = Math.min(view.getUint32(8, true), bytes.byteLength);
    let offset = 12;
    let json: GltfJson | null = null;
    let bin = new Uint8Array(0);
    while (offset + 8 <= length) {
      const chunkLength = view.getUint32(offset, true);
      const chunkType = view.getUint32(offset + 4, true);
      const start = offset + 8;
      if (start + chunkLength > length) throw new GltfError("truncated glTF chunk");
      if (chunkType === CHUNK_JSON) {
        json = JSON.parse(new TextDecoder().decode(new Uint8Array(bytes, start, chunkLength))) as GltfJson;
      } else if (chunkType === CHUNK_BIN && bin.byteLength === 0) {
        bin = new Uint8Array(bytes, start, chunkLength);
      }
      offset = start + chunkLength + ((4 - (chunkLength % 4)) % 4);
    }
    if (!json) throw new GltfError("glTF file has no JSON chunk");
    return new Gltf(json, bin);
  }

  /** The raw bytes of bufferView `index` (embedded images). */
  bufferViewBytes(index: number): Uint8Array {
    const bv = this.json.bufferViews?.[index];
    if (!bv || bv.buffer !== 0) throw new GltfError(`missing bufferView ${index}`);
    const start = bv.byteOffset ?? 0;
    if (start + bv.byteLength > this.bin.byteLength) throw new GltfError("bufferView outside the binary chunk");
    return this.bin.slice(start, start + bv.byteLength);
  }

  accessor(index: number): GltfAccessor {
    const a = this.json.accessors?.[index];
    if (!a) throw new GltfError(`missing accessor ${index}`);
    return a;
  }

  /** Number of components per element of accessor `index` (3 for VEC3). */
  components(index: number): number {
    return COMPONENTS[this.accessor(index).type];
  }

  /**
   * Accessor `index` as floats, applying `normalized` integer conversion
   * (KHR_mesh_quantization, unorm colours and weights) and sparse values.
   */
  floats(index: number): Float32Array {
    const a = this.accessor(index);
    const n = COMPONENTS[a.type];
    const out = new Float32Array(a.count * n);
    this.readInto(a, n, out, a.normalized === true);
    return out;
  }

  /** Accessor `index` as unsigned integers (indices, joints). */
  uints(index: number): Uint32Array {
    const a = this.accessor(index);
    const n = COMPONENTS[a.type];
    const out = new Uint32Array(a.count * n);
    this.readInto(a, n, out, false);
    return out;
  }

  private readInto(a: GltfAccessor, n: number, out: Float32Array | Uint32Array, normalized: boolean): void {
    if (a.bufferView !== undefined) {
      this.readView(a.bufferView, a.byteOffset ?? 0, a.componentType, n, a.count, out, normalized, 0);
    }
    if (a.sparse) {
      const s = a.sparse;
      const idx = new Uint32Array(s.count);
      this.readView(s.indices.bufferView, s.indices.byteOffset ?? 0, s.indices.componentType, 1, s.count, idx, false, 0);
      const values = out instanceof Float32Array ? new Float32Array(s.count * n) : new Uint32Array(s.count * n);
      this.readView(s.values.bufferView, s.values.byteOffset ?? 0, a.componentType, n, s.count, values, normalized, 0);
      for (let i = 0; i < s.count; i++) {
        for (let c = 0; c < n; c++) out[idx[i]! * n + c] = values[i * n + c]!;
      }
    }
  }

  private readView(
    viewIndex: number,
    byteOffset: number,
    componentType: number,
    n: number,
    count: number,
    out: Float32Array | Uint32Array,
    normalized: boolean,
    outOffset: number,
  ): void {
    const bv = this.json.bufferViews?.[viewIndex];
    if (!bv) throw new GltfError(`missing bufferView ${viewIndex}`);
    if (bv.buffer !== 0) throw new GltfError("only the GLB binary buffer is supported");
    const size = COMPONENT_BYTES[componentType];
    if (!size) throw new GltfError(`unknown componentType ${componentType}`);
    const stride = bv.byteStride ?? size * n;
    const base = (bv.byteOffset ?? 0) + byteOffset;
    if (base + stride * (count - 1) + size * n > this.bin.byteLength) throw new GltfError("accessor outside the binary chunk");
    const dv = new DataView(this.bin.buffer, this.bin.byteOffset, this.bin.byteLength);
    for (let i = 0; i < count; i++) {
      const at = base + i * stride;
      for (let c = 0; c < n; c++) {
        const p = at + c * size;
        let v: number;
        switch (componentType) {
          case 5126:
            v = dv.getFloat32(p, true);
            break;
          case 5121:
            v = dv.getUint8(p);
            if (normalized) v /= 255;
            break;
          case 5123:
            v = dv.getUint16(p, true);
            if (normalized) v /= 65535;
            break;
          case 5125:
            v = dv.getUint32(p, true);
            break;
          case 5120:
            v = dv.getInt8(p);
            if (normalized) v = Math.max(v / 127, -1);
            break;
          default: // 5122
            v = dv.getInt16(p, true);
            if (normalized) v = Math.max(v / 32767, -1);
            break;
        }
        out[outOffset + i * n + c] = v;
      }
    }
  }
}

// ------------------------------------------------------------------ writing (tests, tools)

export interface GlbBuilder {
  json: GltfJson;
  /** Appends `data` as a new bufferView + accessor; returns the accessor index. */
  add(data: Float32Array | Uint16Array | Uint32Array | Uint8Array, type: GltfAccessor["type"], opts?: { normalized?: boolean }): number;
  /** The finished .glb. */
  build(): ArrayBuffer;
}

/**
 * Builds .glb files in memory. Used by the unit tests to round-trip parts
 * through the reader; small enough to keep next to it.
 */
export function glbBuilder(json: Omit<GltfJson, "accessors" | "bufferViews" | "buffers">): GlbBuilder {
  const doc: GltfJson = { ...json, accessors: [], bufferViews: [], buffers: [] };
  const chunks: Uint8Array[] = [];
  let length = 0;
  return {
    json: doc,
    add(data, type, opts) {
      const componentType = data instanceof Float32Array ? 5126 : data instanceof Uint16Array ? 5123 : data instanceof Uint32Array ? 5125 : 5121;
      const bytes = new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
      const padded = new Uint8Array(Math.ceil(bytes.byteLength / 4) * 4);
      padded.set(bytes);
      doc.bufferViews!.push({ buffer: 0, byteOffset: length, byteLength: bytes.byteLength });
      chunks.push(padded);
      length += padded.byteLength;
      doc.accessors!.push({
        bufferView: doc.bufferViews!.length - 1,
        componentType,
        count: data.length / COMPONENTS[type],
        type,
        normalized: opts?.normalized,
      });
      return doc.accessors!.length - 1;
    },
    build() {
      doc.buffers = [{ byteLength: length }];
      const jsonBytes = new TextEncoder().encode(JSON.stringify(doc));
      const jsonLen = Math.ceil(jsonBytes.byteLength / 4) * 4;
      const total = 12 + 8 + jsonLen + 8 + length;
      const out = new Uint8Array(total);
      const dv = new DataView(out.buffer);
      dv.setUint32(0, GLB_MAGIC, true);
      dv.setUint32(4, 2, true);
      dv.setUint32(8, total, true);
      dv.setUint32(12, jsonLen, true);
      dv.setUint32(16, CHUNK_JSON, true);
      out.fill(0x20, 20, 20 + jsonLen);
      out.set(jsonBytes, 20);
      let o = 20 + jsonLen;
      dv.setUint32(o, length, true);
      dv.setUint32(o + 4, CHUNK_BIN, true);
      o += 8;
      for (const c of chunks) {
        out.set(c, o);
        o += c.byteLength;
      }
      return out.buffer;
    },
  };
}
