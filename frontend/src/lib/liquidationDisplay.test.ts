/**
 * Display helpers for the liquidation surface (bead vibe-quant-2gyk1.1.10, E2).
 * Contract rows D3 (chip), D4 (sweep column) and R1 (discovery badge) in
 * data/swarm/20261012-p1-honest/design-t5.md.
 *
 * The stored ``parameters`` carry ``liquidation_mode`` (+ ``leverage``, default
 * 10) for screening/validation/discovery runs; ``notes`` is the machine-JSON
 * string on a result row and carries ``liquidation`` (``{mode, leverage}`` for
 * discovery, ``{..., count}`` for validation). Every unreadable shape must
 * render nothing, never a partial chip.
 */
import { describe, expect, it } from "vitest";
import { chipLabel, liqBadge, showLiqColumn } from "@/lib/liquidationDisplay";

describe("chipLabel (D3)", () => {
  it("absent mode renders nothing", () => {
    expect(chipLabel({}, null)).toBeNull();
    expect(chipLabel({}, undefined)).toBeNull();
    expect(chipLabel(null, null)).toBeNull();
    expect(chipLabel(undefined, undefined)).toBeNull();
    expect(chipLabel({ leverage: 20 }, null)).toBeNull();
  });

  it("mode off renders nothing", () => {
    expect(chipLabel({ liquidation_mode: "off" }, null)).toBeNull();
  });

  it("params isolated with leverage", () => {
    expect(chipLabel({ liquidation_mode: "isolated", leverage: 20 }, null)).toBe(
      "Liquidation isolated · 20x",
    );
  });

  it("params cross without leverage defaults to 10x", () => {
    expect(chipLabel({ liquidation_mode: "cross" }, null)).toBe("Liquidation cross · 10x");
  });

  it("params leverage wins over notes leverage; hits still come from notes", () => {
    const notes = JSON.stringify({
      liquidation: { mode: "isolated", leverage: 50, count: 3 },
    });
    expect(chipLabel({ liquidation_mode: "isolated", leverage: 20 }, notes)).toBe(
      "Liquidation isolated · 20x · 3 hits",
    );
  });

  it("validation run adds the hit count", () => {
    const notes = JSON.stringify({ liquidation: { mode: "cross", leverage: 20, count: 4 } });
    expect(chipLabel({ liquidation_mode: "cross", leverage: 20 }, notes)).toBe(
      "Liquidation cross · 20x · 4 hits",
    );
  });

  it("a validation run that hit nothing shows 0 hits", () => {
    const notes = JSON.stringify({ liquidation: { mode: "isolated", leverage: 20, count: 0 } });
    expect(chipLabel({ liquidation_mode: "isolated", leverage: 20 }, notes)).toBe(
      "Liquidation isolated · 20x · 0 hits",
    );
  });

  it("discovery reads the mode and leverage off notes when params carry no mode", () => {
    const notes = JSON.stringify({ liquidation: { mode: "cross", leverage: 5 } });
    expect(chipLabel(null, notes)).toBe("Liquidation cross · 5x");
    expect(chipLabel(undefined, notes)).toBe("Liquidation cross · 5x");
  });

  it("discovery notes without leverage default to 10x", () => {
    const notes = JSON.stringify({ liquidation: { mode: "isolated" } });
    expect(chipLabel(null, notes)).toBe("Liquidation isolated · 10x");
  });

  it("a malformed notes string renders nothing", () => {
    expect(chipLabel(null, "not json")).toBeNull();
    expect(chipLabel(null, '{"liquidation": "nope"}')).toBeNull();
    expect(chipLabel(null, '{"liquidation": []}')).toBeNull();
  });

  it("a malformed notes string still shows the params chip (no hits)", () => {
    expect(chipLabel({ liquidation_mode: "isolated", leverage: 20 }, "not json")).toBe(
      "Liquidation isolated · 20x",
    );
  });

  it("an invalid stored mode renders nothing", () => {
    expect(chipLabel({ liquidation_mode: "Isolated" }, null)).toBeNull();
    expect(chipLabel({ liquidation_mode: "bogus" }, null)).toBeNull();
    expect(chipLabel({ liquidation_mode: 1 }, null)).toBeNull();
  });

  it("a non-numeric or non-positive stored leverage falls back to 10x", () => {
    expect(chipLabel({ liquidation_mode: "isolated", leverage: "20" }, null)).toBe(
      "Liquidation isolated · 10x",
    );
    expect(chipLabel({ liquidation_mode: "isolated", leverage: 0 }, null)).toBe(
      "Liquidation isolated · 10x",
    );
  });
});

describe("showLiqColumn (D4)", () => {
  it("hidden when no row is non-null", () => {
    expect(showLiqColumn([])).toBe(false);
    expect(showLiqColumn([{ liquidation_count: null }, { liquidation_count: null }])).toBe(false);
    expect(showLiqColumn([{ liquidation_count: undefined }])).toBe(false);
    expect(showLiqColumn([{}])).toBe(false);
  });

  it("shown when any row is non-null", () => {
    expect(showLiqColumn([{ liquidation_count: null }, { liquidation_count: 0 }])).toBe(true);
    expect(showLiqColumn([{ liquidation_count: 3 }])).toBe(true);
  });
});

describe("liqBadge (R1)", () => {
  it("no badge when absent or zero", () => {
    expect(liqBadge(null)).toBeNull();
    expect(liqBadge(undefined)).toBeNull();
    expect(liqBadge({})).toBeNull();
    expect(liqBadge({ liquidation_count: null })).toBeNull();
    expect(liqBadge({ liquidation_count: 0 })).toBeNull();
  });

  it("badge for a positive count", () => {
    expect(liqBadge({ liquidation_count: 3 })).toBe("3 liq");
    expect(liqBadge({ liquidation_count: 1 })).toBe("1 liq");
  });

  it("a non-numeric or negative count is not a badge", () => {
    expect(liqBadge({ liquidation_count: "3" })).toBeNull();
    expect(liqBadge({ liquidation_count: -1 })).toBeNull();
    expect(liqBadge({ liquidation_count: Number.NaN })).toBeNull();
  });
});
