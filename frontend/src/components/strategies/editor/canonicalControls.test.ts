/**
 * Controls a canonical StrategyDSL strategy cannot persist are disabled with a note instead
 * of refusing on save (vibe-quant-5u23h). UI-format strategies keep them enabled.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { RiskTab } from "./RiskTab";
import { TimeTab } from "./TimeTab";
import { emptyDslConfig } from "./types";

const NOTE = "Not supported for canonical strategies yet";
const noop = () => {};

const risk = (canonical: boolean) =>
  renderToStaticMarkup(
    createElement(RiskTab, { config: emptyDslConfig(), onConfigChange: noop, canonical }),
  );
const time = (canonical: boolean) =>
  renderToStaticMarkup(
    createElement(TimeTab, { config: emptyDslConfig(), onConfigChange: noop, canonical }),
  );

/** The opening tag of the element with this id. */
const tagWithId = (html: string, id: string): string => {
  const m = new RegExp(`<[^<>]*\\bid="${id}"[^<>]*>`).exec(html);
  if (!m) throw new Error(`no element with id ${id}`);
  return m[0];
};
const isDisabled = (html: string, id: string) => /\bdisabled(=|\s|>)/.test(tagWithId(html, id));

const SESSION_IDS = ["session-Asian", "session-European", "session-US"];

describe("RiskTab on canonical strategies", () => {
  it("disables trailing stop and position sizing and says why", () => {
    const html = risk(true);
    expect(isDisabled(html, "trailing-stop-enabled")).toBe(true);
    expect(isDisabled(html, "position-sizing-method")).toBe(true);
    expect(isDisabled(html, "position-sizing-value")).toBe(true);
    expect(html.split(NOTE).length - 1).toBe(2); // one note per section
  });

  it("keeps stop loss / take profit editable", () => {
    const html = risk(true);
    expect(isDisabled(html, "stop-loss-type")).toBe(false);
    expect(isDisabled(html, "stop-loss-value")).toBe(false);
  });

  it("leaves everything enabled for UI-format strategies", () => {
    const html = risk(false);
    for (const id of ["trailing-stop-enabled", "position-sizing-method", "position-sizing-value"])
      expect(isDisabled(html, id)).toBe(false);
    expect(html).not.toContain(NOTE);
  });
});

describe("TimeTab on canonical strategies", () => {
  it("disables the session chips and says why", () => {
    const html = time(true);
    for (const id of SESSION_IDS) expect(isDisabled(html, id)).toBe(true);
    expect(html.split(NOTE).length - 1).toBe(1);
  });

  it("keeps trading hours, days and funding avoidance editable", () => {
    const html = time(true);
    expect(isDisabled(html, "day-Monday")).toBe(false);
    expect(isDisabled(html, "funding-avoidance")).toBe(false);
  });

  it("leaves session chips enabled for UI-format strategies", () => {
    const html = time(false);
    for (const id of SESSION_IDS) expect(isDisabled(html, id)).toBe(false);
    expect(html).not.toContain(NOTE);
  });
});
