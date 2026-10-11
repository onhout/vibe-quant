import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { DslConfig } from "./types";

interface ValidationPanelProps {
  config: DslConfig;
  /** Canonical StrategyDSL strategy: symbols are chosen when launching a backtest. */
  canonical?: boolean;
}

interface CheckItem {
  label: string;
  passed: boolean;
}

interface Warning {
  message: string;
}

/** Condition counts across the generic and the long/short lists (canonical uses long/short). */
function conditionCounts(config: DslConfig): { entry: number; exit: number } {
  const c = config.conditions;
  return {
    entry: c.entry.length + (c.long_entry?.length ?? 0) + (c.short_entry?.length ?? 0),
    exit: c.exit.length + (c.long_exit?.length ?? 0) + (c.short_exit?.length ?? 0),
  };
}

function computeChecks(config: DslConfig, canonical: boolean): CheckItem[] {
  const counts = conditionCounts(config);
  return [
    { label: "Has entry conditions", passed: counts.entry > 0 },
    { label: "Has exit conditions", passed: counts.exit > 0 },
    { label: "Has stop loss", passed: config.risk.stop_loss.value > 0 },
    { label: "Has at least 2 indicators", passed: config.indicators.length >= 2 },
    canonical
      ? { label: "Symbols chosen at launch", passed: true }
      : { label: "Has symbols configured", passed: config.general.symbols.length > 0 },
  ];
}

function computeWarnings(config: DslConfig, canonical: boolean): Warning[] {
  const warnings: Warning[] = [];
  const counts = conditionCounts(config);
  if (counts.exit === 0) {
    warnings.push({ message: "No exit conditions defined" });
  }
  if (config.indicators.length === 0) {
    warnings.push({ message: "No indicators configured" });
  } else if (config.indicators.length === 1) {
    warnings.push({ message: "Only 1 indicator -- consider adding more" });
  }
  if (config.risk.stop_loss.value === 0) {
    warnings.push({ message: "No stop loss configured" });
  }
  if (counts.entry === 0) {
    warnings.push({ message: "No entry conditions defined" });
  }
  if (!canonical && config.general.symbols.length === 0) {
    warnings.push({ message: "No symbols configured" });
  }
  if (
    config.risk.stop_loss.type.startsWith("atr") &&
    !config.indicators.some((i) => i.type === "ATR")
  ) {
    warnings.push({ message: "ATR stop loss requires an ATR indicator" });
  }
  return warnings;
}

function computeComplexity(config: DslConfig): number {
  const counts = conditionCounts(config);
  return config.indicators.length + counts.entry + counts.exit;
}

export function ValidationPanel({ config, canonical = false }: ValidationPanelProps) {
  const checks = computeChecks(config, canonical);
  const warnings = computeWarnings(config, canonical);
  const complexity = computeComplexity(config);
  const counts = conditionCounts(config);
  const conditionCount = counts.entry + counts.exit;
  const passedCount = checks.filter((c) => c.passed).length;

  let complexityLabel = "Low";
  let complexityColor = "bg-green-500/10 text-green-600";
  if (complexity >= 8) {
    complexityLabel = "High";
    complexityColor = "bg-red-500/10 text-red-600";
  } else if (complexity >= 4) {
    complexityLabel = "Medium";
    complexityColor = "bg-yellow-500/10 text-yellow-600";
  }

  return (
    <div className="grid gap-4 md:grid-cols-3">
      {/* Complexity */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Complexity</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-2">
            <span className="text-2xl font-bold">{complexity}</span>
            <Badge className={complexityColor}>{complexityLabel}</Badge>
          </div>
          <p className="mt-1 text-[10px] text-muted-foreground">
            {config.indicators.length} indicators + {conditionCount} conditions
          </p>
        </CardContent>
      </Card>

      {/* Readiness Checklist */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">
            Readiness ({passedCount}/{checks.length})
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-1">
          {checks.map((check) => (
            <div key={check.label} className="flex items-center gap-2 text-xs">
              <span className={check.passed ? "text-green-500" : "text-red-500"}>
                {check.passed ? "\u2713" : "\u2717"}
              </span>
              <span className={check.passed ? "text-foreground" : "text-muted-foreground"}>
                {check.label}
              </span>
            </div>
          ))}
        </CardContent>
      </Card>

      {/* Warnings */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Warnings ({warnings.length})</CardTitle>
        </CardHeader>
        <CardContent>
          {warnings.length === 0 ? (
            <p className="text-xs text-green-500">No warnings</p>
          ) : (
            <ul className="space-y-1">
              {warnings.map((w) => (
                <li key={w.message} className="text-xs text-yellow-600">
                  {w.message}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
