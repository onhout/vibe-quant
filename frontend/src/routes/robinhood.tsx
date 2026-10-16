import { useState } from "react";
import {
  contractLabel,
  Panel,
  Pill,
  ProposalDialog,
  Stat,
  TradeCell,
  tone,
} from "@/components/robinhood/shared";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { LoadingSpinner } from "@/components/ui/LoadingSpinner";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  errorText,
  num,
  type Proposal,
  pct,
  type StatusResponse,
  useAccount,
  useBotAction,
  useBotLogs,
  useBotStatus,
  usePaper,
  usePaperReset,
  useProposals,
  useSendCommand,
  useUpdateSettings,
  usd,
  when,
} from "@/hooks/useRobinhood";
import { cn } from "@/lib/utils";

function Segmented<T extends string>({
  value,
  options,
  onChange,
  disabled,
}: {
  value: T;
  options: { value: T; label: string; className?: string }[];
  onChange: (value: T) => void;
  disabled?: boolean;
}) {
  return (
    <div className="inline-flex rounded-md border border-border/60 p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          disabled={disabled}
          onClick={() => o.value !== value && onChange(o.value)}
          className={cn(
            "rounded px-3 py-1 text-xs font-semibold transition-colors disabled:opacity-50",
            o.value === value ? (o.className ?? "bg-primary text-primary-foreground") : "text-muted-foreground hover:text-foreground",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function BotPanel({ status }: { status: StatusResponse }) {
  const botAction = useBotAction();
  const settings = useUpdateSettings();
  const command = useSendCommand();
  const running = status.service.active === "active";
  const starting = status.service.active === "activating";
  const bot = status.bot;
  const live = status.settings.trading_mode === "live";

  return (
    <Panel
      title="Bot"
      action={
        <div className="flex items-center gap-2">
          <Pill color={running ? "green" : starting ? "amber" : "gray"}>
            {running ? (status.bot_stale ? "starting…" : "running") : (status.service.active ?? "stopped")}
          </Pill>
          {running ? (
            <Button size="sm" variant="outline" disabled={botAction.isPending} onClick={() => botAction.mutate("stop")}>
              Stop
            </Button>
          ) : (
            <Button size="sm" disabled={botAction.isPending} onClick={() => botAction.mutate("start")}>
              Start
            </Button>
          )}
        </div>
      }
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-x-8 gap-y-3">
          <div className="space-y-1">
            <p className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">Mode</p>
            <Segmented
              value={status.settings.trading_mode}
              disabled={settings.isPending}
              onChange={(trading_mode) => settings.mutate({ trading_mode })}
              options={[
                { value: "paper", label: "Paper" },
                { value: "live", label: "Live", className: "bg-red-600 text-white" },
              ]}
            />
          </div>
          <div className="space-y-1">
            <p className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">Entries</p>
            <Segmented
              value={status.settings.approval_mode}
              disabled={settings.isPending}
              onChange={(approval_mode) => settings.mutate({ approval_mode })}
              options={[
                { value: "auto", label: "Bot buys" },
                { value: "manual", label: "I approve" },
              ]}
            />
          </div>
          <div className="space-y-1">
            <p className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">This week</p>
            <p className="font-mono text-sm">
              {status.week_new_positions} / 1 new position
            </p>
          </div>
        </div>
        {live && (
          <p className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
            LIVE: the bot places real orders on Robinhood ••••7731.
          </p>
        )}
        <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs sm:grid-cols-4">
          <span className="text-muted-foreground">Today</span>
          <span className="font-mono">
            {status.today} {bot && !bot.trading_day ? "(market closed)" : ""}
          </span>
          <span className="text-muted-foreground">Research</span>
          <span className="font-mono">{bot?.researched_today ? "done" : (bot?.schedule.research ?? "").slice(11, 16) || "08:30"}</span>
          <span className="text-muted-foreground">Proposals</span>
          <span className="font-mono">{bot?.frozen_today ? "posted" : (bot?.schedule.freeze ?? "").slice(11, 16) || "09:40"}</span>
          <span className="text-muted-foreground">Next trading day</span>
          <span className="font-mono">{bot?.next_trading_day ?? "—"}</span>
          {bot?.busy.length ? (
            <>
              <span className="text-muted-foreground">Working on</span>
              <span className="font-mono">{bot.busy.join(", ")}</span>
            </>
          ) : null}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={!running || command.isPending}
            onClick={() => command.mutate({ command: "research_now" })}
          >
            Research now
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={!running || command.isPending}
            onClick={() => command.mutate({ command: "report" })}
          >
            Write today's report
          </Button>
        </div>
      </div>
    </Panel>
  );
}

function AccountPanel() {
  const account = useAccount();
  const a = account.data;
  return (
    <Panel
      title={`Robinhood ${a?.account ?? "••••7731"}`}
      action={
        <span className="text-[10px] text-muted-foreground">
          {account.isFetching ? "updating…" : a ? `as of ${when(a.fetched_at)}` : ""}
        </span>
      }
    >
      {account.isLoading ? (
        <LoadingSpinner />
      ) : account.isError || !a ? (
        <p className="text-xs text-loss">{errorText(account.error)}</p>
      ) : (
        <div className="space-y-4">
          <div className="grid grid-cols-3 gap-4">
            <Stat label="Total" value={usd(a.portfolio.total_value)} />
            <Stat label="Cash" value={usd(a.portfolio.cash)} />
            <Stat label="Buying power" value={usd(a.portfolio.buying_power)} />
          </div>
          {a.positions.length + a.option_positions.length > 0 && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Holding</TableHead>
                  <TableHead className="text-right">Qty</TableHead>
                  <TableHead className="text-right">Avg</TableHead>
                  <TableHead className="text-right">Last</TableHead>
                  <TableHead className="text-right">P&amp;L</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {a.positions.map((p) => {
                  const pnl =
                    p.last_price !== null && p.average_price !== null
                      ? (p.last_price - p.average_price) * p.quantity
                      : null;
                  return (
                    <TableRow key={p.symbol}>
                      <TableCell className="font-mono">{p.symbol}</TableCell>
                      <TableCell className="text-right font-mono">{p.quantity}</TableCell>
                      <TableCell className="text-right font-mono">{usd(p.average_price)}</TableCell>
                      <TableCell className="text-right font-mono">{usd(p.last_price)}</TableCell>
                      <TableCell className={cn("text-right font-mono", tone(pnl) === "profit" ? "text-profit" : tone(pnl) === "loss" ? "text-loss" : "")}>
                        {usd(pnl)}
                      </TableCell>
                    </TableRow>
                  );
                })}
                {a.option_positions.map((p) => (
                  <TableRow key={p.symbol}>
                    <TableCell className="font-mono">{p.symbol}</TableCell>
                    <TableCell className="text-right font-mono">{p.quantity}</TableCell>
                    <TableCell className="text-right font-mono">{usd(p.average_price)}</TableCell>
                    <TableCell className="text-right font-mono">—</TableCell>
                    <TableCell className="text-right font-mono">—</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          {a.orders.length > 0 && (
            <details className="text-xs">
              <summary className="cursor-pointer text-muted-foreground">Orders, last 14 days ({a.orders.length})</summary>
              <Table>
                <TableBody>
                  {a.orders.map((o) => (
                    <TableRow key={o.id}>
                      <TableCell className="font-mono text-muted-foreground">{when(o.created_at)}</TableCell>
                      <TableCell className="font-mono">{o.symbol}</TableCell>
                      <TableCell>{o.side}</TableCell>
                      <TableCell className="font-mono">
                        {o.filled ?? 0}/{o.quantity ?? "?"} @ {usd(o.average_price ?? o.price)}
                      </TableCell>
                      <TableCell>
                        <Pill>{o.state}</Pill>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </details>
          )}
        </div>
      )}
    </Panel>
  );
}

function PaperPanel({ botRunning }: { botRunning: boolean }) {
  const paper = usePaper();
  const reset = usePaperReset();
  const [balance, setBalance] = useState("1000");
  const p = paper.data;
  const start = Number.parseFloat((p?.starting_balances?.[0] ?? "").split(" ")[0] ?? "");
  const positions = (p?.positions ?? []) as { instrument_id?: string; side?: string; quantity?: string; avg_px_open?: string }[];
  return (
    <Panel title="Paper account">
      {!p?.exists ? (
        <p className="text-xs text-muted-foreground">No paper account yet: it's created when the bot first runs in Paper mode.</p>
      ) : (
        <div className="space-y-4">
          <div className="grid grid-cols-3 gap-4">
            <Stat label="Cash" value={usd(p.cash)} />
            <Stat label="Started with" value={Number.isFinite(start) ? usd(start, 0) : "—"} sub={when(p.created_at)} />
            <Stat
              label="Change"
              value={p.cash != null && Number.isFinite(start) && positions.length === 0 ? usd(p.cash - start) : "—"}
              tone={p.cash != null && Number.isFinite(start) ? tone(p.cash - start) : undefined}
            />
          </div>
          {positions.length > 0 && (
            <ul className="space-y-1 font-mono text-xs">
              {positions.map((pos) => (
                <li key={pos.instrument_id}>
                  {pos.side} {pos.quantity} {pos.instrument_id} @ {pos.avg_px_open}
                </li>
              ))}
            </ul>
          )}
          <div className="flex items-center gap-2">
            <Input
              value={balance}
              onChange={(e) => setBalance(e.target.value)}
              className="h-8 w-28 font-mono"
              inputMode="decimal"
            />
            <Button
              size="sm"
              variant="outline"
              disabled={botRunning || reset.isPending}
              title={botRunning ? "Stop the bot first" : undefined}
              onClick={() => reset.mutate(Number.parseFloat(balance))}
            >
              Reset paper account
            </Button>
          </div>
        </div>
      )}
    </Panel>
  );
}

function ProposalsPanel({ onOpen }: { onOpen: (id: string) => void }) {
  const proposals = useProposals();
  const command = useSendCommand();
  const rows = proposals.data?.proposals ?? [];

  const actions = (p: Proposal) => {
    const t = p.trade?.status;
    const open = t === "OPEN";
    const working = t === "ENTRY_PENDING" || t === "ENTRY_WORKING";
    return (
      <div className="flex justify-end gap-1.5" onClick={(e) => e.stopPropagation()}>
        {!t && p.status !== "SKIPPED" && (
          <Button size="xs" onClick={() => command.mutate({ command: "trade", args: { proposal_id: p.proposal_id } })}>
            Trade it
          </Button>
        )}
        {(working || (!t && p.status !== "SKIPPED")) && (
          <Button size="xs" variant="outline" onClick={() => command.mutate({ command: "skip", args: { proposal_id: p.proposal_id } })}>
            Skip
          </Button>
        )}
        {open && (
          <Button size="xs" variant="destructive" onClick={() => command.mutate({ command: "close", args: { proposal_id: p.proposal_id } })}>
            Close
          </Button>
        )}
      </div>
    );
  };

  return (
    <Panel title={`Today's proposals · ${proposals.data?.date ?? ""}`}>
      {proposals.isLoading ? (
        <LoadingSpinner />
      ) : rows.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          None yet. The bot researches Reddit at 8:30 ET and posts proposals at 9:40 ET on trading days.
        </p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Ticker</TableHead>
              <TableHead>Contract</TableHead>
              <TableHead>Setup</TableHead>
              <TableHead className="text-right">Score</TableHead>
              <TableHead className="text-right">Limit</TableHead>
              <TableHead>Decision</TableHead>
              <TableHead>Trade</TableHead>
              <TableHead>Shadow</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((p) => (
              <TableRow key={p.proposal_id} className="cursor-pointer" onClick={() => onOpen(p.proposal_id)}>
                <TableCell className="font-mono font-semibold">
                  {p.ticker}
                  {p.lab && <Pill color="violet" className="ml-1.5">Lab</Pill>}
                </TableCell>
                <TableCell className="font-mono text-xs">{contractLabel(p)}</TableCell>
                <TableCell className="text-xs">{p.selected_setup ?? "—"}</TableCell>
                <TableCell className="text-right font-mono">{num(p.entry_score, 0)}</TableCell>
                <TableCell className="text-right font-mono">{usd(p.proposed_limit)}</TableCell>
                <TableCell>
                  <Pill color={p.trade_eligible ? "green" : "gray"}>{p.decision ?? p.status}</Pill>
                </TableCell>
                <TableCell>
                  <TradeCell record={p.trade} />
                </TableCell>
                <TableCell>
                  <TradeCell record={p.shadow} />
                </TableCell>
                <TableCell>{actions(p)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </Panel>
  );
}

function CommandsPanel({ status }: { status: StatusResponse }) {
  if (!status.commands.length) return null;
  return (
    <Panel title="Recent commands">
      <ul className="space-y-1 text-xs">
        {status.commands.map((c) => (
          <li key={c.id} className="flex gap-3">
            <span className="shrink-0 font-mono text-muted-foreground">{when(c.created_at)}</span>
            <span className="shrink-0 font-medium">{c.command.replace("_", " ")}</span>
            <span className="truncate font-mono text-muted-foreground">
              {(c.args.proposal_id as string | undefined) ?? ""}
            </span>
            <Pill color={c.status === "done" ? "green" : c.status === "failed" ? "red" : "blue"}>{c.status}</Pill>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function LogsPanel() {
  const [unit, setUnit] = useState<"bot" | "lab">("bot");
  const [open, setOpen] = useState(false);
  const logs = useBotLogs(unit, open);
  return (
    <Panel
      title="Logs"
      action={
        <Button size="xs" variant="ghost" onClick={() => setOpen(!open)}>
          {open ? "Hide" : "Show"}
        </Button>
      }
    >
      {open && (
        <Tabs value={unit} onValueChange={(v) => setUnit(v as "bot" | "lab")}>
          <TabsList>
            <TabsTrigger value="bot">Bot</TabsTrigger>
            <TabsTrigger value="lab">Research Lab</TabsTrigger>
          </TabsList>
          <TabsContent value={unit}>
            <pre className="mt-2 max-h-[28rem] overflow-auto whitespace-pre-wrap rounded-md bg-muted/30 p-3 font-mono text-[11px] leading-relaxed">
              {logs.data?.text ?? "loading…"}
            </pre>
          </TabsContent>
        </Tabs>
      )}
    </Panel>
  );
}

export function RobinhoodPage() {
  const status = useBotStatus();
  const [openId, setOpenId] = useState<string | null>(null);

  if (status.isLoading) return <LoadingSpinner />;
  if (status.isError || !status.data) {
    return <p className="text-sm text-loss">Couldn't reach the trading system: {errorText(status.error)}</p>;
  }
  const s = status.data;
  const running = s.service.active === "active" || s.service.active === "activating";

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="grid gap-6 lg:grid-cols-2">
        <BotPanel status={s} />
        <AccountPanel />
      </div>
      <ProposalsPanel onOpen={setOpenId} />
      <div className="grid gap-6 lg:grid-cols-2">
        <PaperPanel botRunning={running} />
        <CommandsPanel status={s} />
      </div>
      <LogsPanel />
      <ProposalDialog id={openId} onClose={() => setOpenId(null)} />
      <p className="text-center text-[10px] text-muted-foreground/50">
        Paper results {pct(null)} · bot status {s.bot?.age_secs != null ? `${s.bot.age_secs}s old` : "n/a"}
      </p>
    </div>
  );
}
