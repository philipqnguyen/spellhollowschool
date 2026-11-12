// The credits compiled in by client/scripts/credits.mjs (creditsPlugin), from
// data/credits.json, client/public/audio/CREDITS.md and the libraries the
// client ships. src/ui/credits.ts is its one user.
declare module "virtual:shs-credits" {
  export interface CreditEntry {
    work: string;
    author?: string;
    link?: string;
    licence: string;
    licenceUrl?: string;
    note?: string;
  }
  export interface CreditSection {
    id: string;
    title: string;
    intro?: string;
    entries: CreditEntry[];
  }
  export interface Credits {
    game: { name: string; repository: string; licences: { what: string; licence: string; licenceUrl: string }[] };
    sections: CreditSection[];
    /** The third-party notices file at the root of the built client. */
    notices: string;
  }
  const credits: Credits;
  export default credits;
}
