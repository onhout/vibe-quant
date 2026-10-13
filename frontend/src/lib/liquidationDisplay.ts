/**
 * Display helpers for the liquidation surface (bead vibe-quant-2gyk1.1.10, E2):
 * the results-page chip (D3), the sweep table column (D4) and the discovery
 * row badge (R1). Kept pure so every stored shape — off, absent, malformed —
 * has a named test (design-t5.md, contract rows D3/D4/R1).
 *
 * Backend shapes:
 * - ``parameters.liquidation_mode`` is "isolated" | "cross" when on and absent
 *   (or "off") otherwise; ``parameters.leverage`` is the chosen leverage
 *   (default 10.0), shared with the venue (V1).
 * - ``notes`` is the machine-JSON string on a result row. ``notes.liquidation``
 *   is ``{mode, leverage}`` for discovery and the overlay summary — including
 *   ``count`` — for validation/screening.
 */
import { parseResultNotes } from "@/lib/metrics";

/** Matches `LiquidationSettings.DEFAULT_LEVERAGE` (#: the params default). */
export const DEFAULT_LIQUIDATION_LEVERAGE = 10;

const ON_MODES = ["isolated", "cross"] as const;
type LiquidationMode = (typeof ON_MODES)[number];

function onMode(value: unknown): LiquidationMode | null {
  return typeof value === "string" && (ON_MODES as readonly string[]).includes(value)
    ? (value as LiquidationMode)
    : null;
}

/** A positive finite number, otherwise null (never coerce a string/NaN). */
function positiveNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;
}

/** A finite count ≥ 0 (0 hits is meaningful, so it is kept). */
function nonNegativeNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

/** The `notes.liquidation` object, defensively read from the stored string. */
function readLiquidation(
  notes: string | Record<string, unknown> | null | undefined,
): Record<string, unknown> | null {
  const parsed = typeof notes === "string" ? parseResultNotes(notes) : (notes ?? null);
  if (parsed == null || typeof parsed !== "object" || Array.isArray(parsed)) return null;
  const liquidation = (parsed as Record<string, unknown>).liquidation;
  if (liquidation == null || typeof liquidation !== "object" || Array.isArray(liquidation)) {
    return null;
  }
  return liquidation as Record<string, unknown>;
}

/**
 * D3: "Liquidation isolated · 20x" (validation appends "· N hits"). The stored
 * parameters win; discovery runs that carry only notes.liquidation fall back to
 * it. Off, absent or unreadable → null (render nothing).
 */
export function chipLabel(
  params: Record<string, unknown> | null | undefined,
  notes: string | Record<string, unknown> | null | undefined,
): string | null {
  const liquidation = readLiquidation(notes);
  const paramsMode = onMode(params?.liquidation_mode);
  const notesMode = onMode(liquidation?.mode);
  const mode = paramsMode ?? notesMode;
  if (mode == null) return null;

  const leverage =
    (paramsMode != null
      ? positiveNumber(params?.leverage)
      : positiveNumber(liquidation?.leverage)) ?? DEFAULT_LIQUIDATION_LEVERAGE;

  const count = nonNegativeNumber(liquidation?.count);
  const parts = [`Liquidation ${mode}`, `${leverage}x`];
  if (count != null) parts.push(`${count} hits`);
  return parts.join(" · ");
}

/** D4: the sweep "Liq" column appears only when some row recorded a count. */
export function showLiqColumn(
  rows: readonly { liquidation_count?: number | null | undefined }[],
): boolean {
  return rows.some((r) => r.liquidation_count != null);
}

/** R1: the "N liq" badge on discovery champion/rejection rows (positive only). */
export function liqBadge(entry: { liquidation_count?: unknown } | null | undefined): string | null {
  const count = positiveNumber(entry?.liquidation_count);
  return count == null ? null : `${count} liq`;
}
