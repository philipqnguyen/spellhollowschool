// The asset map compiled in by client/scripts/asset-map.mjs (assetMapPlugin):
// logical path (`assets/world/manifest.json`) to content hash. Empty in
// development and tests. src/assets.ts is its one user.
declare module "virtual:shs-asset-map" {
  const map: Readonly<Record<string, string>>;
  export default map;
}
