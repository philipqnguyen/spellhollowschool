// Types of asset-map.mjs for client/vite.config.ts (TypeScript).
import type { Plugin } from "vite";

export declare const ASSET_DIRS: string[];
export declare const HASH_HEX: number;
export declare const MAP_FILE: string;
export declare const MAP_VERSION: number;
export declare const SAME_ORIGIN_PREFIXES: string[];
export declare const VIRTUAL_ID: string;
export declare function hashFile(path: string): Promise<string>;
export declare function buildAssetMap(publicDir: string, dirs?: string[]): Promise<Record<string, string>>;
export declare function hashedKey(path: string, hash: string): string;
export declare function sameOriginOnly(path: string): boolean;
export declare function mapFileJson(map: Record<string, string>): string;
export declare function parseMapFile(text: string): Record<string, string>;
export declare function assetMapPlugin(opts?: { publicDir?: string }): Plugin;
