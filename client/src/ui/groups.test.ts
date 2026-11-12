import { describe, expect, it } from "vitest";
import { GROUP_PRECEDENCE, plateGroup, type GroupMembership } from "./groups";

const of = <K extends string>(kind: K, ids: number[]): GroupMembership<K> => ({ kind, has: (id) => ids.includes(id) });

describe("nameplate groups", () => {
  it("tints party members and nobody else", () => {
    const groups = [of("party", [4, 7])];
    expect(plateGroup(4, groups)).toBe("party");
    expect(plateGroup(5, groups)).toBeNull();
    expect(plateGroup(4, [])).toBeNull();
  });

  it("puts the party first", () => {
    expect(GROUP_PRECEDENCE[0]).toBe("party");
  });

  it("shows party blue over club green for someone in both", () => {
    expect(GROUP_PRECEDENCE).toEqual(["party", "club"]);
    const groups = [of<"party" | "club">("club", [1, 2]), of<"party" | "club">("party", [2])];
    expect(plateGroup(2, groups)).toBe("party");
    expect(plateGroup(1, groups)).toBe("club");
    expect(plateGroup(3, groups)).toBeNull();
  });

  it("lets the earlier group win whatever order the groups come in", () => {
    // A second kind of group (as a club would be): the party still wins for someone in both.
    const order = ["party", "club"] as const;
    const groups = [of<"party" | "club">("club", [1, 2]), of<"party" | "club">("party", [2, 3])];
    expect(plateGroup(2, groups, order)).toBe("party");
    expect(plateGroup(1, groups, order)).toBe("club");
    expect(plateGroup(3, groups, order)).toBe("party");
    expect(plateGroup(9, groups, order)).toBeNull();
  });
});
