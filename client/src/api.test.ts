// Following a verification link signs in exactly as the sign-in form does:
// the session it answers with is stored and authenticates what comes next.

import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, clearSession, loadSession, sessionToken, verifyEmail } from "./api";

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

function answer(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => {
  clearSession();
  vi.unstubAllGlobals();
});

describe("verifyEmail", () => {
  it("posts the link's token and stores the session it answers with", async () => {
    vi.stubGlobal("localStorage", memoryStorage());
    const expiresAt = new Date(Date.now() + 30 * 86_400_000).toISOString();
    const fetch = vi.fn(async () => answer(200, { token: "s".repeat(43), expires_at: expiresAt }));
    vi.stubGlobal("fetch", fetch);

    const session = await verifyEmail("t".repeat(43));
    expect(session).toEqual({ token: "s".repeat(43), expiresAt });
    expect(fetch).toHaveBeenCalledWith("/api/auth/verify", expect.objectContaining({ method: "POST", body: JSON.stringify({ token: "t".repeat(43) }) }));
    expect(loadSession()).toEqual(session);
    expect(sessionToken()).toBe("s".repeat(43));
  });

  it("stores nothing when the link is spent or expired", async () => {
    vi.stubGlobal("localStorage", memoryStorage());
    vi.stubGlobal("fetch", vi.fn(async () => answer(400, { error: "invalid_token", message: "This link is invalid or has expired." })));

    const err = await verifyEmail("t".repeat(43)).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe("invalid_token");
    expect(loadSession()).toBeNull();
    expect(sessionToken()).toBeNull();
  });
});
