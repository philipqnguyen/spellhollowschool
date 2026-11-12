// The F3 performance overlay. The game fills in a `PerfStats` a few times a
// second while it is shown; nothing is measured while it is hidden.

import { h } from "./dom";

export interface PerfStats {
  fps: number;
  frameMs: number;
  drawCalls: number;
  activeMeshes: number;
  triangles: number;
  /** Used JS heap in bytes, where the browser exposes it (Chromium's performance.memory). */
  heapBytes: number | null;
  cellsLoaded: number;
  cellsLoading: number;
  cellsTotal: number;
  characters: number;
  rttMs: number | null;
  resolution: string;
  position: string;
  /** Further labelled lines from features (e.g. character assembly), shown after the rest. */
  extra?: readonly [label: string, value: string][];
}

export class PerfOverlay {
  readonly root: HTMLElement;
  private readonly body: HTMLPreElement;

  constructor() {
    this.body = h("pre", {});
    this.root = h("aside", { class: "perf panel", hidden: true, "aria-label": "Performance" }, this.body);
  }

  get visible(): boolean {
    return !this.root.hidden;
  }

  setVisible(on: boolean): void {
    this.root.hidden = !on;
  }

  show(s: PerfStats): void {
    const heap = s.heapBytes === null ? "n/a" : `${(s.heapBytes / 1048576).toFixed(0)} MB`;
    this.body.textContent = [
      `fps          ${s.fps.toFixed(0)}  (${s.frameMs.toFixed(1)} ms)`,
      `draw calls   ${s.drawCalls}`,
      `meshes       ${s.activeMeshes} active`,
      `triangles    ${s.triangles.toLocaleString()}`,
      `js heap      ${heap}`,
      `cells        ${s.cellsLoaded}/${s.cellsTotal} loaded${s.cellsLoading ? `, ${s.cellsLoading} loading` : ""}`,
      `characters   ${s.characters}`,
      `ping         ${s.rttMs === null ? "–" : `${s.rttMs.toFixed(0)} ms`}`,
      `resolution   ${s.resolution}`,
      `position     ${s.position}`,
      ...(s.extra ?? []).map(([label, value]) => `${label.padEnd(12)} ${value}`),
    ].join("\n");
  }
}

/** `performance.memory.usedJSHeapSize` where available (Chromium only). */
export function jsHeapBytes(): number | null {
  const mem = (performance as Performance & { memory?: { usedJSHeapSize: number } }).memory;
  return mem ? mem.usedJSHeapSize : null;
}
