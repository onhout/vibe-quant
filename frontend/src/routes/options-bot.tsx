import { useState } from "react";
import {
  useGetBotStatusApiOptionsBotStatusGet,
  useListProposalsApiOptionsBotProposalsGet,
} from "@/api/generated/options-bot/options-bot";
import { formatApiErrorDetail } from "@/components/discovery/apiErrorDetail";
import { BotControl } from "@/components/options-bot/BotControl";
import { shortTime } from "@/components/options-bot/format";
import { HistoryPanel } from "@/components/options-bot/HistoryPanel";
import { LogsPanel } from "@/components/options-bot/LogsPanel";
import { ProposalDetailDialog } from "@/components/options-bot/ProposalDetailDialog";
import { ProposalsTable } from "@/components/options-bot/ProposalsTable";
import { ReportsPanel } from "@/components/options-bot/ReportsPanel";
import { LoadingSpinner } from "@/components/ui/LoadingSpinner";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

function TodayPanel({ date, onOpen }: { date: string; onOpen: (proposalId: string) => void }) {
  const query = useListProposalsApiOptionsBotProposalsGet(
    { date },
    { query: { refetchInterval: 15_000 } },
  );
  const proposals = query.data?.status === 200 ? query.data.data.proposals : [];
  if (query.isLoading) return <LoadingSpinner />;
  return <ProposalsTable date={date} proposals={proposals} onOpen={onOpen} />;
}

export function OptionsBotPage() {
  const statusQuery = useGetBotStatusApiOptionsBotStatusGet({ query: { refetchInterval: 5_000 } });
  const [openId, setOpenId] = useState<string | null>(null);
  const status = statusQuery.data?.status === 200 ? statusQuery.data.data : null;

  if (statusQuery.isLoading) return <LoadingSpinner />;
  if (!status) {
    return (
      <p className="text-sm text-loss">
        Couldn't read the bot's status:{" "}
        {formatApiErrorDetail(statusQuery.error) || "backend unavailable"}
      </p>
    );
  }

  return (
    <div className="mx-auto max-w-6xl space-y-8">
      <BotControl status={status} />

      <Tabs defaultValue="today">
        <TabsList>
          <TabsTrigger value="today">Today's proposals</TabsTrigger>
          <TabsTrigger value="history">History</TabsTrigger>
          <TabsTrigger value="reports">Daily reports</TabsTrigger>
          <TabsTrigger value="commands">Commands</TabsTrigger>
          <TabsTrigger value="logs">Logs</TabsTrigger>
        </TabsList>
        <TabsContent value="today" className="mt-4">
          <div className="rounded-xl border border-border/60 bg-card/40 p-5 backdrop-blur-sm">
            <TodayPanel date={status.today} onOpen={setOpenId} />
          </div>
        </TabsContent>
        <TabsContent value="history" className="mt-4">
          <HistoryPanel onOpen={setOpenId} />
        </TabsContent>
        <TabsContent value="reports" className="mt-4">
          <ReportsPanel />
        </TabsContent>
        <TabsContent value="commands" className="mt-4">
          {status.commands.length === 0 ? (
            <p className="text-xs text-muted-foreground">No commands sent yet.</p>
          ) : (
            <ul className="space-y-1 text-xs">
              {status.commands.map((c) => (
                <li key={c.id} className="flex items-center gap-3">
                  <span className="shrink-0 font-mono text-muted-foreground">
                    {shortTime(c.created_at)}
                  </span>
                  <span className="shrink-0 font-medium">
                    {(c.command ?? "").replace("_", " ")}
                  </span>
                  <span className="truncate font-mono text-muted-foreground">
                    {typeof c.args?.proposal_id === "string" ? c.args.proposal_id : ""}
                  </span>
                  <StatusBadge status={c.status} />
                  {c.result && <span className="truncate text-muted-foreground">{c.result}</span>}
                </li>
              ))}
            </ul>
          )}
        </TabsContent>
        <TabsContent value="logs" className="mt-4">
          <LogsPanel />
        </TabsContent>
      </Tabs>

      <ProposalDetailDialog proposalId={openId} onClose={() => setOpenId(null)} />
    </div>
  );
}
