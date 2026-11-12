import { describe, expect, it } from "vitest";
import type { DormErrorCode } from "../../wasm/sim_wasm";
import { dormErrorText, isDecorError } from "./messages";

const CODES: DormErrorCode[] = [
  "not_owner",
  "not_in_dorm",
  "item_not_found",
  "not_furniture",
  "item_unavailable",
  "out_of_bounds",
  "cap_reached",
  "not_friend",
  "not_online",
  "closed",
  "invite_expired",
  "too_far",
  "wrong_house",
  "unknown_target",
  "rate_limited",
  "failed",
  "full",
  "mounted",
  "not_empty",
  "blocks_way",
];

describe("dormErrorText", () => {
  it("has a sentence for every code", () => {
    for (const code of CODES) expect(dormErrorText(code)).toMatch(/\.$/);
  });

  it("names the friend involved when known", () => {
    expect(dormErrorText("closed", "Basil")).toBe("Basil's room is not open to friends.");
    expect(dormErrorText("not_online", "Basil")).toBe("Basil is not online.");
    expect(dormErrorText("full", "Basil")).toBe("Basil's room is full right now.");
  });

  it("tells decorating errors from visits and doors", () => {
    expect(isDecorError("out_of_bounds")).toBe(true);
    expect(isDecorError("cap_reached")).toBe(true);
    expect(isDecorError("not_empty")).toBe(true);
    expect(isDecorError("blocks_way")).toBe(true);
    expect(isDecorError("closed")).toBe(false);
    expect(isDecorError("too_far")).toBe(false);
  });
});
