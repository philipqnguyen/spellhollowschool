// Test helper (the castle tests): a tiny .glb as a data: URL, one quad per
// node, with the node and material names an export writes, and optional
// TEXCOORD_0, TEXCOORD_1 and COLOR_0. Only tests import it.

export interface FixtureNode {
  name: string;
  material: string;
  /** Adds TEXCOORD_0 and TEXCOORD_1 (the terrain's turf and its blend mask). */
  uv2?: boolean;
  /** Adds COLOR_0 (r = g = b = this value). */
  colour?: number;
  /** Where the quad stands (x offset, m). */
  x?: number;
}

export function gltfFixtureUrl(nodes: readonly FixtureNode[]): string {
  const materials: object[] = [];
  const matIndex = new Map<string, number>();
  const bytes: number[] = [];
  const views: object[] = [];
  const accessors: object[] = [];
  const meshes: object[] = [];
  const push = (arr: Float32Array | Uint16Array) => {
    const offset = bytes.length;
    bytes.push(...new Uint8Array(arr.buffer));
    while (bytes.length % 4) bytes.push(0);
    views.push({ buffer: 0, byteOffset: offset, byteLength: arr.byteLength });
    return views.length - 1;
  };
  const vec = (data: number[], type: string, count: number, extra: object = {}) => {
    accessors.push({ bufferView: push(new Float32Array(data)), componentType: 5126, count, type, ...extra });
    return accessors.length - 1;
  };
  nodes.forEach((n, i) => {
    const x = n.x ?? 0;
    const z = -2 - i * 0.1;
    const attributes: Record<string, number> = {
      POSITION: vec([x - 4, 0, z, x + 4, 0, z, x + 4, 8, z, x - 4, 8, z], "VEC3", 4, { min: [x - 4, 0, z], max: [x + 4, 8, z] }),
      NORMAL: vec([0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1], "VEC3", 4),
    };
    if (n.uv2) {
      attributes.TEXCOORD_0 = vec([0, 0, 1, 0, 1, 1, 0, 1], "VEC2", 4);
      attributes.TEXCOORD_1 = vec([1, 0, 0, 1, 0, 0, 0.5, 0.5], "VEC2", 4);
    }
    if (n.colour !== undefined) {
      const c = n.colour;
      attributes.COLOR_0 = vec([c, c, c, 1, c, c, c, 1, c, c, c, 1, c, c, c, 1], "VEC4", 4);
    }
    accessors.push({ bufferView: push(new Uint16Array([0, 1, 2, 0, 2, 3])), componentType: 5123, count: 6, type: "SCALAR" });
    if (!matIndex.has(n.material)) {
      materials.push({ name: n.material, pbrMetallicRoughness: { baseColorFactor: [0.5, 0.45, 0.4, 1] } });
      matIndex.set(n.material, materials.length - 1);
    }
    meshes.push({ name: n.name, primitives: [{ attributes, indices: accessors.length - 1, material: matIndex.get(n.material) }] });
  });
  const gltf = {
    asset: { version: "2.0" },
    scene: 0,
    scenes: [{ nodes: nodes.map((_, i) => i) }],
    nodes: nodes.map((n, i) => ({ name: n.name, mesh: i })),
    meshes,
    materials,
    accessors,
    bufferViews: views,
    buffers: [{ byteLength: bytes.length }],
  };
  const json = Buffer.from(JSON.stringify(gltf));
  const jsonPad = Buffer.concat([json, Buffer.alloc((4 - (json.length % 4)) % 4, 0x20)]);
  const bin = Buffer.from(new Uint8Array(bytes));
  const chunk = (type: number, data: Buffer) => {
    const h = Buffer.alloc(8);
    h.writeUInt32LE(data.length, 0);
    h.writeUInt32LE(type, 4);
    return Buffer.concat([h, data]);
  };
  const header = Buffer.alloc(12);
  header.writeUInt32LE(0x46546c67, 0);
  header.writeUInt32LE(2, 4);
  header.writeUInt32LE(12 + 8 + jsonPad.length + 8 + bin.length, 8);
  return `data:model/gltf-binary;base64,${Buffer.concat([header, chunk(0x4e4f534a, jsonPad), chunk(0x004e4942, bin)]).toString("base64")}`;
}
