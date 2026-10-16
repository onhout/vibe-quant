import { Link } from "@tanstack/react-router";
import type { TunerAttempt, TunerWindowResult } from "@/api/generated/models";
import { useGetCandidateApiTunerCandidatesRootStrategyIdGet } from "@/api/generated/tuner/tuner";
import { LoadingSpinner } from "@/components/ui/LoadingSpinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { dd, num, pct, when } from "./format";
import { TunerBadge } from "./TunerBadges";

function WindowsTable({ windows }: { windows: TunerWindowResult[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Market</TableHead>
          <TableHead>Symbols</TableHead>
          <TableHead>Dates</TableHead>
          <TableHead className="text-right">Return</TableHead>
          <TableHead className="text-right">PF</TableHead>
          <TableHead className="text-right">Trades</TableHead>
          <TableHead className="text-right">Max DD</TableHead>
          <TableHead>Result</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {windows.map((w) => (
          <TableRow key={w.label}>
            <TableCell>
              <div className="flex items-center gap-2">
                <TunerBadge value={w.stage} />
                <span className="font-mono text-xs">{w.label}</span>
              </div>
              {w.note && <div className="mt-0.5 text-[10px] text-muted-foreground">{w.note}</div>}
            </TableCell>
            <TableCell className="font-mono text-xs">{w.symbols.join(", ")}</TableCell>
            <TableCell className="whitespace-nowrap font-mono text-[11px] text-muted-foreground">
              {w.start} → {w.end}
            </TableCell>
            <TableCell
              className={cn(
                "text-right font-mono text-xs",
                (w.total_return ?? 0) > 0 && "text-profit",
                (w.total_return ?? 0) < 0 && "text-loss",
              )}
            >
              {pct(w.total_return)}
            </TableCell>
            <TableCell className="text-right font-mono text-xs">{num(w.profit_factor)}</TableCell>
            <TableCell className="text-right font-mono text-xs">{w.trades ?? "--"}</TableCell>
            <TableCell className="text-right font-mono text-xs">{dd(w.max_drawdown)}</TableCell>
            <TableCell>
              <div className="flex items-center gap-2">
                <TunerBadge value={w.verdict ?? "running"} />
                {w.run_id != null && (
                  <Link
                    to="/results/$runId"
                    params={{ runId: String(w.run_id) }}
                    className="text-[11px] text-primary hover:underline"
                  >
                    run {w.run_id}
                  </Link>
                )}
              </div>
              {(w.error || w.run_note) && (
                <div
                  className="mt-0.5 max-w-[16rem] truncate text-[10px] text-muted-foreground"
                  title={w.error ?? w.run_note ?? ""}
                >
                  {w.error ?? w.run_note}
                </div>
              )}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function ParamChanges({ attempt }: { attempt: TunerAttempt }) {
  const suggestion = (attempt.ai?.suggestion ?? {}) as Record<string, unknown>;
  const shown = Object.entries(suggestion).filter(([k]) => k !== "reasoning");
  if (shown.length === 0) return null;
  return (
    <pre className="mt-2 max-h-56 overflow-auto rounded-md bg-muted/30 p-3 font-mono text-[11px] leading-relaxed">
      {JSON.stringify(Object.fromEntries(shown), null, 2)}
    </pre>
  );
}

function AttemptCard({ attempt }: { attempt: TunerAttempt }) {
  return (
    <div className="rounded-xl border border-border/60 bg-card/40 p-4 backdrop-blur-sm">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs text-muted-foreground">#{attempt.n}</span>
        <Link
          to="/strategies/$strategyId"
          params={{ strategyId: String(attempt.strategy_id) }}
          className="font-mono text-sm font-semibold hover:underline"
        >
          {attempt.strategy_name}
        </Link>
        <TunerBadge value={attempt.stage} />
        {attempt.passed != null && <TunerBadge value={attempt.passed ? "pass" : "fail"} />}
        {attempt.reason && <span className="text-xs text-muted-foreground">{attempt.reason}</span>}
        <span className="ml-auto text-[10px] text-muted-foreground">
          {when(attempt.started_at)}
        </span>
      </div>
      <WindowsTable windows={attempt.windows} />
      {attempt.ai && (
        <div className="mt-4 rounded-lg border border-violet-500/20 bg-violet-500/5 p-3">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-violet-300">
            AI parameter change
            {attempt.ai.new_strategy_id != null && (
              <>
                {" → "}
                <Link
                  to="/strategies/$strategyId"
                  params={{ strategyId: String(attempt.ai.new_strategy_id) }}
                  className="normal-case text-primary hover:underline"
                >
                  strategy #{attempt.ai.new_strategy_id}
                </Link>
              </>
            )}
          </p>
          {attempt.ai.error ? (
            <p className="mt-1 text-xs text-loss">{attempt.ai.error}</p>
          ) : (
            <p className="mt-1 text-sm text-foreground/90">{attempt.ai.reasoning}</p>
          )}
          <ParamChanges attempt={attempt} />
          {attempt.ai.notes && attempt.ai.notes.length > 0 && (
            <ul className="mt-2 list-disc pl-4 text-[11px] text-muted-foreground">
              {attempt.ai.notes.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

export function CandidateJourney({ rootStrategyId }: { rootStrategyId: number }) {
  const query = useGetCandidateApiTunerCandidatesRootStrategyIdGet(rootStrategyId, {
    query: { refetchInterval: 10_000 },
  });
  const candidate = query.data?.status === 200 ? query.data.data : null;
  if (query.isLoading) return <LoadingSpinner />;
  if (!candidate)
    return <p className="text-sm text-muted-foreground">No journey for this strategy yet.</p>;
  const attempts = [...candidate.attempt_list].reverse();
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="font-mono text-base font-semibold">{candidate.root_name}</h2>
        <TunerBadge value={candidate.status} />
        {candidate.verdict && (
          <span className="text-sm text-muted-foreground">{candidate.verdict}</span>
        )}
        {candidate.paper && (
          <span className="text-xs text-muted-foreground">
            paper: {candidate.paper.state}
            {candidate.paper.note ? ` (${candidate.paper.note})` : ""}
            {candidate.paper.error ? ` (${candidate.paper.error})` : ""}
          </span>
        )}
      </div>
      {attempts.map((a) => (
        <AttemptCard key={a.n} attempt={a} />
      ))}
    </div>
  );
}
