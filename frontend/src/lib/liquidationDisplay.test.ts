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
 *
 * The exit-timing helpers (rows E1-E7 of data/swarm/20261015-small-beads/
 * design.md D1b) read the screening sweep row's ``exit_timing``
 * (``{refined, kept_bar_close, by_symbol}``): the sweep "1m exits" column and
 * the chip note. Same rule: a malformed shape renders nothing.
 */
import { describe, expect, it } from "vitest";
import {
  chipLabel,
  exitTimingLabel,
  exitTimingTitle,
  liqBadge,
  runExitTimingNote,
  showExitColumn,
  showLiqColumn,
} from "@/lib/liquidationDisplay";

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

const E3_EXIT_TIMING = {
  refined: 18,
  kept_bar_close: 2,
  by_symbol: {
    ETHUSDT: { refined: 6, kept_bar_close: 1 },
    BTCUSDT: { refined: 12, kept_bar_close: 1 },
  },
};

const ZERO_EXIT_TIMING = { refined: 0, kept_bar_close: 0, by_symbol: {} };

describe("exitTimingLabel (E2/E3/E7)", () => {
  it("E3: refined / kept_bar_close", () => {
    expect(exitTimingLabel(E3_EXIT_TIMING)).toBe("18 / 2");
  });

  it("E2: zeros are rendered (mode on, nothing to re-time)", () => {
    expect(exitTimingLabel(ZERO_EXIT_TIMING)).toBe("0 / 0");
  });

  it("E1: null, absent or non-object renders nothing", () => {
    expect(exitTimingLabel(null)).toBeNull();
    expect(exitTimingLabel(undefined)).toBeNull();
    expect(exitTimingLabel("18 / 2")).toBeNull();
    expect(exitTimingLabel(18)).toBeNull();
    expect(exitTimingLabel([18, 2])).toBeNull();
    expect(exitTimingLabel({})).toBeNull();
  });

  it("E7: a bad refined count renders nothing", () => {
    for (const refined of [-1, 1.5, Number.NaN, Number.POSITIVE_INFINITY, "18", true, null]) {
      expect(exitTimingLabel({ refined, kept_bar_close: 0, by_symbol: {} })).toBeNull();
    }
  });

  it("E7: a bad kept_bar_close count renders nothing", () => {
    for (const kept of [-1, 1.5, Number.NaN, Number.NEGATIVE_INFINITY, "2", false, null]) {
      expect(exitTimingLabel({ refined: 18, kept_bar_close: kept, by_symbol: {} })).toBeNull();
    }
  });

  it("E7: a malformed by_symbol renders nothing", () => {
    expect(exitTimingLabel({ refined: 18, kept_bar_close: 2 })).toBeNull();
    expect(exitTimingLabel({ refined: 18, kept_bar_close: 2, by_symbol: null })).toBeNull();
    expect(exitTimingLabel({ refined: 18, kept_bar_close: 2, by_symbol: [] })).toBeNull();
    expect(exitTimingLabel({ refined: 18, kept_bar_close: 2, by_symbol: "BTCUSDT" })).toBeNull();
    expect(
      exitTimingLabel({ refined: 18, kept_bar_close: 2, by_symbol: { BTCUSDT: 12 } }),
    ).toBeNull();
    expect(
      exitTimingLabel({
        refined: 18,
        kept_bar_close: 2,
        by_symbol: { BTCUSDT: { refined: 12, kept_bar_close: "1" } },
      }),
    ).toBeNull();
  });
});

describe("exitTimingTitle (E2/E3/E7)", () => {
  it("E3: totals then each symbol, sorted", () => {
    expect(exitTimingTitle(E3_EXIT_TIMING)).toBe(
      "exits re-timed to 1m: 18 · kept at bar close: 2 · BTCUSDT 12/1 · ETHUSDT 6/1",
    );
  });

  it("E2: no symbol part when by_symbol is empty", () => {
    expect(exitTimingTitle(ZERO_EXIT_TIMING)).toBe(
      "exits re-timed to 1m: 0 · kept at bar close: 0",
    );
  });

  it("E1/E7: null and malformed render nothing", () => {
    expect(exitTimingTitle(null)).toBeNull();
    expect(exitTimingTitle(undefined)).toBeNull();
    expect(exitTimingTitle({ refined: -1, kept_bar_close: 0, by_symbol: {} })).toBeNull();
    expect(exitTimingTitle({ refined: 18, kept_bar_close: 2 })).toBeNull();
    expect(
      exitTimingTitle({ refined: 18, kept_bar_close: 2, by_symbol: { BTCUSDT: { refined: 1 } } }),
    ).toBeNull();
  });
});

describe("showExitColumn (E1/E5/E6/E7)", () => {
  it("hidden when no row has a valid exit_timing", () => {
    expect(showExitColumn([])).toBe(false);
    expect(showExitColumn([{}, { exit_timing: null }])).toBe(false);
    expect(showExitColumn([{ exit_timing: null }, { exit_timing: undefined }])).toBe(false);
    expect(showExitColumn([{ exit_timing: "18 / 2" }])).toBe(false);
    expect(
      showExitColumn([{ exit_timing: { refined: -1, kept_bar_close: 0, by_symbol: {} } }]),
    ).toBe(false);
    expect(
      showExitColumn([{ exit_timing: { refined: 1.5, kept_bar_close: 0, by_symbol: {} } }]),
    ).toBe(false);
  });

  it("E5: shown for a 3-row sweep with one valid row", () => {
    expect(
      showExitColumn([
        { exit_timing: E3_EXIT_TIMING },
        { exit_timing: null },
        { exit_timing: null },
      ]),
    ).toBe(true);
  });

  it("E6: shown for a mixed valid + null sweep; the null row's cell is -", () => {
    expect(showExitColumn([{ exit_timing: null }, { exit_timing: E3_EXIT_TIMING }])).toBe(true);
    expect(exitTimingLabel(null) ?? "-").toBe("-");
  });
});

describe("runExitTimingNote (E1/E2/E3/E5/E6)", () => {
  it("E3: a one-row run shows the note next to the chip", () => {
    expect(runExitTimingNote([{ exit_timing: E3_EXIT_TIMING }])).toBe(
      "exits re-timed to 1m: 18 · kept at bar close: 2",
    );
  });

  it("E2: zeros are shown", () => {
    expect(runExitTimingNote([{ exit_timing: ZERO_EXIT_TIMING }])).toBe(
      "exits re-timed to 1m: 0 · kept at bar close: 0",
    );
  });

  it("E5: more than one row hides the note", () => {
    expect(
      runExitTimingNote([
        { exit_timing: E3_EXIT_TIMING },
        { exit_timing: E3_EXIT_TIMING },
        { exit_timing: E3_EXIT_TIMING },
      ]),
    ).toBeNull();
    expect(
      runExitTimingNote([{ exit_timing: E3_EXIT_TIMING }, { exit_timing: E3_EXIT_TIMING }]),
    ).toBeNull();
  });

  it("E1/E6: no row, or a null/invalid exit_timing, hides the note", () => {
    expect(runExitTimingNote([])).toBeNull();
    expect(runExitTimingNote([{}])).toBeNull();
    expect(runExitTimingNote([{ exit_timing: null }])).toBeNull();
    expect(runExitTimingNote([{ exit_timing: { refined: 18 } }])).toBeNull();
    expect(runExitTimingNote([{ exit_timing: "18 / 2" }])).toBeNull();
  });
});
