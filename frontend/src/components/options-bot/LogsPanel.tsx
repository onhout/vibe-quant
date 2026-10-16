import { useGetBotLogsApiOptionsBotLogsGet } from "@/api/generated/options-bot/options-bot";

export function LogsPanel() {
  const query = useGetBotLogsApiOptionsBotLogsGet(
    { lines: 400 },
    { query: { refetchInterval: 10_000 } },
  );
  const logs = query.data?.status === 200 ? query.data.data : null;
  return (
    <div className="space-y-2">
      <p className="text-xs text-muted-foreground">
        {logs ? `journalctl --user -u ${logs.unit}` : "Loading…"}
      </p>
      <pre className="max-h-[32rem] overflow-auto whitespace-pre-wrap rounded-md bg-muted/30 p-3 font-mono text-[11px] leading-relaxed">
        {logs?.text ?? ""}
      </pre>
    </div>
  );
}
