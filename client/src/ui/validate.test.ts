import { describe, expect, it } from "vitest";
import { houseInfo, houseWireName, HOUSES } from "../content/houses";
import { backoffDelay, isRetryableKick } from "../game/net";
import { nameProblem, passwordProblem } from "./validate";

describe("passwordProblem", () => {
  it("enforces length and not-the-email", () => {
    expect(passwordProblem("short", "a@b.c")).not.toBeNull();
    expect(passwordProblem("correct horse battery", "a@b.c")).toBeNull();
    expect(passwordProblem("Someone@Example.org", "someone@example.org")).not.toBeNull();
  });
});

describe("nameProblem", () => {
  it("allows Latin letters with single separators", () => {
    for (const ok of ["Mary-Kate", "O'Neil", "Anne Marie", "Émile"]) expect(nameProblem(ok)).toBeNull();
    for (const bad of ["Al", "Anne  Marie", "-Anne", "Bot42", "Ανna", "A".repeat(25)]) expect(nameProblem(bad)).not.toBeNull();
  });
});

describe("houses", () => {
  it("looks houses up by either spelling", () => {
    const first = HOUSES[0]!;
    expect(houseInfo(first.id.toUpperCase()).name).toBe(first.name);
    expect(houseWireName(first.id)).toBe(first.id[0]!.toUpperCase() + first.id.slice(1));
    expect(houseInfo("nonexistent").colour).toMatch(/^#/);
  });
});

describe("backoffDelay", () => {
  it("doubles from 0.5 s up to 15 s, with jitter in [50%, 100%]", () => {
    expect(backoffDelay(0, () => 1)).toBe(500);
    expect(backoffDelay(1, () => 1)).toBe(1000);
    expect(backoffDelay(3, () => 0)).toBe(2000);
    expect(backoffDelay(20, () => 1)).toBe(15_000);
  });
});

describe("isRetryableKick", () => {
  it("retries temporary kicks only", () => {
    for (const code of ["server_restart", "timeout", "too_slow", "server_full"] as const) {
      expect(isRetryableKick(code)).toBe(true);
    }
    for (const code of ["replaced", "session_expired", "version_mismatch", "banned", "protocol_error", "no_character", "flood"] as const) {
      expect(isRetryableKick(code)).toBe(false);
    }
  });
});
