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

/**
 * D1b (job 20261015-small-beads, rows E1-E7): the exit-timing counts of one
 * mode-on screening sweep row. ``exit_timing`` is null on mode-off rows and on
 * the sentinel rows (E1/E4), and ``by_symbol`` is keyed by symbol.
 */
interface ExitTimingCounts {
  refined: number;
  kept_bar_close: number;
  by_symbol: Record<string, { refined: number; kept_bar_close: number }>;
}

/** A finite whole count ≥ 0, otherwise null (never coerce a string/float/NaN). */
function nonNegativeInteger(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

/** The stored counts, or null for every absent/malformed shape (rows E1/E7). */
function readExitTiming(value: unknown): ExitTimingCounts | null {
  if (value == null || typeof value !== "object" || Array.isArray(value)) return null;
  const raw = value as Record<string, unknown>;
  const refined = nonNegativeInteger(raw.refined);
  const kept = nonNegativeInteger(raw.kept_bar_close);
  if (refined == null || kept == null) return null;

  const bySymbol = raw.by_symbol;
  if (bySymbol == null || typeof bySymbol !== "object" || Array.isArray(bySymbol)) return null;
  const symbols: ExitTimingCounts["by_symbol"] = {};
  for (const [symbol, entry] of Object.entries(bySymbol as Record<string, unknown>)) {
    if (entry == null || typeof entry !== "object" || Array.isArray(entry)) return null;
    const row = entry as Record<string, unknown>;
    const symbolRefined = nonNegativeInteger(row.refined);
    const symbolKept = nonNegativeInteger(row.kept_bar_close);
    if (symbolRefined == null || symbolKept == null) return null;
    symbols[symbol] = { refined: symbolRefined, kept_bar_close: symbolKept };
  }
  return { refined, kept_bar_close: kept, by_symbol: symbols };
}

/** D1b: the sweep "1m exits" cell — "refined / kept_bar_close" (rows E2/E3). */
export function exitTimingLabel(value: unknown): string | null {
  const counts = readExitTiming(value);
  return counts == null ? null : `${counts.refined} / ${counts.kept_bar_close}`;
}

/** D1b: the sweep cell tooltip — totals, then each symbol sorted (row E3). */
export function exitTimingTitle(value: unknown): string | null {
  const counts = readExitTiming(value);
  if (counts == null) return null;
  const parts = [
    `exits re-timed to 1m: ${counts.refined}`,
    `kept at bar close: ${counts.kept_bar_close}`,
  ];
  const entries = Object.entries(counts.by_symbol).sort(([left], [right]) =>
    left < right ? -1 : left > right ? 1 : 0,
  );
  for (const [symbol, row] of entries) {
    parts.push(`${symbol} ${row.refined}/${row.kept_bar_close}`);
  }
  return parts.join(" · ");
}

/** D1b: the "1m exits" column appears when any row's counts are valid (E6). */
export function showExitColumn(rows: readonly { exit_timing?: unknown }[]): boolean {
  return rows.some((row) => readExitTiming(row.exit_timing) != null);
}

/** D1b (E5): the chip note needs exactly one sweep row with valid counts. */
export function runExitTimingNote(rows: readonly { exit_timing?: unknown }[]): string | null {
  if (rows.length !== 1) return null;
  const [only] = rows;
  const counts = readExitTiming(only?.exit_timing);
  return counts == null
    ? null
    : `exits re-timed to 1m: ${counts.refined} · kept at bar close: ${counts.kept_bar_close}`;
}
