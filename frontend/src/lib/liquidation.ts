/**
 * Liquidation / leverage launch-payload helpers (vibe-quant-6wq6w).
 *
 * One leverage value (whole number 1-125, default 10) is honoured in every
 * mode. A default launch must send NO liquidation or leverage keys so the
 * stored run parameters stay byte-identical to today's runs (contract C6):
 * the mode is sent only when on, and leverage only when the mode is on or the
 * value is not the default.
 */
import type { BacktestLaunchRequestLiquidationMode } from "@/api/generated/models/backtestLaunchRequestLiquidationMode";

export type LiquidationMode = BacktestLaunchRequestLiquidationMode;

export const DEFAULT_LEVERAGE = 10;
export const MIN_LEVERAGE = 1;
export const MAX_LEVERAGE = 125;

export const LIQUIDATION_MODE_OPTIONS: ReadonlyArray<{ value: LiquidationMode; label: string }> = [
  { value: "off", label: "Off" },
  { value: "isolated", label: "Isolated" },
  { value: "cross", label: "Cross" },
];

export interface LiquidationPayload {
  liquidation_mode?: LiquidationMode;
}

export interface LeveragePayload {
  leverage?: number;
}

/** Mode fields to spread into a launch request: {} when off, else the mode. */
export function liquidationPayload(mode: LiquidationMode): LiquidationPayload {
  if (mode === "off") return {};
  return { liquidation_mode: mode };
}

/**
 * Leverage fields to spread into a discovery launch request: {} for a mode-off
 * run at the default, else the value (the screening venue runs at it in every
 * mode, vibe-quant-6wq6w).
 */
export function leveragePayload(mode: LiquidationMode, leverage: number): LeveragePayload {
  if (mode === "off" && leverage === DEFAULT_LEVERAGE) return {};
  return { leverage };
}
