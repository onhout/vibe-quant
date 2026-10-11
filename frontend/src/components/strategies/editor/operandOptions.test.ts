/**
 * Pins the operand strings the Conditions tab offers for configured indicators.
 *
 * tests/unit/test_strategy_editor_operands.py reads the snapshot and feeds every operand to the
 * backend translator, so a change to the operand format (or to what the catalog hands the editor)
 * must still resolve. Regenerate: `pnpm vitest run -u src/components/strategies/editor`.
 */

import { describe, expect, it } from "vitest";
import { buildOperandOptions, type DslIndicator } from "./types";

// Editor-shaped indicators: `type` + the catalog `default_params` the Indicators tab inserts
// (registry spellings, ints, floats that serialise as integers, fractional floats).
const INDICATORS: DslIndicator[] = [
  { type: "SMA", params: { period: 20 } },
  { type: "BB", params: { period: 20, std_dev: 2 } },
  { type: "BBANDS", params: { period: 20, std_dev: 2.5 } },
  { type: "MACD", params: { fast_period: 8, slow_period: 30, signal_period: 5 } },
  { type: "MACD", params: { fast: 12, slow: 26, signal: 9 } },
  { type: "STOCH", params: { period_k: 10, period_d: 4 } },
  { type: "STOCH", params: { k_period: 14, d_period: 3 } },
  { type: "SQUEEZE_RATIO", params: { length: 20, bb_mult: 2, kc_mult: 1.5 } },
  { type: "VWAP", params: {} },
  // JavaScript prints small numbers as 0.000001 but 1e-7 (Python: 1e-06 / 1e-07)
  { type: "ADAPTIVE_RSI", params: { period: 14, alpha: 0.000001 } },
  { type: "ADAPTIVE_RSI", params: { period: 14, alpha: 0.0000001 } },
];

describe("buildOperandOptions", () => {
  it("emits TYPE(v1,v2,...) in param order for every configured indicator", async () => {
    const operands = buildOperandOptions(INDICATORS);
    expect(operands.slice(0, 6)).toEqual(["price", "close", "open", "high", "low", "volume"]);
    expect(operands.slice(6)).toEqual([
      "SMA(20)",
      "BB(20,2)",
      "BBANDS(20,2.5)",
      "MACD(8,30,5)",
      "MACD(12,26,9)",
      "STOCH(10,4)",
      "STOCH(14,3)",
      "SQUEEZE_RATIO(20,2,1.5)",
      "VWAP",
      "ADAPTIVE_RSI(14,0.000001)",
      "ADAPTIVE_RSI(14,1e-7)",
    ]);
    const snapshot = INDICATORS.map((indicator, i) => ({
      indicator,
      operand: operands[6 + i],
    }));
    await expect(`${JSON.stringify(snapshot, null, 2)}\n`).toMatchFileSnapshot(
      "./__fixtures__/operand_options.json",
    );
  });
});
