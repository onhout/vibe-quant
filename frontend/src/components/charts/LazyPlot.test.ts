import { describe, expect, it, vi } from "vitest";

const plotlyBundle = { newPlot: () => undefined, tag: "dist-min" };
const boundComponent = () => null;
const factory = vi.hoisted(() => vi.fn());

vi.mock("plotly.js-dist-min", () => ({ default: plotlyBundle }));
vi.mock("react-plotly.js/factory", () => ({ default: factory }));

describe("LazyPlot", () => {
  it("binds react-plotly.js to the plotly.js-dist-min bundle via the factory", async () => {
    factory.mockReturnValue(boundComponent);
    const { loadPlotComponent } = await import("./LazyPlot");

    const loaded = await loadPlotComponent();

    expect(factory).toHaveBeenCalledTimes(1);
    expect(factory.mock.calls[0]?.[0]).toBe(plotlyBundle);
    expect(loaded.default).toBe(boundComponent);
  });

  it("unwraps double-wrapped default exports (Rolldown CJS interop)", async () => {
    vi.resetModules();
    factory.mockReset();
    factory.mockReturnValue(boundComponent);
    vi.doMock("plotly.js-dist-min", () => ({ default: { default: plotlyBundle } }));
    vi.doMock("react-plotly.js/factory", () => ({ default: { default: factory } }));
    const { loadPlotComponent } = await import("./LazyPlot");

    const loaded = await loadPlotComponent();

    expect(factory.mock.calls[0]?.[0]).toBe(plotlyBundle);
    expect(loaded.default).toBe(boundComponent);
  });
});
