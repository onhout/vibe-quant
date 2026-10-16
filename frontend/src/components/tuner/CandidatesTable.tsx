import type { TunerCandidateSummary } from "@/api/generated/models";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { when } from "./format";
import { TunerBadge } from "./TunerBadges";

interface Props {
  candidates: TunerCandidateSummary[];
  currentId: number | null;
  selected: number | null;
  onSelect: (rootStrategyId: number) => void;
}

export function CandidatesTable({ candidates, currentId, selected, onSelect }: Props) {
  if (candidates.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-muted-foreground">
        No strategy has been tuned yet. Queue one above, or let the tuner pick them up.
      </p>
    );
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Strategy</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Stage</TableHead>
          <TableHead>Home market</TableHead>
          <TableHead className="text-right">AI rounds</TableHead>
          <TableHead>Latest</TableHead>
          <TableHead>Updated</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {candidates.map((c) => (
          <TableRow
            key={c.root_strategy_id}
            className={selected === c.root_strategy_id ? "bg-primary/5" : "cursor-pointer"}
            onClick={() => onSelect(c.root_strategy_id)}
          >
            <TableCell>
              <div className="font-mono text-xs font-semibold">{c.root_name}</div>
              <div className="text-[10px] text-muted-foreground">
                #{c.root_strategy_id} · {c.timeframe ?? "?"}
                {c.current_strategy_id !== c.root_strategy_id && ` · now #${c.current_strategy_id}`}
              </div>
            </TableCell>
            <TableCell>
              <TunerBadge value={currentId === c.root_strategy_id ? "running" : c.status} />
            </TableCell>
            <TableCell>
              <TunerBadge value={c.stage} />
            </TableCell>
            <TableCell className="font-mono text-xs">{c.symbols.join(", ")}</TableCell>
            <TableCell className="text-right font-mono text-xs">{c.round}</TableCell>
            <TableCell
              className="max-w-xs truncate text-xs text-muted-foreground"
              title={c.verdict ?? c.last_reason ?? ""}
            >
              {c.verdict ?? c.last_reason ?? "--"}
            </TableCell>
            <TableCell className="text-xs text-muted-foreground">
              {when(c.settled_at ?? c.finished_at ?? c.created_at)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
