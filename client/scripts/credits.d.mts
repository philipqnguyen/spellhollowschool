// Types of credits.mjs for client/vite.config.ts (TypeScript).
import type { Plugin } from "vite";

export declare const CREDITS_SOURCE: string;
export declare const ASSET_CREDIT_FILES: string[];
export declare const NOTICES_FILE: string;
export declare const VIRTUAL_ID: string;
export declare function creditsPlugin(opts?: { repo?: string }): Plugin;
