import { describe, it, expect } from "vitest";
import { fmtMultiplier } from "./format";

describe("scenario format helpers", () => {
  it("rounds a multiplier to two decimals", () => {
    expect(fmtMultiplier(1.6903178886863979)).toBe("×1.69");
    expect(fmtMultiplier(1)).toBe("×1.00");
    expect(fmtMultiplier(0.8512)).toBe("×0.85");
  });

  it("shows a dash when the multiplier is missing", () => {
    expect(fmtMultiplier(null)).toBe("×—");
    expect(fmtMultiplier(undefined)).toBe("×—");
    expect(fmtMultiplier(Number.NaN)).toBe("×—");
  });
});
