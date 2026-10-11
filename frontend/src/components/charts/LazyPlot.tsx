import type { Config, Data, Layout } from "plotly.js-dist-min";
import { lazy, Suspense } from "react";
import type { PlotParams } from "react-plotly.js";

export type LazyPlotProps = Omit<PlotParams, "data" | "layout" | "config"> & {
  data: Data[];
  layout?: Partial<Layout>;
  config?: Partial<Config>;
};

/**
 * CJS/UMD default imports resolve to `{ default: x }` or `{ default: { default: x } }`
 * depending on the bundler's interop (Vite 7 vs Rolldown), so unwrap until `x`.
 */
function unwrapDefault<T>(mod: unknown, isExpected: (value: unknown) => boolean): T {
  let value = mod;
  for (let i = 0; i < 3 && !isExpected(value) && typeof value === "object" && value !== null; i++) {
    if (!("default" in value)) break;
    value = value.default;
  }
  return value as T;
}

/**
 * Bind react-plotly.js to the minified plotly.js-dist-min bundle. The default
 * `react-plotly.js` entry would instead pull the full `plotly.js` package.
 */
export async function loadPlotComponent() {
  const [factoryModule, plotlyModule] = await Promise.all([
    import("react-plotly.js/factory"),
    import("plotly.js-dist-min"),
  ]);
  const createPlotlyComponent = unwrapDefault<(typeof factoryModule)["default"]>(
    factoryModule,
    (v) => typeof v === "function",
  );
  const Plotly = unwrapDefault<(typeof plotlyModule)["default"]>(
    plotlyModule,
    (v) => typeof v === "object" && v !== null && "newPlot" in v,
  );
  return { default: createPlotlyComponent(Plotly) };
}

const Plot = lazy(loadPlotComponent);

function PlotFallback() {
  return (
    <div className="flex items-center justify-center rounded-md border border-border bg-muted p-8 text-sm text-muted-foreground">
      Loading chart...
    </div>
  );
}

export default function LazyPlot(props: LazyPlotProps) {
  return (
    <Suspense fallback={<PlotFallback />}>
      <Plot {...props} />
    </Suspense>
  );
}
