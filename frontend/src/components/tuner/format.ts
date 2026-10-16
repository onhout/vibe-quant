export function pct(value: number | null | undefined, digits = 1): string {
  if (value == null || Number.isNaN(value)) return "--";
  const v = value * 100;
  return `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

export function dd(value: number | null | undefined, digits = 1): string {
  if (value == null || Number.isNaN(value)) return "--";
  return `${(Math.abs(value) * 100).toFixed(digits)}%`;
}

export function num(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) return "--";
  return value.toFixed(digits);
}

export function when(iso: string | null | undefined): string {
  if (!iso) return "--";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
