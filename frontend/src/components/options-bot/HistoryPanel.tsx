import type { OutcomeStats } from "@/api/generated/models";
import { useGetProposalHistoryApiOptionsBotHistoryGet } from "@/api/generated/options-bot/options-bot";
import { LoadingSpinner } from "@/components/ui/LoadingSpinner";
import { MetricCard } from "@/components/ui/MetricCard";
import { pct, usd } from "./format";
import { ProposalsTable } from "./ProposalsTable";

function trend(value: number | null | undefined): "up" | "down" | "neutral" {
  if (value == null || value === 0) return "neutral";
  return value > 0 ? "up" : "down";
}

function OutcomeCards({ label, stats }: { label: string; stats: OutcomeStats }) {
  return (
    <>
      <MetricCard
        label={`${label}: closed`}
        value={stats.count}
        subtitle={
          stats.win_rate_pct != null
            ? `${stats.wins} wins · ${stats.win_rate_pct}%`
            : "no closed yet"
        }
      />
      <MetricCard
        label={`${label}: avg return`}
        value={pct(stats.avg_return_pct)}
        trend={trend(stats.avg_return_pct)}
      />
      <MetricCard
        label={`${label}: P&L`}
        value={usd(stats.total_pnl)}
        trend={trend(stats.total_pnl)}
      />
    </>
  );
}

export function HistoryPanel({ onOpen }: { onOpen: (proposalId: string) => void }) {
  const query = useGetProposalHistoryApiOptionsBotHistoryGet(undefined, {
    query: { refetchInterval: 30_000 },
  });
  const history = query.data?.status === 200 ? query.data.data : null;
  if (query.isLoading) return <LoadingSpinner />;
  if (!history) return <p className="text-xs text-muted-foreground">No history yet.</p>;
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-3">
        <OutcomeCards label="Trades" stats={history.trades} />
        <OutcomeCards label="Shadows" stats={history.shadows} />
      </div>
      <p className="text-xs text-muted-foreground">
        {history.proposals} proposals in the ledger. Shadows follow every proposal as if it had been
        bought.
      </p>
      <ProposalsTable date="history" proposals={history.rows} onOpen={onOpen} actions={false} />
    </div>
  );
}
