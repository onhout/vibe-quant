import {
  useGetRunMetaApiResultsRunsRunIdMetaGet,
  useGetRunSummaryApiResultsRunsRunIdGet,
} from "@/api/generated/results/results";
import { Badge } from "@/components/ui/badge";
import { chipLabel } from "@/lib/liquidationDisplay";

interface LiquidationChipProps {
  runId: number;
}

/**
 * D3: the run-level liquidation chip. The mode/leverage come from the run's
 * stored parameters (RunMeta), the hit count from the result's machine notes
 * (RunSummary) — both already fetched elsewhere on this page. Renders nothing
 * when the run is off, absent or unreadable.
 */
export function LiquidationChip({ runId }: LiquidationChipProps) {
  const metaQuery = useGetRunMetaApiResultsRunsRunIdMetaGet(runId);
  const summaryQuery = useGetRunSummaryApiResultsRunsRunIdGet(runId);

  const params = metaQuery.data?.status === 200 ? metaQuery.data.data.parameters : undefined;
  const notes = summaryQuery.data?.status === 200 ? summaryQuery.data.data.notes : null;

  const label = chipLabel(params, notes);
  if (label == null) return null;

  return (
    <Badge variant="outline" className="font-mono text-[11px]">
      {label}
    </Badge>
  );
}
