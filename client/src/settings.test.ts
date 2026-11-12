// Settings > Portrait chatter and Settings > Chats are remembered per viewer (localStorage, every
// access guarded: a private window or blocked storage still plays).

import { afterEach, describe, expect, it, vi } from "vitest";
import { loadSettings, saveSettings } from "./settings";

function memoryStorage(): Storage {
  const m = new Map<string, string>();
  return {
    get length() {
      return m.size;
    },
    clear: () => m.clear(),
    getItem: (k) => m.get(k) ?? null,
    key: (i) => [...m.keys()][i] ?? null,
    removeItem: (k) => void m.delete(k),
    setItem: (k, v) => void m.set(k, String(v)),
  };
}

describe("Portrait chatter", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("starts On, and keeps Quiet or Off once chosen", () => {
    vi.stubGlobal("localStorage", memoryStorage());
    const s = loadSettings();
    expect(s.chatter).toBe("on");
    s.chatter = "quiet";
    saveSettings(s);
    expect(loadSettings().chatter).toBe("quiet");
    s.chatter = "off";
    saveSettings(s);
    expect(loadSettings().chatter).toBe("off");
  });

  it("reads rubbish as On and survives storage that throws", () => {
    const store = memoryStorage();
    store.setItem("shs.settings", JSON.stringify({ chatter: "shouting" }));
    vi.stubGlobal("localStorage", store);
    expect(loadSettings().chatter).toBe("on");
    vi.stubGlobal("localStorage", {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    });
    expect(loadSettings().chatter).toBe("on");
    expect(() => saveSettings({ ...loadSettings(), chatter: "off" })).not.toThrow();
  });
});

describe("Settings > Chats", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows every channel in All at first, and remembers one taken out", () => {
    vi.stubGlobal("localStorage", memoryStorage());
    const s = loadSettings();
    expect(s.chatAll).toEqual({ house: true, party: true, club: true, whisper: true });
    s.chatAll.party = false;
    saveSettings(s);
    expect(loadSettings().chatAll).toEqual({ house: true, party: false, club: true, whisper: true });
  });

  it("keeps say, NPC and system lines whatever is stored", () => {
    const store = memoryStorage();
    store.setItem("shs.settings", JSON.stringify({ chatAll: { say: false, npc: false, system: false, club: "no", whisper: false } }));
    vi.stubGlobal("localStorage", store);
    expect(loadSettings().chatAll).toEqual({ house: true, party: true, club: true, whisper: false });
  });
});
