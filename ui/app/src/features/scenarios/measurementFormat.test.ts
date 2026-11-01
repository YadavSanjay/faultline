import { describe, it, expect } from "vitest";
import {
  diversityZone,
  fmtCosine,
  fmtGain,
  fmtShare,
  fmtSizeRatio,
  gainTone,
  missingLabel,
  sizeRatioTone,
  targetPosition,
} from "./measurementFormat";

describe("number formats", () => {
  it("formats shares, cosines, gains and size ratios", () => {
    expect(fmtShare(0.125)).toBe("12.5%");
    expect(fmtShare(null)).toBe("—");
    expect(fmtCosine(0.75)).toBe("0.750");
    expect(fmtCosine(undefined)).toBe("—");
    expect(fmtGain(0.25)).toBe("+0.250");
    expect(fmtGain(-0.125)).toBe("−0.125");
    expect(fmtGain(0)).toBe("0.000");
    expect(fmtGain(null)).toBe("—");
    expect(fmtSizeRatio(0.9639)).toBe("×0.964");
    expect(fmtSizeRatio(Number.NaN)).toBe("—");
  });

  it("tones gains and size ratios", () => {
    expect(gainTone(0.1)).toBe("text-success-fg");
    expect(gainTone(-0.1)).toBe("text-danger-fg");
    expect(gainTone(null)).toBe("text-fg-secondary");
    expect(sizeRatioTone(0.9)).toBe("text-warning-fg");
    expect(sizeRatioTone(1.1)).toBe("text-info-fg");
    expect(sizeRatioTone(1.0001)).toBe("text-fg-muted");
  });
});

describe("targetPosition", () => {
  it("places a value inside the band", () => {
    const p = targetPosition({ participation_ratio: 3.5, target: [3, 4], in_target: true });
    expect(p.side).toBe("in");
    // scale = max(4, 3.5) * 1.25 = 5
    expect(p.valuePct).toBeCloseTo(70, 10);
    expect(p.bandPct).toEqual({ from: 60, to: 80 });
    expect(diversityZone(p.side)).toBe("green");
  });

  it("flags a value above or below the band", () => {
    const above = targetPosition({ participation_ratio: 5, target: [3, 4], in_target: false });
    expect(above.side).toBe("above");
    // scale = 5 * 1.25 = 6.25 -> 5 / 6.25 = 80%
    expect(above.valuePct).toBeCloseTo(80, 10);
    expect(diversityZone(above.side)).toBe("amber");
    expect(targetPosition({ participation_ratio: 2, target: [3, 4], in_target: false }).side).toBe("below");
  });

  it("is unknown without a value or a two-sided target", () => {
    expect(targetPosition({ participation_ratio: null, target: [3, 4], in_target: null }).side).toBe("unknown");
    const noTarget = targetPosition({ participation_ratio: 3, target: null, in_target: null });
    expect(noTarget).toMatchObject({ side: "unknown", valuePct: null, bandPct: null });
    expect(diversityZone("unknown")).toBe("unknown");
  });
});

describe("missingLabel", () => {
  it("accepts names and records", () => {
    expect(missingLabel("gulf")).toBe("gulf");
    expect(missingLabel({ scenario: "ai_capex_credit", reason: "stopped: macro_unavailable" }))
      .toBe("ai_capex_credit — stopped: macro_unavailable");
    expect(missingLabel({ scenario: "x" })).toBe("x");
  });
});
