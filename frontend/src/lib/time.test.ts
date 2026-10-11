/**
 * Server timestamps (vibe-quant-u6dsn): SQLite `datetime('now')` text carries no zone
 * but is UTC, while V8 parses a zone-less string as LOCAL time. Forced to a non-UTC
 * zone so mis-reading zone-less text as local is observable.
 */
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { formatRelative, parseServerTimestamp } from "@/lib/time";

describe("parseServerTimestamp", () => {
  beforeAll(() => {
    vi.stubEnv("TZ", "Europe/Berlin");
  });
  afterAll(() => {
    vi.unstubAllEnvs();
  });

  it("runs under a non-UTC zone (guard for the assertions below)", () => {
    expect(new Date(2026, 0, 3).getTimezoneOffset()).toBe(-60);
  });

  it("reads zone-less SQLite datetime text as UTC, not local time", () => {
    expect(parseServerTimestamp("2026-01-03 00:00:00")).toBe(Date.UTC(2026, 0, 3));
  });

  it("reads the T form and fractional seconds as UTC", () => {
    expect(parseServerTimestamp("2026-01-03T00:00:00.500")).toBe(
      Date.UTC(2026, 0, 3, 0, 0, 0, 500),
    );
  });

  it("keeps an ISO instant with a Z zone unchanged", () => {
    expect(parseServerTimestamp("2026-01-03T00:00:00Z")).toBe(Date.UTC(2026, 0, 3));
  });

  it("keeps an ISO instant with a numeric offset unchanged", () => {
    expect(parseServerTimestamp("2026-01-03T02:00:00+02:00")).toBe(Date.UTC(2026, 0, 3));
  });

  it("returns undefined for null, empty and unparseable input", () => {
    expect(parseServerTimestamp(null)).toBeUndefined();
    expect(parseServerTimestamp(undefined)).toBeUndefined();
    expect(parseServerTimestamp("")).toBeUndefined();
    expect(parseServerTimestamp("not a date")).toBeUndefined();
  });
});

describe("formatRelative", () => {
  beforeAll(() => {
    vi.stubEnv("TZ", "Europe/Berlin");
  });
  afterAll(() => {
    vi.unstubAllEnvs();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("runs under a non-UTC zone (guard for the assertions below)", () => {
    expect(new Date(2026, 0, 3).getTimezoneOffset()).toBe(-60);
  });

  it("reads zone-less server text as UTC, not the browser's local time", () => {
    vi.spyOn(Date, "now").mockReturnValue(Date.UTC(2026, 0, 3, 12, 0, 0));
    expect(formatRelative("2026-01-03 00:00:00")).toBe("12h ago");
  });

  it("keeps a zone-carrying instant unchanged", () => {
    vi.spyOn(Date, "now").mockReturnValue(Date.UTC(2026, 0, 3, 12, 0, 0));
    expect(formatRelative("2026-01-03T11:30:00Z")).toBe("30m ago");
  });

  it("falls back for null, empty and unparseable input", () => {
    expect(formatRelative(null, "never")).toBe("never");
    expect(formatRelative("", "never")).toBe("never");
    expect(formatRelative("not a date", "never")).toBe("never");
  });
});
