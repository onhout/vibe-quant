import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import type { BotStatusResponse } from "@/api/generated/models";
import {
  getGetBotStatusApiOptionsBotStatusGetQueryKey,
  useControlBotServiceApiOptionsBotServiceActionPost,
  useSubmitBotCommandApiOptionsBotCommandsPost,
  useUpdateBotSettingsApiOptionsBotSettingsPut,
} from "@/api/generated/options-bot/options-bot";
import { formatApiErrorDetail } from "@/components/discovery/apiErrorDetail";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { cn } from "@/lib/utils";
import { asRecord, str, usd } from "./format";

function Segmented<T extends string>({
  value,
  options,
  onChange,
  disabled,
}: {
  value: T;
  options: { value: T; label: string; activeClass?: string }[];
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
            o.value === value
              ? (o.activeClass ?? "bg-primary text-primary-foreground")
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <p className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
        {label}
      </p>
      {children}
    </div>
  );
}

export function BotControl({ status }: { status: BotStatusResponse }) {
  const queryClient = useQueryClient();
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: getGetBotStatusApiOptionsBotStatusGetQueryKey() });
  const serviceMutation = useControlBotServiceApiOptionsBotServiceActionPost();
  const settingsMutation = useUpdateBotSettingsApiOptionsBotSettingsPut();
  const commandMutation = useSubmitBotCommandApiOptionsBotCommandsPost();

  const active = status.service.active ?? "inactive";
  const running = active === "active" || active === "activating";
  const live = status.settings.trading_mode === "live";
  const bot = status.bot ? asRecord(status.bot) : null;
  const schedule = asRecord(bot?.schedule);
  const account = asRecord(bot?.account);
  const busy = Array.isArray(bot?.busy) ? (bot.busy as unknown[]).map(String) : [];
  const state = running ? (status.bot_stale ? "starting" : "running") : active;

  const onService = (action: "start" | "stop") =>
    serviceMutation.mutate(
      { action },
      {
        onSuccess: () => {
          toast.success(action === "start" ? "Bot started" : "Bot stopped");
          void refresh();
        },
        onError: (err: unknown) =>
          toast.error(`Bot ${action} failed`, {
            description: formatApiErrorDetail(err) || "Request failed",
          }),
      },
    );

  const onSettings = (data: {
    trading_mode?: "paper" | "live";
    approval_mode?: "auto" | "manual";
  }) =>
    settingsMutation.mutate(
      { data },
      {
        onSuccess: () => {
          toast.success(
            data.trading_mode
              ? `Switched to ${data.trading_mode.toUpperCase()}${running ? " (bot restarting)" : ""}`
              : `Entries: ${data.approval_mode === "manual" ? "I approve" : "bot buys"}`,
          );
          void refresh();
        },
        onError: (err: unknown) =>
          toast.error("Settings not saved", {
            description: formatApiErrorDetail(err) || "Request failed",
          }),
      },
    );

  const onCommand = (command: "research_now" | "report") =>
    commandMutation.mutate(
      { data: { command } },
      {
        onSuccess: () => {
          toast.success(command === "report" ? "Report requested" : "Research requested");
          void refresh();
        },
        onError: (err: unknown) =>
          toast.error("Command refused", {
            description: formatApiErrorDetail(err) || "Request failed",
          }),
      },
    );

  return (
    <div className="space-y-4 rounded-xl border border-border/60 bg-card/40 p-5 backdrop-blur-sm">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold text-foreground">Bot</h2>
          <StatusBadge status={state} />
          {status.bot_age_secs != null && running && (
            <span className="text-[11px] text-muted-foreground">
              status {status.bot_age_secs}s old
            </span>
          )}
        </div>
        <div className="flex gap-2">
          {running ? (
            <Button
              size="sm"
              variant="outline"
              disabled={serviceMutation.isPending}
              onClick={() => onService("stop")}
            >
              Stop
            </Button>
          ) : (
            <Button
              size="sm"
              disabled={serviceMutation.isPending || !status.service.installed}
              onClick={() => onService("start")}
            >
              Start
            </Button>
          )}
        </div>
      </div>

      {!status.service.installed && (
        <p className="text-xs text-amber-400">
          The trading-paper service isn't installed: run tools/install_services.sh in the trading
          project.
        </p>
      )}

      <div className="flex flex-wrap items-end gap-x-8 gap-y-3">
        <Field label="Account">
          <Segmented
            value={status.settings.trading_mode}
            disabled={settingsMutation.isPending}
            onChange={(trading_mode) => onSettings({ trading_mode })}
            options={[
              { value: "paper", label: "Paper" },
              { value: "live", label: "Live", activeClass: "bg-red-600 text-white" },
            ]}
          />
        </Field>
        <Field label="Entries">
          <Segmented
            value={status.settings.approval_mode}
            disabled={settingsMutation.isPending}
            onChange={(approval_mode) => onSettings({ approval_mode })}
            options={[
              { value: "auto", label: "Bot buys" },
              { value: "manual", label: "I approve" },
            ]}
          />
        </Field>
        <Field label="This week">
          <p className="font-mono text-sm">
            {status.week_new_positions} / {status.max_new_positions_per_week ?? 1} new position
          </p>
        </Field>
        <Field label="Bot's account view">
          <p className="font-mono text-sm">
            {usd(typeof account?.value === "number" ? account.value : null)} value ·{" "}
            {usd(typeof account?.cash_free === "number" ? account.cash_free : null)} free
          </p>
        </Field>
      </div>

      {live && (
        <p className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
          LIVE: the bot sends real orders to the Alpaca live account (real money).
        </p>
      )}

      <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs sm:grid-cols-4">
        <span className="text-muted-foreground">Today (New York)</span>
        <span className="font-mono">
          {status.today}
          {bot && bot.trading_day === false ? " · market closed" : ""}
        </span>
        <span className="text-muted-foreground">Research</span>
        <span className="font-mono">
          {bot?.researched_today
            ? "done"
            : (str(schedule?.research) ?? "").slice(11, 16) || "08:30"}
        </span>
        <span className="text-muted-foreground">Proposals</span>
        <span className="font-mono">
          {bot?.frozen_today ? "posted" : (str(schedule?.freeze) ?? "").slice(11, 16) || "09:40"}
        </span>
        <span className="text-muted-foreground">Next trading day</span>
        <span className="font-mono">{str(bot?.next_trading_day) ?? "—"}</span>
        {busy.length > 0 && (
          <>
            <span className="text-muted-foreground">Working on</span>
            <span className="font-mono">{busy.join(", ")}</span>
          </>
        )}
      </div>

      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="outline"
          disabled={!running || commandMutation.isPending}
          onClick={() => onCommand("research_now")}
        >
          Research now
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={!running || commandMutation.isPending}
          onClick={() => onCommand("report")}
        >
          Write today's report
        </Button>
      </div>
    </div>
  );
}
