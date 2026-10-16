import { useState } from "react";
import { useGetTunerApiTunerGet } from "@/api/generated/tuner/tuner";
import { formatApiErrorDetail } from "@/components/discovery/apiErrorDetail";
import { CandidateJourney } from "@/components/tuner/CandidateJourney";
import { CandidatesTable } from "@/components/tuner/CandidatesTable";
import { when } from "@/components/tuner/format";
import { QueueStrategy } from "@/components/tuner/QueueStrategy";
import { TunerBadge } from "@/components/tuner/TunerBadges";
import { LoadingSpinner } from "@/components/ui/LoadingSpinner";

export function TunerPage() {
  const query = useGetTunerApiTunerGet({ query: { refetchInterval: 10_000 } });
  const [selected, setSelected] = useState<number | null>(null);
  const data = query.data?.status === 200 ? query.data.data : null;

  if (query.isLoading) return <LoadingSpinner />;
  if (!data) {
    return (
      <p className="text-sm text-loss">
        Couldn't read the tuner: {formatApiErrorDetail(query.error) || "backend unavailable"}
      </p>
    );
  }
  const s = data.settings;
  const active =
    selected ?? data.current?.root_strategy_id ?? data.candidates[0]?.root_strategy_id ?? null;
  const settled = data.candidates.filter((c) => c.status === "settled");

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <section className="rounded-xl border border-border/60 bg-card/40 p-5 backdrop-blur-sm">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="max-w-2xl space-y-1.5 text-sm text-muted-foreground">
            <p>
              Each strategy is tested long-only on its own market in the bull run, then on bear
              markets, then on other markets. A losing market sends its results to the AI for new
              parameters (a new strategy version, up to {s.max_rounds} rounds); a winning stage
              widens to the next. No entry is fine: a market it stays out of doesn't count against
              it.
            </p>
            <p>
              Pass: return &gt; {(s.min_return * 100).toFixed(0)}% and profit factor ≥{" "}
              {s.min_profit_factor} in every market it traded, ≥ {s.min_total_trades} trades in
              total. Settled strategies start an Alpaca paper session
              {s.auto_paper ? "" : " (auto paper is off)"}.
            </p>
          </div>
          <div className="space-y-1 text-right text-xs text-muted-foreground">
            {data.current ? (
              <div className="flex items-center justify-end gap-2">
                <TunerBadge value="running" /> strategy #{data.current.strategy_id} ·{" "}
                {data.current.stage} stage
              </div>
            ) : (
              <div>idle</div>
            )}
            <div>updated {when(data.updated_at)}</div>
            <div>{settled.length} settled</div>
          </div>
        </div>
        <div className="mt-4">
          <QueueStrategy timeframes={s.timeframes} />
        </div>
      </section>

      <section className="rounded-xl border border-border/60 bg-card/40 p-5 backdrop-blur-sm">
        <CandidatesTable
          candidates={data.candidates}
          currentId={data.current?.root_strategy_id ?? null}
          selected={active}
          onSelect={setSelected}
        />
      </section>

      {active != null && <CandidateJourney rootStrategyId={active} />}
    </div>
  );
}
