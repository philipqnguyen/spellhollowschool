import { describe, expect, it } from "vitest";
import { chromeRoute, isGoogleChrome } from "./browser";

const UA = {
  chromeMac:
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
  chromeWin: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
  chromeAndroid:
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36",
  safariMac:
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/19.0 Safari/605.1.15",
  safariIphone:
    "Mozilla/5.0 (iPhone; CPU iPhone OS 19_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/19.0 Mobile/15E148 Safari/604.1",
  chromeIphone:
    "Mozilla/5.0 (iPhone; CPU iPhone OS 19_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/141.0.0.0 Mobile/15E148 Safari/604.1",
  firefox: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:144.0) Gecko/20100101 Firefox/144.0",
  edge: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0",
  opera: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 OPR/125.0.0.0",
  samsung:
    "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/28.0 Chrome/130.0.0.0 Mobile Safari/537.36",
  headless:
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/141.0.0.0 Safari/537.36",
};

const brands = (...names: string[]) => names.map((brand) => ({ brand }));

describe("isGoogleChrome", () => {
  it("accepts Google Chrome on desktop and Android", () => {
    expect(isGoogleChrome(UA.chromeMac, brands("Google Chrome", "Chromium", "Not?A_Brand"))).toBe(true);
    expect(isGoogleChrome(UA.chromeWin, null)).toBe(true);
    expect(isGoogleChrome(UA.chromeAndroid, brands("Chromium", "Google Chrome", "Not?A_Brand"))).toBe(true);
  });

  it("refuses Safari, Firefox and every iOS browser", () => {
    expect(isGoogleChrome(UA.safariMac, undefined)).toBe(false);
    expect(isGoogleChrome(UA.safariIphone, undefined)).toBe(false);
    expect(isGoogleChrome(UA.chromeIphone, undefined)).toBe(false);
    expect(isGoogleChrome(UA.firefox, undefined)).toBe(false);
  });

  it("refuses other Chromium browsers by their brands or user agent", () => {
    expect(isGoogleChrome(UA.edge, brands("Microsoft Edge", "Chromium", "Not?A_Brand"))).toBe(false);
    expect(isGoogleChrome(UA.edge, null)).toBe(false);
    expect(isGoogleChrome(UA.opera, null)).toBe(false);
    expect(isGoogleChrome(UA.samsung, null)).toBe(false);
    // Brave sends Chrome's user agent; its brands give it away.
    expect(isGoogleChrome(UA.chromeMac, brands("Brave", "Chromium", "Not?A_Brand"))).toBe(false);
    // Plain Chromium (Playwright's) lists no Google Chrome brand.
    expect(isGoogleChrome(UA.chromeMac, brands("Chromium", "Not?A_Brand"))).toBe(false);
    expect(isGoogleChrome(UA.headless, null)).toBe(false);
  });
});

describe("chromeRoute", () => {
  it("opens the same address in Chrome on Android, with the Play Store as fallback", () => {
    const r = chromeRoute(UA.samsung, "https://game.example.org/?verify=abc");
    expect(r.kind).toBe("android");
    if (r.kind !== "android") return;
    expect(r.intent).toBe(
      "intent://game.example.org/?verify=abc#Intent;scheme=https;package=com.android.chrome;" +
        "S.browser_fallback_url=https%3A%2F%2Fplay.google.com%2Fstore%2Fapps%2Fdetails%3Fid%3Dcom.android.chrome;end",
    );
  });

  it("knows iOS has no way in and desktops must copy the address", () => {
    expect(chromeRoute(UA.safariIphone, "https://game.example.org/").kind).toBe("ios");
    expect(chromeRoute(UA.chromeIphone, "https://game.example.org/").kind).toBe("ios");
    expect(chromeRoute(UA.safariMac, "https://game.example.org/").kind).toBe("desktop");
    expect(chromeRoute(UA.firefox, "https://game.example.org/").kind).toBe("desktop");
  });
});
