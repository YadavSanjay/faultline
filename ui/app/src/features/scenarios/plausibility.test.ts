import { describe, it, expect } from "vitest";
import { plausibilityCounts, plausibilityLabel } from "./format";

describe("plausibility display", () => {
  it("labels only a hard-bound fail as not plausible", () => {
    expect(plausibilityLabel("pass")).toBe("plausible");
    expect(plausibilityLabel("flag")).toBe("plausible · notes");
    expect(plausibilityLabel("fail")).toBe("not plausible");
    expect(plausibilityLabel(null)).toBe("unchecked");
    expect(plausibilityLabel(undefined)).toBe("unchecked");
  });

  it("counts flagged tiers as plausible and unchecked tiers toward the total only", () => {
    expect(plausibilityCounts(["pass", "flag", "flag", "fail", null])).toEqual({
      plausible: 3, withNotes: 2, failed: 1, total: 5,
    });
    expect(plausibilityCounts([])).toEqual({ plausible: 0, withNotes: 0, failed: 0, total: 0 });
  });
});
