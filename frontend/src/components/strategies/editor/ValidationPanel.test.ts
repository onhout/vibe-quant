/**
 * ValidationPanel on canonical StrategyDSL strategies (vibe-quant-yj57u): entries live in
 * conditions.long_entry/short_entry and symbols are chosen at launch, so neither may warn.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import templates from "./__fixtures__/templates.json";
import { type DslConfig, emptyDslConfig, parseDslConfig } from "./types";
import { ValidationPanel } from "./ValidationPanel";

interface Template {
  name: string;
  dsl_config: Record<string, unknown>;
}

const render = (config: DslConfig, canonical: boolean) =>
  renderToStaticMarkup(createElement(ValidationPanel, { config, canonical }));

const canonicalConfig = (name: string): DslConfig => {
  const t = (templates as Template[]).find((x) => x.name === name);
  if (!t) throw new Error(`no template ${name}`);
  return parseDslConfig(t.dsl_config);
};

describe("ValidationPanel", () => {
  it("canonical strategy with entries + exits shows no false entry/symbol/exit warnings", () => {
    const html = render(canonicalConfig("dual_ema_crossover"), true);
    expect(html).not.toContain("No entry conditions defined");
    expect(html).not.toContain("No symbols configured");
    expect(html).not.toContain("No exit conditions defined");
  });

  it("canonical strategy with only short entries counts as having entries", () => {
    const config = canonicalConfig("dual_ema_crossover");
    config.conditions.long_entry = [];
    expect(config.conditions.short_entry?.length).toBeGreaterThan(0);
    expect(render(config, true)).not.toContain("No entry conditions defined");
  });

  it("canonical strategy with no entries at all still warns", () => {
    const config = canonicalConfig("dual_ema_crossover");
    config.conditions.long_entry = [];
    config.conditions.short_entry = [];
    expect(render(config, true)).toContain("No entry conditions defined");
  });

  it("empty UI-format strategy still warns about entries and symbols", () => {
    const html = render(emptyDslConfig(), false);
    expect(html).toContain("No entry conditions defined");
    expect(html).toContain("No symbols configured");
  });

  it("UI-format strategy with symbols but canonical=false keeps the symbols check", () => {
    const config = emptyDslConfig();
    config.general.symbols = ["BTCUSDT"];
    expect(render(config, false)).not.toContain("No symbols configured");
  });
});
