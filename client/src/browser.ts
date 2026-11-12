// The game supports Google Chrome only. Other browsers draw parts of it wrong
// (Safari, and every iOS browser since they all run on WebKit, ignores
// createImageBitmap's premultiplyAlpha: "none", which blacks out the face
// textures' eyes), so main.ts shows them a card asking for Chrome instead of
// the game.
//
// Testers and automation (Playwright's Chromium is not Google Chrome) skip the
// check with `?browser=any`, remembered in this browser until `?browser=chrome`.
// The Vite dev server never checks.

/** The `navigator.userAgentData.brands` entries (Chromium browsers only). */
export interface Brand {
  brand: string;
}

/** Other Chromium browsers name themselves in the user agent next to "Chrome/". */
const NOT_CHROME = /Edg\/|EdgA\/|EdgiOS\/|OPR\/|Opera|SamsungBrowser\/|YaBrowser\/|Vivaldi\/|Brave\/|Electron\/|HeadlessChrome\//;
/** Every browser on iPhone and iPad is WebKit underneath, Chrome included (CriOS). */
const IOS = /iPhone|iPad|iPod|CriOS\//;

/**
 * Whether this is Google Chrome. With client hints (`brands`) Chrome is the
 * browser that lists the "Google Chrome" brand: Edge, Opera, Brave and plain
 * Chromium list their own. Without them, the user agent decides. Pure.
 */
export function isGoogleChrome(userAgent: string, brands: readonly Brand[] | null | undefined): boolean {
  if (IOS.test(userAgent)) return false;
  if (brands && brands.length > 0) return brands.some((b) => b.brand === "Google Chrome");
  return /Chrome\/\d/.test(userAgent) && !NOT_CHROME.test(userAgent);
}

const OVERRIDE_KEY = "shs.anyBrowser";

/**
 * Whether to let this browser in: Google Chrome, the dev server, or a
 * `?browser=any` override (kept in storage; `?browser=chrome` clears it).
 */
export function browserAllowed(): boolean {
  if (import.meta.env.DEV) return true;
  const param = new URLSearchParams(location.search).get("browser");
  try {
    if (param === "any") localStorage.setItem(OVERRIDE_KEY, "1");
    else if (param === "chrome") localStorage.removeItem(OVERRIDE_KEY);
    if (localStorage.getItem(OVERRIDE_KEY) === "1") return true;
  } catch {
    if (param === "any") return true;
  }
  const nav = navigator as Navigator & { userAgentData?: { brands?: Brand[] } };
  return isGoogleChrome(navigator.userAgent, nav.userAgentData?.brands);
}

/** Where to get Chrome. */
export const CHROME_DOWNLOAD_URL = "https://www.google.com/chrome/";

/** How this device can get the player into Chrome. */
export type ChromeRoute =
  /** Android: an intent link opens this page in Chrome (or the Play Store page for it). */
  | { kind: "android"; intent: string }
  /** iPhone and iPad: every browser there is WebKit, so no way in; a computer or Android is needed. */
  | { kind: "ios" }
  /** Desktop: a web page cannot start another browser; copy the address, open it in Chrome. */
  | { kind: "desktop" };

/** The route for a user agent and this page's address. Pure. */
export function chromeRoute(userAgent: string, href: string): ChromeRoute {
  if (IOS.test(userAgent) || (/Macintosh/.test(userAgent) && /Mobile\//.test(userAgent))) return { kind: "ios" };
  if (/Android/.test(userAgent)) {
    const url = new URL(href);
    const fallback = encodeURIComponent(CHROME_PLAY_STORE_URL);
    const intent = `intent://${url.host}${url.pathname}${url.search}${url.hash}#Intent;scheme=${url.protocol.replace(":", "")};package=com.android.chrome;S.browser_fallback_url=${fallback};end`;
    return { kind: "android", intent };
  }
  return { kind: "desktop" };
}

const CHROME_PLAY_STORE_URL = "https://play.google.com/store/apps/details?id=com.android.chrome";
