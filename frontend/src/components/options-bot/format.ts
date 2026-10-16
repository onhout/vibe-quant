/** Loose-record readers and formatters for the options bot's JSON payloads (trade, shadow, evidence). */

export type LooseRecord = { [key: string]: unknown };

export function asRecord(value: unknown): LooseRecord | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as LooseRecord)
    : null;
}

export function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

export function str(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

export function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function usd(value: number | null | undefined, decimals = 2): string {
  if (value == null || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value);
}

export function pct(value: number | null | undefined, decimals = 1): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(decimals)}%`;
}

export function fixed(value: number | null | undefined, decimals = 2): string {
  return value == null || Number.isNaN(value) ? "—" : value.toFixed(decimals);
}

export function shortTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** "250C 2026-10-30" from a proposal's strike/option_type/expiration. */
export function contractLabel(p: {
  strike?: number | null;
  option_type?: string | null;
  expiration?: string | null;
  contract?: string | null;
}): string {
  if (!p.option_type) return p.contract ?? "—";
  const kind = p.option_type.toLowerCase().startsWith("c") ? "C" : "P";
  return `${p.strike ?? "?"}${kind} ${p.expiration ?? ""}`.trim();
}

/** Short display of any JSON value. */
export function show(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "number")
    return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(4)));
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}
