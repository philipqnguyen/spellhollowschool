import { describe, expect, it } from "vitest";
import type { KickCode } from "../wasm/sim_wasm";
import { isRetryableKick } from "../game/net";
import { CHAT_DEFAULT_SIZE, CHAT_MIN_SIZE, clampChatSize, dragChatSize, kickNotice, quoteName, rejectionText } from "./chat";

describe("the chat's size", () => {
  const big = { width: 1600, height: 1000 };
  it("grows dragging up and to the right, shrinks dragging back", () => {
    expect(dragChatSize(CHAT_DEFAULT_SIZE, 100, -150, big)).toEqual({ width: 540, height: 340 });
    expect(dragChatSize({ width: 540, height: 340 }, -100, 150, big)).toEqual(CHAT_DEFAULT_SIZE);
  });
  it("stays between its minimum and what the window has room for", () => {
    expect(dragChatSize(CHAT_DEFAULT_SIZE, -1000, 1000, big)).toEqual(CHAT_MIN_SIZE);
    const huge = dragChatSize(CHAT_DEFAULT_SIZE, 5000, -5000, big);
    expect(huge.width).toBeLessThanOrEqual(big.width - 24);
    expect(huge.height).toBeLessThanOrEqual(big.height - 200);
    expect(clampChatSize({ width: Number.NaN, height: 200 }, big)).toEqual({ width: CHAT_MIN_SIZE.width, height: 200 });
  });
});

describe("rejectionText", () => {
  it("explains each refusal", () => {
    expect(rejectionText("rate_limited")).toMatch(/too quickly/);
    expect(rejectionText("filtered")).toMatch(/does not allow/);
    expect(rejectionText("invalid")).toMatch(/empty or too long/);
    expect(rejectionText("no_channel")).toMatch(/not in a party/);
    expect(rejectionText("no_channel", { channel: "club" })).toBe("Not sent: you're not in a club.");
  });

  it("names the whisper target that is not there", () => {
    expect(rejectionText("unknown_target", { channel: "whisper", to: "Basil" })).toBe("Not sent: Basil is not online.");
    expect(rejectionText("unknown_target")).toMatch(/nobody by that name/);
  });
});

describe("kickNotice", () => {
  const codes: KickCode[] = ["server_restart", "timeout", "too_slow", "server_full", "replaced", "banned", "protocol_error", "version_mismatch", "session_expired", "no_character", "flood"];

  it("has a heading and body for every code, keyed by code rather than the reason text", () => {
    for (const code of codes) {
      const n = kickNotice(code, "");
      expect(n.heading.length).toBeGreaterThan(0);
      expect(n.body.length).toBeGreaterThan(0);
    }
    expect(kickNotice("replaced", "whatever the server said").heading).toBe("Signed in elsewhere");
    expect(kickNotice("banned", "Suspended until Friday.").body).toBe("Suspended until Friday.");
  });

  it("retries only the temporary codes", () => {
    expect(codes.filter(isRetryableKick)).toEqual(["server_restart", "timeout", "too_slow", "server_full"]);
  });
});

describe("quoteName", () => {
  it("quotes names with spaces for /w", () => {
    expect(quoteName("Basil")).toBe("Basil");
    expect(quoteName("Anne Marie")).toBe('"Anne Marie"');
  });
});
