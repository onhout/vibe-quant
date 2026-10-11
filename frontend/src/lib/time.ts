/**
 * Epoch ms of a server timestamp. SQLite `datetime('now')` text has no zone but is UTC,
 * while V8 parses a zone-less string as LOCAL time (vibe-quant-u6dsn), so the zone is
 * added here. Strings that already carry a zone are parsed unchanged.
 */
const ZONELESS_DATETIME = /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/;

export function parseServerTimestamp(value: string | null | undefined): number | undefined {
  if (!value) return undefined;
  const ms = Date.parse(ZONELESS_DATETIME.test(value) ? `${value.replace(" ", "T")}Z` : value);
  return Number.isNaN(ms) ? undefined : ms;
}

export function formatRelative(iso: string | null | undefined, fallback = "—"): string {
  const t = parseServerTimestamp(iso);
  if (t === undefined) return fallback;
  const min = Math.floor((Date.now() - t) / 60_000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return `${Math.floor(hr / 24)}d ago`;
}
