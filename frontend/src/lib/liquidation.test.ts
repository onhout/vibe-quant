/**
 * Tests for the liquidation launch payload helper (vibe-quant-2gyk1.1.10, E1).
 *
 * Off must send NO liquidation keys so stored parameters stay identical to
 * today's runs (contract E1/E6). The backend reads the mode top-level and the
 * backtest leverage from parameters.leverage; discovery sends its own
 * liquidation_leverage only when the mode is on.
 */
import { describe, expect, it } from "vitest";
import { DEFAULT_LIQUIDATION_LEVERAGE, liquidationPayload } from "@/lib/liquidation";

describe("liquidationPayload", () => {
  it("off sends no liquidation fields", () => {
    expect(liquidationPayload("off")).toStrictEqual({});
    expect(liquidationPayload("off", 20)).toStrictEqual({});
  });

  it("isolated sends the mode and no leverage for backtests", () => {
    expect(liquidationPayload("isolated")).toStrictEqual({ liquidation_mode: "isolated" });
  });

  it("cross sends the mode", () => {
    expect(liquidationPayload("cross")).toStrictEqual({ liquidation_mode: "cross" });
  });

  it("discovery sends liquidation_leverage only when on", () => {
    expect(liquidationPayload("isolated", 20)).toStrictEqual({
      liquidation_mode: "isolated",
      liquidation_leverage: 20,
    });
    expect(liquidationPayload("cross", 125)).toStrictEqual({
      liquidation_mode: "cross",
      liquidation_leverage: 125,
    });
  });

  it("defaults discovery leverage to 10", () => {
    expect(DEFAULT_LIQUIDATION_LEVERAGE).toBe(10);
  });
});
