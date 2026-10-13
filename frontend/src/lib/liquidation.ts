/**
 * Liquidation launch-payload helper (vibe-quant-2gyk1.1.10, E1).
 *
 * The launch forms must send NO liquidation keys when the mode is off, so the
 * stored run parameters stay byte-identical to today's runs (contract E1/E6).
 * Backtests reuse the existing parameters.leverage; discovery sends its own
 * liquidation_leverage, and only when the mode is on.
 */
import type { BacktestLaunchRequestLiquidationMode } from "@/api/generated/models/backtestLaunchRequestLiquidationMode";

export type LiquidationMode = BacktestLaunchRequestLiquidationMode;

export const DEFAULT_LIQUIDATION_LEVERAGE = 10;
export const MAX_LIQUIDATION_LEVERAGE = 125;

export const LIQUIDATION_MODE_OPTIONS: ReadonlyArray<{ value: LiquidationMode; label: string }> = [
  { value: "off", label: "Off" },
  { value: "isolated", label: "Isolated" },
  { value: "cross", label: "Cross" },
];

export interface LiquidationPayload {
  liquidation_mode?: LiquidationMode;
  liquidation_leverage?: number;
}

/**
 * Fields to spread into a launch request. Off returns {} (no keys); on returns
 * the mode, plus liquidation_leverage when a leverage is supplied (discovery).
 */
export function liquidationPayload(mode: LiquidationMode, leverage?: number): LiquidationPayload {
  if (mode === "off") return {};
  return {
    liquidation_mode: mode,
    ...(leverage !== undefined && { liquidation_leverage: leverage }),
  };
}
