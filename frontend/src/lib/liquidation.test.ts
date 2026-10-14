/**
 * Tests for the liquidation / leverage launch-payload helpers (vibe-quant-6wq6w).
 *
 * One leverage value (whole number 1-125, default 10) is honoured in every
 * mode. The liquidation helper sends NO keys when the mode is off (stored
 * parameters stay identical to today's runs, contract C6); the leverage helper
 * sends `leverage` only when the mode is on or the value is not the default, so
 * a default discovery launch sends exactly today's body.
 */
import { describe, expect, it } from "vitest";
import { DEFAULT_LEVERAGE, leveragePayload, liquidationPayload } from "@/lib/liquidation";

describe("liquidationPayload", () => {
  it("off sends no liquidation fields", () => {
    expect(liquidationPayload("off")).toStrictEqual({});
  });

  it("isolated sends the mode and no leverage", () => {
    expect(liquidationPayload("isolated")).toStrictEqual({ liquidation_mode: "isolated" });
  });

  it("cross sends the mode", () => {
    expect(liquidationPayload("cross")).toStrictEqual({ liquidation_mode: "cross" });
  });
});

describe("leveragePayload", () => {
  it("mode off at the default sends nothing", () => {
    expect(leveragePayload("off", 10)).toStrictEqual({});
  });

  it("mode off at a non-default sends leverage", () => {
    expect(leveragePayload("off", 3)).toStrictEqual({ leverage: 3 });
  });

  it("mode on sends leverage", () => {
    expect(leveragePayload("isolated", 20)).toStrictEqual({ leverage: 20 });
  });

  it("defaults leverage to 10", () => {
    expect(DEFAULT_LEVERAGE).toBe(10);
  });
});
