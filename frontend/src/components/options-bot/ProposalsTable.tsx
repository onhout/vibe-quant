import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import type { ProposalSummary } from "@/api/generated/models";
import {
  getGetBotStatusApiOptionsBotStatusGetQueryKey,
  getListProposalsApiOptionsBotProposalsGetQueryKey,
  useSubmitBotCommandApiOptionsBotCommandsPost,
} from "@/api/generated/options-bot/options-bot";
import { formatApiErrorDetail } from "@/components/discovery/apiErrorDetail";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { asRecord, contractLabel, fixed, num, pct, str, usd } from "./format";

const TRADE_STYLE: Record<string, string> = {
  OPEN: "bg-sky-500/15 text-sky-300 border-sky-500/25",
  ENTRY_PENDING: "bg-sky-500/15 text-sky-300 border-sky-500/25",
  ENTRY_WORKING: "bg-sky-500/15 text-sky-300 border-sky-500/25",
  EXITING: "bg-sky-500/15 text-sky-300 border-sky-500/25",
  AWAITING_APPROVAL: "bg-amber-500/15 text-amber-300 border-amber-500/25",
  CLOSED: "bg-violet-500/15 text-violet-300 border-violet-500/25",
};
const FALLBACK = "bg-muted text-muted-foreground border-border";

export function TradeCell({ record }: { record: unknown }) {
  const r = asRecord(record);
  const status = str(r?.status);
  if (!status) return <span className="text-muted-foreground/50">—</span>;
  const ret = num(r?.return_pct);
  return (
    <div className="flex items-center gap-1.5">
      <Badge
        variant="outline"
        className={cn(TRADE_STYLE[status] ?? FALLBACK, "font-mono text-[10px]")}
      >
        {status}
      </Badge>
      {r?.mode === "live" && (
        <Badge
          variant="outline"
          className="border-red-500/25 bg-red-500/15 font-mono text-[10px] text-red-300"
        >
          LIVE
        </Badge>
      )}
      {ret != null && (
        <span className={cn("font-mono text-xs", ret >= 0 ? "text-profit" : "text-loss")}>
          {pct(ret)}
        </span>
      )}
    </div>
  );
}

export function DecisionBadge({ p }: { p: ProposalSummary }) {
  return (
    <Badge
      variant="outline"
      className={cn(
        "font-mono text-[10px]",
        p.trade_eligible
          ? "border-emerald-500/25 bg-emerald-500/15 text-emerald-300"
          : "border-border bg-muted text-muted-foreground",
      )}
    >
      {p.decision ?? p.status}
    </Badge>
  );
}

interface ProposalsTableProps {
  date: string;
  proposals: ProposalSummary[];
  onOpen: (proposalId: string) => void;
  actions?: boolean;
}

export function ProposalsTable({ date, proposals, onOpen, actions = true }: ProposalsTableProps) {
  const queryClient = useQueryClient();
  const commandMutation = useSubmitBotCommandApiOptionsBotCommandsPost();

  const send = (command: "trade" | "skip" | "close", p: ProposalSummary) =>
    commandMutation.mutate(
      { data: { command, proposal_id: p.proposal_id } },
      {
        onSuccess: () => {
          toast.success(
            `${command === "trade" ? "Trade" : command === "skip" ? "Skip" : "Close"} sent for ${p.ticker}`,
            {
              description: "The bot picks it up within a few seconds.",
            },
          );
          void queryClient.invalidateQueries({
            queryKey: getListProposalsApiOptionsBotProposalsGetQueryKey({ date }),
          });
          void queryClient.invalidateQueries({
            queryKey: getGetBotStatusApiOptionsBotStatusGetQueryKey(),
          });
        },
        onError: (err: unknown) =>
          toast.error("Command refused", {
            description: formatApiErrorDetail(err) || "Request failed",
          }),
      },
    );

  if (proposals.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        No proposals for {date}. On trading days the bot researches Reddit at 8:30 ET and posts
        proposals at 9:40 ET.
      </p>
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Ticker</TableHead>
          <TableHead>Contract</TableHead>
          <TableHead>Setup</TableHead>
          <TableHead className="text-right">Score</TableHead>
          <TableHead className="text-right">Limit × qty</TableHead>
          <TableHead>Decision</TableHead>
          <TableHead>Trade</TableHead>
          <TableHead>Shadow</TableHead>
          {actions && <TableHead />}
        </TableRow>
      </TableHeader>
      <TableBody>
        {proposals.map((p) => {
          const tradeStatus = str(asRecord(p.trade)?.status);
          const working = tradeStatus === "ENTRY_PENDING" || tradeStatus === "ENTRY_WORKING";
          const canTrade = !tradeStatus || tradeStatus === "AWAITING_APPROVAL";
          return (
            <TableRow
              key={p.proposal_id}
              className="cursor-pointer"
              onClick={() => onOpen(p.proposal_id)}
            >
              <TableCell className="font-mono font-semibold">{p.ticker}</TableCell>
              <TableCell className="font-mono text-xs">{contractLabel(p)}</TableCell>
              <TableCell className="text-xs">{p.selected_setup ?? "—"}</TableCell>
              <TableCell className="text-right font-mono">{fixed(p.entry_score, 0)}</TableCell>
              <TableCell className="text-right font-mono">
                {usd(p.proposed_limit)} × {p.proposed_quantity ?? "—"}
              </TableCell>
              <TableCell>
                <DecisionBadge p={p} />
              </TableCell>
              <TableCell>
                <TradeCell record={p.trade} />
              </TableCell>
              <TableCell>
                <TradeCell record={p.shadow} />
              </TableCell>
              {actions && (
                <TableCell onClick={(e) => e.stopPropagation()}>
                  <div className="flex justify-end gap-1.5">
                    {canTrade && p.status !== "SKIPPED" && (
                      <Button
                        size="xs"
                        disabled={commandMutation.isPending}
                        onClick={() => send("trade", p)}
                      >
                        Trade it
                      </Button>
                    )}
                    {(working || canTrade) && p.status !== "SKIPPED" && (
                      <Button
                        size="xs"
                        variant="outline"
                        disabled={commandMutation.isPending}
                        onClick={() => send("skip", p)}
                      >
                        Skip
                      </Button>
                    )}
                    {tradeStatus === "OPEN" && (
                      <Button
                        size="xs"
                        variant="destructive"
                        disabled={commandMutation.isPending}
                        onClick={() => send("close", p)}
                      >
                        Close
                      </Button>
                    )}
                  </div>
                </TableCell>
              )}
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
