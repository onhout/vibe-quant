/**
 * Sweep payload sent by the backtest launch form must be StrategyDSL.sweep: `{<indicator>_<param>:
 * [values]}` (vibe-quant-tsxf8). The payloads are snapshotted to __fixtures__/*.json; a python
 * test (tests/unit/test_ui_sweep_payload.py) feeds them to the real screening runner.
 */
import { describe, expect, it } from "vitest";
import templates from "../strategies/editor/__fixtures__/templates.json";
import catalogDefaults from "./__fixtures__/catalog_ui_strategy.json";
import folded from "./__fixtures__/folded_ui_strategy.json";
import catalog from "./__fixtures__/indicator_catalog.json";
import legacy from "./__fixtures__/legacy_ui_strategy.json";
import {
  expandParam,
  MAX_SWEEP_COMBINATIONS,
  MAX_VALUES_PER_PARAM,
  type SweepableParam,
  type SweepCatalog,
  type SweepConfig,
  type SweepParam,
  strategyTranslationProblem,
  sweepableParamsFor,
  sweepProblem,
  sweepToPayload,
} from "./SweepBuilder";

type Dict = Record<string, unknown>;
/** JSON with inline number arrays: the shape biome's formatter keeps, so fixtures lint clean. */
const fixtureJson = (v: unknown): string =>
  `${JSON.stringify(v, null, 2).replace(/\[([^\]]*)\]/g, (_m, inner: string) => `[${inner.replace(/\s*\n\s*/g, " ").trim()}]`)}\n`;
const CATALOG = catalog as SweepCatalog;
const TEMPLATES = templates as Array<{ name: string; dsl_config: Dict }>;

/** What a user gets after "Initialize" + switching every param to a 2-value list `v, v+1`. */
function twoValueSweep(params: SweepableParam[]): SweepConfig {
  return {
    params: params.map((p) => ({
      key: p.key,
      label: p.label,
      type: "list",
      fixedValue: p.currentValue,
      rangeMin: 1,
      rangeMax: 2,
      rangeStep: 1,
      listValues: `${p.currentValue}, ${p.currentValue + 1}`,
    })),
  };
}

const range = (over: Partial<SweepParam>): SweepParam => ({
  key: "ema_period",
  label: "ema.period",
  type: "range",
  fixedValue: 0,
  rangeMin: 1,
  rangeMax: 3,
  rangeStep: 1,
  listValues: "",
  ...over,
});

describe("sweepableParamsFor canonical strategies (templates)", () => {
  it("keys are <indicator_name>_<param> for every numeric indicator param", () => {
    for (const t of TEMPLATES) {
      const expected: string[] = [];
      for (const [name, cfg] of Object.entries(t.dsl_config.indicators as Record<string, Dict>)) {
        for (const [param, v] of Object.entries(cfg)) {
          if (typeof v === "number") expected.push(`${name}_${param}`);
        }
      }
      expect(
        sweepableParamsFor(t.dsl_config).map((p) => p.key),
        t.name,
      ).toEqual(expected);
    }
  });

  it("dual_ema_crossover 2-value sweep is a dict of value lists", () => {
    const t = TEMPLATES.find((x) => x.name === "dual_ema_crossover");
    const sweep = twoValueSweep(sweepableParamsFor(t!.dsl_config));
    expect(sweepToPayload(sweep)).toEqual({
      ema_fast_period: [12, 13],
      ema_slow_period: [26, 27],
      atr_period: [14, 15],
    });
  });

  it("macd keeps its *_period field names", () => {
    const t = TEMPLATES.find((x) => x.name === "macd_crossover");
    const keys = sweepableParamsFor(t!.dsl_config).map((p) => p.key);
    expect(keys).toContain("macd_fast_period");
    expect(keys).toContain("macd_signal_period");
  });

  it("template sweep payloads match the snapshot the backend test consumes", async () => {
    const payloads = Object.fromEntries(
      TEMPLATES.map((t) => [
        t.name,
        sweepToPayload(twoValueSweep(sweepableParamsFor(t.dsl_config))),
      ]),
    );
    await expect(fixtureJson(payloads)).toMatchFileSnapshot(
      "./__fixtures__/template_sweep_payloads.json",
    );
  });
});

describe("sweepableParamsFor UI-format strategies (backend translator renames indicators)", () => {
  it("keys follow translator naming: <type>_<period>, _2 on clashes, fast -> fast_period", () => {
    expect(sweepableParamsFor(legacy as Dict).map((p) => p.key)).toEqual([
      "ema_12_period",
      "ema_12_2_period",
      "macd_fast_period",
      "macd_slow_period",
      "macd_signal_period",
      "bbands_20_period",
      "bbands_20_std_dev",
      "stoch_period",
      "stoch_d_period",
      "atr_14_period",
    ]);
  });

  it("legacy sweep payload matches the snapshot the backend test consumes", async () => {
    const payload = sweepToPayload(twoValueSweep(sweepableParamsFor(legacy as Dict)));
    expect(payload.macd_fast_period).toEqual([12, 13]);
    await expect(fixtureJson(payload)).toMatchFileSnapshot(
      "./__fixtures__/legacy_sweep_payload.json",
    );
  });
});

describe("sweepableParamsFor with catalog defaults (what the editor really stores)", () => {
  const keysOf = (raw: Dict) =>
    sweepableParamsFor(raw, CATALOG).map((p) => `${p.key}${p.integer ? ":int" : ":float"}`);

  it("keeps registry spellings, aliases period_k/period_d and catalog extras", () => {
    expect(keysOf(catalogDefaults as Dict)).toEqual([
      "ema_12_period:int",
      "atr_14_period:int",
      "macd_fast_period:int",
      "macd_slow_period:int",
      "macd_signal_period:int",
      "stoch_period:int",
      "stoch_d_period:int",
      "ichimoku_tenkan:int",
      "ichimoku_kijun:int",
      "ichimoku_senkou:int",
      "adaptive_rsi_14_period:int",
      "adaptive_rsi_14_alpha:float",
      "squeeze_mom_length:int",
      "bbands_20_period:int",
      "bbands_20_std_dev:float",
    ]);
  });

  it("a single MACD or STOCH indicator is still sweepable", () => {
    const only = (type: string, params: Record<string, number>) =>
      sweepableParamsFor({ general: {}, indicators: [{ type, params }] }, CATALOG).map(
        (p) => p.key,
      );
    expect(only("MACD", { fast_period: 12, slow_period: 26, signal_period: 9 })).toEqual([
      "macd_fast_period",
      "macd_slow_period",
      "macd_signal_period",
    ]);
    expect(only("STOCH", { period_k: 14, period_d: 3 })).toEqual([
      "stoch_period",
      "stoch_d_period",
    ]);
    // without the catalog (still loading) the natives and aliases still resolve
    expect(
      sweepableParamsFor({
        general: {},
        indicators: [{ type: "STOCH", params: { period_k: 14 } }],
      }).map((p) => p.key),
    ).toEqual(["stoch_period"]);
  });

  it("BB keeps its editor name (bb_20); synonyms are STOCH-only for k/d", () => {
    const raw = {
      general: {},
      indicators: [
        { type: "BB", params: { period: 20, std_dev: 2 } },
        { type: "RSI", params: { period: 14, k_period: 3 } },
      ],
    };
    expect(sweepableParamsFor(raw, CATALOG).map((p) => p.key)).toEqual([
      "bb_20_period",
      "bb_20_std_dev",
      "rsi_14_period",
    ]);
  });

  it("equal spellings fold into one field (translator: nothing is last-wins)", () => {
    expect(strategyTranslationProblem(folded as Dict)).toBeNull();
    expect(sweepableParamsFor(folded as Dict, CATALOG).map((p) => p.key)).toEqual([
      "ema_20_period",
      "atr_14_period",
      "macd_fast_period",
      "macd_slow_period",
      "macd_signal_period",
      "stoch_14_period",
      "stoch_14_d_period",
    ]);
  });

  it("different values for two spellings: no sweep key, and the launch is flagged", () => {
    const conflict = (type: string, params: Record<string, number>) => ({
      general: {},
      indicators: [{ type, params }],
    });
    const stoch = conflict("STOCH", { period: 10, k_period: 14, d_period: 3 });
    expect(strategyTranslationProblem(stoch)).toBe(
      "Conflicting STOCH params for 'period': 'period'=10, 'k_period'=14",
    );
    expect(sweepableParamsFor(stoch, CATALOG).map((p) => p.key)).toEqual(["stoch_10_d_period"]);
    const macd = conflict("MACD", { fast: 12, fast_period: 9, slow: 26 });
    expect(strategyTranslationProblem(macd)).toMatch(/Conflicting MACD params for 'fast_period'/);
    expect(sweepableParamsFor(macd, CATALOG).map((p) => p.key)).toEqual(["macd_slow_period"]);
    // canonical strategies never go through the translator
    const templ = TEMPLATES[0]?.dsl_config as Dict;
    expect(strategyTranslationProblem(templ)).toBeNull();
  });

  it("folded payload matches the snapshot the backend test decodes", async () => {
    const payload = sweepToPayload(twoValueSweep(sweepableParamsFor(folded as Dict, CATALOG)));
    await expect(fixtureJson(payload)).toMatchFileSnapshot(
      "./__fixtures__/folded_sweep_payload.json",
    );
  });

  it("integer defaults are normalised like the translator (12.5 compiles as 12)", () => {
    const [ema] = sweepableParamsFor(catalogDefaults as Dict, CATALOG);
    expect(ema).toMatchObject({ key: "ema_12_period", currentValue: 12, integer: true });
  });

  it("catalog payload matches the snapshot the backend test decodes", async () => {
    const payload = sweepToPayload(
      twoValueSweep(sweepableParamsFor(catalogDefaults as Dict, CATALOG)),
    );
    await expect(fixtureJson(payload)).toMatchFileSnapshot(
      "./__fixtures__/catalog_sweep_payload.json",
    );
  });
});

describe("expandParam / sweepToPayload", () => {
  it("range expands to a plain list and never overshoots max", () => {
    expect(
      sweepToPayload({ params: [range({ rangeMin: 1, rangeMax: 10, rangeStep: 2 })] }),
    ).toEqual({
      ema_period: [1, 3, 5, 7, 9],
    });
  });

  it("fixed becomes a one-value list", () => {
    expect(sweepToPayload({ params: [range({ type: "fixed", fixedValue: 14 })] })).toEqual({
      ema_period: [14],
    });
  });

  it("tiny steps are not collapsed by rounding", () => {
    const vals = expandParam(range({ rangeMin: 1, rangeMax: 1.0000000003, rangeStep: 1e-10 }));
    expect(new Set(vals).size).toBe(4);
  });

  it("float steps carry no drift", () => {
    expect(expandParam(range({ rangeMin: 0.1, rangeMax: 0.5, rangeStep: 0.1 }))).toEqual([
      0.1, 0.2, 0.3, 0.4, 0.5,
    ]);
  });

  it("step <= 0, max < min, empty list and over-large expansions are empty (invalid)", () => {
    expect(expandParam(range({ rangeStep: 0 }))).toEqual([]);
    expect(expandParam(range({ rangeStep: -1 }))).toEqual([]);
    expect(expandParam(range({ rangeMin: 5, rangeMax: 1 }))).toEqual([]);
    expect(expandParam(range({ type: "list", listValues: " , x" }))).toEqual([]);
    expect(
      expandParam(range({ rangeMin: 1, rangeMax: MAX_VALUES_PER_PARAM + 1, rangeStep: 1 })),
    ).toEqual([]);
  });
});

/** What sweepableParamsFor yields under the live catalog for the `ema_period` test param. */
const sweepable = (integer: boolean, key = "ema_period"): SweepableParam[] => [
  { key, label: "ema.period", currentValue: 12, integer },
];

describe("sweepProblem integer parameters", () => {
  const ints = sweepable(true);

  it("rejects fractional values for an int-typed field (every backtest would fail decoding)", () => {
    const check = (over: Partial<SweepParam>) => sweepProblem({ params: [range(over)] }, ints);
    expect(check({ rangeMin: 12, rangeMax: 13, rangeStep: 0.5 })).toMatch(
      /ema\.period is an integer parameter; 12\.5 is not a whole number/,
    );
    expect(check({ type: "fixed", fixedValue: 12.5 })).toMatch(/12\.5/);
    expect(check({ type: "list", listValues: "10, 11.5" })).toMatch(/11\.5/);
  });

  it("accepts whole numbers on int fields and fractions on float fields", () => {
    expect(
      sweepProblem({ params: [range({ rangeMin: 10, rangeMax: 14, rangeStep: 2 })] }, ints),
    ).toBeNull();
    expect(
      sweepProblem(
        { params: [range({ rangeMin: 1, rangeMax: 2, rangeStep: 0.5 })] },
        sweepable(false),
      ),
    ).toBeNull();
  });

  it("a sweep built before the catalog arrived is checked against the catalog's int types", () => {
    // canonical ICHIMOKU: tenkan is only known to be int-typed once the catalog is loaded
    const raw = {
      indicators: { ichi: { type: "ICHIMOKU", tenkan: 9, kijun: 26, senkou: 52 } },
      entry_conditions: { long: [] },
    };
    const early = sweepableParamsFor(raw);
    expect(early.find((p) => p.key === "ichi_tenkan")?.integer).toBe(false);
    const sweep = twoValueSweep(early); // "Initialize" before the catalog loads
    const tenkan = sweep.params.find((p) => p.key === "ichi_tenkan") as SweepParam;
    tenkan.listValues = "9, 9.5"; // user edits after the catalog arrives
    expect(sweepProblem(sweep, early)).toBeNull(); // stale metadata cannot see it

    const current = sweepableParamsFor(raw, CATALOG);
    expect(current.find((p) => p.key === "ichi_tenkan")?.integer).toBe(true);
    expect(sweepProblem(sweep, current)).toMatch(
      /ichi\.tenkan is an integer parameter; 9\.5 is not a whole number/,
    );
  });

  it("a swept param the current strategy metadata no longer lists blocks the launch", () => {
    expect(sweepProblem({ params: [range({})] }, [])).toMatch(
      /ema\.period is not a sweepable parameter/,
    );
  });
});

describe("sweepProblem", () => {
  it("is null for a valid sweep", () => {
    expect(sweepProblem({ params: [range({})] }, sweepable(true))).toBeNull();
  });

  it("names the param with no valid values", () => {
    expect(sweepProblem({ params: [range({ rangeStep: 0 })] }, sweepable(true))).toMatch(
      /ema\.period has no valid/,
    );
  });

  it("rejects more combinations than the cap", () => {
    const wide = (key: string) =>
      range({ key, label: key, rangeMin: 1, rangeMax: 101, rangeStep: 1 });
    expect(MAX_SWEEP_COMBINATIONS).toBe(10_000);
    expect(
      sweepProblem({ params: [wide("a"), wide("b")] }, [
        ...sweepable(true, "a"),
        ...sweepable(true, "b"),
      ]),
    ).toMatch(/10[\s\u00a0\u202f,.]?201 combinations exceed/); // 101 * 101
  });
});
