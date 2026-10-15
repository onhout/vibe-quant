import { useGetSweepsApiResultsRunsRunIdSweepsGet } from "@/api/generated/results/results";
import { runExitTimingNote } from "@/lib/liquidationDisplay";

interface ExitTimingNoteProps {
  runId: number;
}

/**
 * D1b (job 20261015-small-beads, E5): the run-level exit-timing note beside the
 * liquidation chip. The counts live on the sweep rows, so the note is shown only
 * when the run has exactly one row with valid counts; a multi-row sweep carries
 * them in the "1m exits" column instead. Renders nothing otherwise.
 */
export function ExitTimingNote({ runId }: ExitTimingNoteProps) {
  const query = useGetSweepsApiResultsRunsRunIdSweepsGet(runId);
  const rows = query.data?.status === 200 ? query.data.data : null;

  const note = rows == null ? null : runExitTimingNote(rows);
  if (note == null) return null;

  return <span className="font-mono text-[11px] text-muted-foreground">{note}</span>;
}
