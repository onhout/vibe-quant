import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { customInstance, isApiError } from "@/api/client";

// Hand-written client for the Robinhood trading system (/api/robinhood) and the Research Lab
// (/api/lab), served by vibe_quant/robinhood/api.py.

type Json = Record<string, unknown>;

async function get<T>(url: string): Promise<T> {
  const res = await customInstance<{ data: T; status: number }>(url, { method: "GET" });
  return res.data;
}

async function send<T>(url: string, method: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method };
  if (body !== undefined) init.body = JSON.stringify(body);
  const res = await customInstance<{ data: T; status: number }>(url, init);
  return res.data;
}

export function errorText(error: unknown): string {
  if (isApiError(error)) {
    const detail = (error.body as { detail?: unknown } | undefined)?.detail;
    if (typeof detail === "string") return detail;
  }
  return error instanceof Error ? error.message : String(error);
}

// -- Types --------------------------------------------------------------------------------------

export interface ServiceState {
  installed: boolean;
  active?: string;
  sub?: string;
  since?: string | null;
  error?: string;
}

export interface Settings {
  approval_mode: "auto" | "manual";
  trading_mode: "paper" | "live";
}

export interface Command {
  id: number;
  created_at: string;
  command: string;
  args: Json;
  status: string;
  result_json?: string | null;
  finished_at?: string | null;
}

export interface BotStatus {
  updated_at: string;
  running: boolean;
  mode: string;
  approval_mode: string;
  today: string;
  trading_day: boolean;
  next_trading_day: string;
  in_session: boolean;
  schedule: Record<string, string>;
  busy: string[];
  researched_today: boolean;
  frozen_today: boolean;
  account?: { cash_free?: number; value?: number; cash_total?: number };
  positions?: Json[];
  age_secs?: number;
}

export interface StatusResponse {
  service: ServiceState;
  bot: BotStatus | null;
  bot_stale: boolean;
  settings: Settings;
  commands: Command[];
  week_new_positions: number;
  today: string;
}

export interface TradeRecord {
  status?: string;
  mode?: string;
  entry_price?: number | null;
  exit_price?: number | null;
  quantity?: number | null;
  return_pct?: number | null;
  pnl?: number | null;
  exit_reason?: string | null;
  opened_at?: string | null;
  closed_at?: string | null;
  [key: string]: unknown;
}

export interface Proposal {
  proposal_id: string;
  trading_date: string;
  proposed_at: string;
  status: string;
  ticker: string;
  thesis: string | null;
  thesis_source: string | null;
  contract: string | null;
  expiration: string | null;
  strike: number | null;
  option_type: string | null;
  dte: number | null;
  proposed_quantity: number | null;
  proposed_limit: number | null;
  maximum_debit: number | null;
  underlying_at_proposal: number | null;
  option_bid_at_proposal: number | null;
  option_ask_at_proposal: number | null;
  option_delta_at_proposal: number | null;
  selected_setup: string | null;
  combined_setup_score: number | null;
  social_score: number | null;
  entry_score: number | null;
  decision: string | null;
  fatal_flags: string[] | null;
  missing_fields: string[] | null;
  entry_eligible: boolean | null;
  trade_eligible: boolean | null;
  trade: TradeRecord | null;
  shadow: TradeRecord | null;
  ai_summary: string | null;
  lab: { strategy_id?: string; name?: string } | null;
  exit_rules: Json | null;
}

export interface Account {
  account: string;
  portfolio: { total_value: number; equity_value: number; cash: number; buying_power: number };
  positions: { symbol: string; quantity: number; average_price: number | null; last_price: number | null }[];
  option_positions: { symbol: string; quantity: number; average_price: number | null }[];
  orders: {
    id: string;
    kind: string;
    symbol: string;
    side: string;
    type: string;
    state: string;
    created_at: string;
    quantity: number | null;
    filled: number | null;
    price: number | null;
    average_price: number | null;
  }[];
  fetched_at: string;
}

export interface PaperAccount {
  exists: boolean;
  cash?: number | null;
  positions?: Json[];
  starting_balances?: string[] | null;
  created_at?: string | null;
  fills?: { ts: string; strategy: string; instrument_id: string; side: string; qty: string; px: string }[];
}

export interface Stats {
  count: number;
  wins: number;
  win_rate_pct: number | null;
  avg_return_pct: number | null;
  total_pnl: number;
}

export interface History {
  trades: Stats;
  shadows: Stats;
  proposals: number;
  rows: Proposal[];
}

export interface ProposalDetail {
  proposal: Json;
  events: { at: string; type: string; payload: Json }[];
  evidence: Json;
}

export interface BacktestSummary {
  run_id: string;
  finished_at: string;
  start: string | null;
  end: string | null;
  setups: string[];
  tickers: string[];
  all: Json | null;
  out_of_sample: Json | null;
  account: { start_cash?: number; end_cash?: number; return_pct?: number; max_drawdown_pct?: number; trades?: number };
}

// -- Bot ----------------------------------------------------------------------------------------

export function useBotStatus() {
  return useQuery({
    queryKey: ["rh", "status"],
    queryFn: () => get<StatusResponse>("/api/robinhood/status"),
    refetchInterval: 5_000,
  });
}

export function useAccount() {
  return useQuery({
    queryKey: ["rh", "account"],
    queryFn: () => get<Account>("/api/robinhood/account"),
    refetchInterval: 60_000,
    retry: 1,
  });
}

export function usePaper() {
  return useQuery({
    queryKey: ["rh", "paper"],
    queryFn: () => get<PaperAccount>("/api/robinhood/paper"),
    refetchInterval: 15_000,
  });
}

export function useBotLogs(unit: "bot" | "lab", enabled: boolean) {
  return useQuery({
    queryKey: ["rh", "logs", unit],
    queryFn: () => get<{ text: string }>(`/api/robinhood/logs?unit=${unit}&lines=400`),
    refetchInterval: 10_000,
    enabled,
  });
}

function useAction<TVars>(fn: (vars: TVars) => Promise<unknown>, success?: (vars: TVars) => string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (_data, vars) => {
      if (success) toast.success(success(vars));
      void client.invalidateQueries({ queryKey: ["rh"] });
      void client.invalidateQueries({ queryKey: ["lab"] });
    },
    onError: (error) => toast.error(errorText(error)),
  });
}

export function useBotAction() {
  return useAction(
    (action: "start" | "stop" | "restart") => send(`/api/robinhood/bot/${action}`, "POST"),
    (action) => `Bot ${action === "stop" ? "stopped" : action === "start" ? "started" : "restarted"}`,
  );
}

export function useSendCommand() {
  return useAction(
    (vars: { command: string; args?: Json }) =>
      send("/api/robinhood/command", "POST", { command: vars.command, args: vars.args ?? {} }),
    (vars) => `Sent "${vars.command.replace("_", " ")}" to the bot`,
  );
}

export function useUpdateSettings() {
  return useAction(
    (changes: Partial<Settings>) => send<Settings>("/api/robinhood/settings", "PUT", changes),
    (changes) =>
      changes.trading_mode
        ? `Switched to ${changes.trading_mode.toUpperCase()}`
        : `Approval: ${changes.approval_mode ?? ""}`,
  );
}

export function usePaperReset() {
  return useAction(
    (balance: number) => send("/api/robinhood/paper/reset", "POST", { balance }),
    (balance) => `Paper account reset to $${balance.toLocaleString()}`,
  );
}

// -- Proposals ----------------------------------------------------------------------------------

export function useProposals(date?: string) {
  return useQuery({
    queryKey: ["rh", "proposals", date ?? "today"],
    queryFn: () =>
      get<{ date: string; proposals: Proposal[] }>(
        `/api/robinhood/proposals${date ? `?date=${date}` : ""}`,
      ),
    refetchInterval: 15_000,
  });
}

export function useHistory() {
  return useQuery({
    queryKey: ["rh", "history"],
    queryFn: () => get<History>("/api/robinhood/history"),
    refetchInterval: 30_000,
  });
}

export function useProposalDetail(id: string | null) {
  return useQuery({
    queryKey: ["rh", "proposal", id],
    queryFn: () => get<ProposalDetail>(`/api/robinhood/proposal?id=${encodeURIComponent(id ?? "")}`),
    enabled: id !== null,
  });
}

export function useReports() {
  return useQuery({
    queryKey: ["rh", "reports"],
    queryFn: () => get<string[]>("/api/robinhood/reports"),
  });
}

export function useReport(date: string | null) {
  return useQuery({
    queryKey: ["rh", "report", date],
    queryFn: () => get<{ date: string; markdown: string }>(`/api/robinhood/reports/${date ?? ""}`),
    enabled: date !== null,
  });
}

// -- Options backtests -------------------------------------------------------------------------

export function useBacktests() {
  return useQuery({
    queryKey: ["rh", "backtests"],
    queryFn: () =>
      get<{ running: Json | null; runs: BacktestSummary[] }>("/api/robinhood/backtests"),
    refetchInterval: (q) => (q.state.data?.running ? 3_000 : 30_000),
  });
}

export function useBacktest(runId: string | null) {
  return useQuery({
    queryKey: ["rh", "backtest", runId],
    queryFn: () => get<Json>(`/api/robinhood/backtests/${runId ?? ""}`),
    enabled: runId !== null,
  });
}

export function useStartBacktest() {
  return useAction(
    (body: { tickers?: string; universe?: number; start?: string }) =>
      send<{ run_id: string }>("/api/robinhood/backtests", "POST", body),
    () => "Backtest started",
  );
}

// -- Research Lab --------------------------------------------------------------------------------

export interface LabStatus {
  status: {
    state?: string;
    current?: { id: number; title: string; stage: string } | null;
    last_experiment?: number;
    updated_at?: string;
    next_window?: string;
  };
  stop_requested: boolean;
  run_now: string | null;
  experiments: Record<string, number>;
  plays: number;
  knowledge: Record<string, { count: number; tested: number }>;
  pool: Json | null;
  groups: Record<string, { description: string; count: number }>;
  wsb: {
    first?: string | null;
    last?: string | null;
    days_covered?: number;
    days_total?: number;
    posts?: number;
    [key: string]: unknown;
  } | null;
  service: ServiceState;
  live: Record<string, { proposals: number; closed: number; profit_factor: number | null; avg_return_pct: number | null }>;
}

export interface ExperimentRow {
  id: number;
  created_at: string;
  finished_at: string | null;
  status: string;
  stage: string;
  source_kind: string;
  title: string;
  groups: string[];
  verdict: string | null;
  lesson: string | null;
  error: string | null;
  strategy: string | null;
  train_return_pct: number | null;
  train_trades: number | null;
  train_pf: number | null;
  holdout_pf: number | null;
  holdout_trades: number | null;
  holdout_account_pct: number | null;
  holdout_win_rate: number | null;
}

export interface LabStrategy {
  id: string;
  name: string;
  summary: string | null;
  created_at: string;
  experiment_id: number;
  status: string;
  groups: string[];
  score: number | null;
  promoted_at: string | null;
  retired_at: string | null;
  retire_reason: string | null;
  holdout: {
    profit_factor?: number;
    account?: { median_return_pct?: number };
    stats?: { trades?: number; win_rate_pct?: number };
    checks?: Record<string, boolean>;
  };
  live: { proposals: number; closed: number; profit_factor: number | null; avg_return_pct: number | null } | null;
}

export interface Knowledge {
  id: number;
  source: string;
  title: string;
  body: string;
  tags: string[];
  tested: number;
  created_at: string;
}

export function useLab() {
  return useQuery({
    queryKey: ["lab", "status"],
    queryFn: () => get<LabStatus>("/api/lab"),
    refetchInterval: 10_000,
  });
}

export function useExperiments() {
  return useQuery({
    queryKey: ["lab", "experiments"],
    queryFn: () => get<ExperimentRow[]>("/api/lab/experiments?limit=200"),
    refetchInterval: 20_000,
  });
}

export function useExperiment(id: number | null) {
  return useQuery({
    queryKey: ["lab", "experiment", id],
    queryFn: () => get<Json>(`/api/lab/experiments/${id ?? 0}`),
    enabled: id !== null,
  });
}

export function useLabStrategies() {
  return useQuery({
    queryKey: ["lab", "strategies"],
    queryFn: () => get<LabStrategy[]>("/api/lab/strategies"),
    refetchInterval: 30_000,
  });
}

export function useKnowledge() {
  return useQuery({
    queryKey: ["lab", "knowledge"],
    queryFn: () => get<Knowledge[]>("/api/lab/knowledge"),
  });
}

export function usePlays() {
  return useQuery({
    queryKey: ["lab", "plays"],
    queryFn: () => get<{ stats: Json; recent: Json[] }>("/api/lab/plays"),
  });
}

export function useGroups() {
  return useQuery({
    queryKey: ["lab", "groups"],
    queryFn: () =>
      get<Record<string, { description: string; members: string[] }>>("/api/lab/groups"),
  });
}

export function useLabAction() {
  return useAction(
    (action: "start" | "stop" | "run-now") => send(`/api/lab/control/${action}`, "POST"),
    (action) =>
      action === "run-now" ? "Lab will start an experiment now" : `Lab ${action === "stop" ? "stopped" : "started"}`,
  );
}

export function useStrategyAction() {
  return useAction(
    (vars: { id: string; action: "retire" | "activate" }) =>
      send(`/api/lab/strategies/${encodeURIComponent(vars.id)}/${vars.action}`, "POST"),
    (vars) => (vars.action === "retire" ? "Strategy retired" : "Strategy active: it will trade"),
  );
}

export function useAddKnowledge() {
  return useAction(
    (vars: { title: string; body: string }) => send("/api/lab/knowledge", "POST", vars),
    () => "Idea added: the Lab will test it",
  );
}

export function useDeleteKnowledge() {
  return useAction((id: number) => send(`/api/lab/knowledge/${id}`, "DELETE"), () => "Idea removed");
}

// -- Formatting -----------------------------------------------------------------------------------

export function usd(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
}

export function pct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}%`;
}

export function num(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
}

export function when(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
